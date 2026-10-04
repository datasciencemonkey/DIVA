# Architecture: how DIVA works end to end

DIVA is a blueprint for running real-time voice agents on Databricks. This page shows every moving part of the
reference implementation, the Unity Gateway Voice Studio, and the order in which they talk to each other.

Everything runs in one of four places:

| Where | What runs there |
|---|---|
| **The caller's browser** | The studio single-page app and `livekit-client` |
| **External SaaS** | LiveKit Cloud (WebRTC media and agent dispatch) and Deepgram (speech-to-text and text-to-speech) |
| **One Databricks App** | `start_app.py` boots both tiers in one container: the web / token tier (`app/web_server.py`) and the agent worker (`app/agent.py`) |
| **Your Databricks workspace** | Unity Gateway (chat and embeddings), Lakebase Postgres with [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres) (the worlds), Unity Catalog and MLflow (traces) |

## The diagram

Numbers mark the order things happen: step 0 runs once per world, steps 1–4 establish the connection, and 5–9 run on every call.

```text
                              +- Databricks App (1 container) -+
+------------------+          | +----------------------------+ |
| Browser          |--[1][2]->| | Web / token tier           | |
| studio UI        |<---JWT---| | web_server.py              | |
| livekit-client   |          | | GET /   GET /api/token     | |
+------------------+          | +----------------------------+ |
    | [3]     ^ [8]           |                                |
    v         |               |                                |            Databricks workspace
+------------------+          | +----------------------------+ |          +----------------------+
| LiveKit Cloud    |--[4]---->| | Agent worker               | |-[6][7]-->| Unity Gateway        |
| WebRTC SFU       |<=[6][8]=>| | agent.py (LiveKit Agents)  | |          | /responses (LLM)     |
| + agent dispatch |          | |                            | |          | /embeddings          |
+------------------+          | | [5] bind: tier -> model    | |          +----------------------+
                              | | [6] STT -> LLM -> TTS      | |
                              | | [7] tools: semantic_search | |          +----------------------+
                              | |            record_lookup   | |--[5]---->| Lakebase Postgres    |
                              | | [8] publish evidence       | |--[7]---->| customers, records   |
                              | | [9] flush OTLP spans       | |          | Lakebase Search ANN  |
+------------------+          | |                            | |          +----------------------+
| Deepgram         |<===[6]==>| |                            | |
| STT nova-3       |          | |                            | |          +----------------------+
| TTS aura-2       |          | |                            | |--[9]---->| Unity Catalog table  |
+------------------+          | +----------------------------+ |          | -> MLflow traces     |
                              | start_app.py boots both tiers  |          +----------------------+
                              +--------------------------------+

[0] Before any call - generate a world (studio "Generate" step):

    Browser --POST /api/generate--> Web tier --draft + embed--> Unity Gateway
                                       |
                                       +--write in 1 txn (data_generation_id)--> Lakebase Postgres
```

## Step by step

### Before any call

0. **Generate a world.** Once per company, the studio's Generate step posts to `/api/generate` (or run
   `uv run python generate.py "<company>"`). `src/generate.py` drafts documents, customers across the Standard
   / Premium / VIP tiers, and order and case records with a JSON-mode chat call through Unity Gateway,
   embeds the documents with the gateway's embedding model, and writes it all to Lakebase in one transaction
   under a fresh `data_generation_id`. The documents are indexed for
   [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres):
   `lakebase_ann` (cosine ANN via `lakebase_vector`) and `lakebase_bm25` (BM25 via `lakebase_text`).

### Establishing the connection

1. **Load the studio.** The browser loads the studio from the web tier (`GET /`) and lists the worlds stored
   in Lakebase (`GET /api/datasets`).
2. **Start a call.** `GET /api/token?dataset=…&customer=…&name=…` returns a signed HS256 LiveKit JWT. It lives
   15 minutes, names a fresh room, carries `{data_generation_id, customer_id}` as metadata, and dispatches the
   agent named by `AGENT_NAME` (default `ug-agent`). The caller's name is courtesy-only and sanitized.
3. **Join the room.** The browser connects to LiveKit Cloud over WebRTC with that token.
4. **Dispatch the agent.** LiveKit hands the job to the agent worker (`app/agent.py`), which keeps a WebSocket
   open to LiveKit and is registered as `ug-agent`.

### On every call

5. **Bind and route.** The worker waits for the participant, reads the token metadata, and looks up the
   caller's loyalty tier in Lakebase (`src/services/session_bind.py`, `src/services/loyalty_context.py`).
   `route_for(tier)` (`src/policy/routing.py`) picks the model from the `UG_MODEL_*` map. The model never sees
   the tier, and the session is only built once the model is known.
6. **Talk.** Audio flows browser ⇄ LiveKit ⇄ worker. Deepgram STT (nova-3) turns speech into text, the routed
   LLM answers through Unity Gateway's OpenAI-compatible `/responses` API, and Deepgram TTS (aura-2) speaks
   the reply.
7. **Ground.** The LLM can call two read-only tools (`app/tools.py`, `src/services/retrieval.py`).
   `semantic_search` embeds the question through the gateway's `/embeddings` and runs
   [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
   ANN (`ORDER BY embedding <=> $q` on the `lakebase_ann` index) over the world's documents; `record_lookup`
   is plain SQL that returns only the bound caller's records and abstains for unknown callers. Both are scoped
   to the bound `data_generation_id`.
8. **Show.** The worker publishes PII-free evidence packets (the routing decision, retrieval hits, cumulative
   token usage) on the LiveKit data channel, and the studio renders them as the Choice / Control / Context /
   Costs pillars.
9. **Trace.** When the session ends, `app/tracing.py` enriches the OpenTelemetry spans (`ug.*` attributes,
   MLflow span types) and flushes them over OTLP into a Unity Catalog table, where they read as MLflow traces.
   Tracing is fail-soft: with no trace table configured, calls still work.

## Check it's working locally

Start both tiers (see the [README quickstart](../README.md#quickstart-local)), for example with the web tier on
port 9090:

```bash
PORT=9090 uv run python app/web_server.py     # logs: listening on 0.0.0.0:9090 (agent: "ug-agent")
uv run python app/agent.py dev                # logs: registered worker
```

Then walk the connection steps:

| Step | Check | Healthy result |
|---|---|---|
| 1 | `curl -s -o /dev/null -w '%{http_code}' http://localhost:9090/` | `200` |
| 1 | `curl -s http://localhost:9090/api/datasets` | the worlds in Lakebase, each `ready` |
| 2 | `curl -s 'http://localhost:9090/api/token?dataset=<data_generation_id>&name=Test'` | `token`, `serverUrl` and `roomName`; the JWT dispatches `ug-agent` |
| 4 | the agent worker's log | `registered worker` |
| 5–9 | open http://localhost:9090, pick a world and a caller, and start a call | the agent greets you, answers from the world's data, and the four pillars fill in |
