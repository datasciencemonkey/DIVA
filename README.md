# DIVA: Databricks Intelligent Voice Agents

**A blueprint for running real-time voice agents on Databricks.**

You open a browser, click call, and talk to an AI support agent. That's the whole demo. Underneath, the
agent's models run through **Unity Gateway**. Its answers come from **Lakebase Postgres** using
[**Lakebase Search**](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
(`lakebase_vector` ANN + `lakebase_text` BM25). Every call gets traced into **Unity Catalog / MLflow**, and the
whole thing ships as **one Databricks App**. **LiveKit** carries the audio; **Deepgram** handles speech-to-text
and text-to-speech.

The reference implementation is the **Unity Gateway Voice Studio**. It walks through Unity Gateway's 4 pillars
(Choice, Control, Context, Costs) using a LiveKit customer-support agent: loyalty tier picks the model, tools read
from Lakebase, and each fictional company gets its own generated dataset. Fork it, point it at your workspace,
and swap in your own data, prompts and tools.

The repo also ships an [Agent Skill](https://agentskills.io), **`building-voice-agents-on-databricks`**. Load it
into Cursor, Claude Code, or any Agent Skills loader and your coding agent picks up the patterns (and the traps)
without you having to explain them. More in **[Agent skill](#agent-skill)** below.

## What the blueprint covers

| Pillar | What you see | How it's built |
|---|---|---|
| **Choice** | Different callers get different models | The app maps the caller's loyalty tier (Standard / Premium / VIP) to a model through env vars; Unity Gateway serves it |
| **Control** | Governed, observable LLM calls | Every chat and embedding call goes through Unity Gateway; OpenTelemetry spans land in a Unity Catalog table and show up as MLflow traces |
| **Context** | Answers grounded in real data | Read-only tools over Lakebase: `semantic_search` ([**Lakebase Search**](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres) ANN, `lakebase_vector`) and `record_lookup` (only the caller's own records) |
| **Costs** | Spend you can watch | Token usage streams to the UI as the call goes, with a cost projection |

## How a call works

The numbers show the order things happen. Step 0 runs once per world, steps 1–4 set up the connection, and
steps 5–9 run on every call. The longer walkthrough lives in [`docs/architecture.md`](docs/architecture.md).

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

0. **Generate a world.** Make up a company, a role and a system prompt. The generator drafts documents,
   customers and records through Unity Gateway, embeds them, and writes everything to Lakebase in one
   transaction under a fresh `data_generation_id`. The indexes are
   [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres):
   `lakebase_ann` (cosine ANN via `lakebase_vector`) and `lakebase_bm25` (BM25 via `lakebase_text`).
1. **Load the studio.** The browser pulls the studio from the web tier and lists the worlds sitting in
   Lakebase.
2. **Start a call.** The web tier mints a short-lived LiveKit token (`GET /api/token`) carrying
   `data_generation_id` and `customer_id`, and dispatches the agent into a fresh room.
3. **Join the room.** The browser connects to LiveKit Cloud over WebRTC with that token.
4. **Dispatch the agent.** LiveKit hands the job to the agent worker, which stays registered as `ug-agent`
   over a WebSocket.
5. **Bind and route.** The worker looks up the caller's tier in Lakebase (never from what they say), and
   `route_for(tier)` picks the model. The model itself never sees the tier.
6. **Talk.** Deepgram STT, then the routed LLM through the gateway's Responses API, then Deepgram TTS.
7. **Ground.** `semantic_search` runs a
   [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
   ANN query (`ORDER BY embedding <=> $q` on the `lakebase_ann` index) after embedding the question through the
   gateway. `record_lookup` is plain SQL and only returns the caller's own records. Both tools stay scoped to
   the bound dataset.
8. **Show.** PII-free evidence (routing decision, retrieval hits, token usage) streams to the UI over the
   LiveKit data channel.
9. **Trace.** Spans flush over OTLP into a Unity Catalog table, and you read them as MLflow traces.

## Halloween mode (Plan 5)

Ask the agent for a spooky voice and it switches — and the whole studio switches with it. Tier, model and
governance stay exactly as they were.

- **AI Decide.** Databricks `ai_decide` (Beta) classifies each caller turn as enter, exit or none, outside the
  conversational LLM. A deterministic policy applies at most one change per turn, and an exit always wins.
  `UG_DECIDE_ENGINE=uaig_chat` swaps in a gateway-served chat model as the decider; `UG_AI_DECIDE=0` turns the
  feature off.
- **Expressive voice.** In Halloween mode the Databricks LLM writes inline cues such as `[whispers]`, and
  ElevenLabs (`eleven_v3_conversational`) performs them. Cues are never read aloud or shown. If ElevenLabs is
  unavailable the call carries on in a darker Deepgram voice (`aura-2-zeus-en`).
- **Themed UI.** The Control pillar shows each mode change live (mode, confidence, latency, path), and the whole
  studio cross-fades into an "All Hallows' Console" theme — moon, fog, embers, bats, display type, a
  jack-o'-lantern on the Control pillar — then cleanly back when the agent exits. Honors `prefers-reduced-motion`.

Settings live in `app.yaml` and are mirrored in `.env.example`: `UG_AI_DECIDE`, `UG_DECIDE_ENGINE`,
`UG_DECIDE_MODEL`, `UG_DECIDE_TIMEOUT_S`, `UG_DECIDE_CUE_WAIT_S`, `UG_HALLOWEEN_TTS`, `UG_HALLOWEEN_TTS_MODEL`,
`UG_HALLOWEEN_VOICE_ID`, `UG_HALLOWEEN_STABILITY`, `UG_HALLOWEEN_FALLBACK_VOICE`, plus the secret
`ELEVEN_API_KEY`. The design spec and plan are in
[`docs/superpowers/`](docs/superpowers/specs/2026-10-02-ug-voice-studio-plan-5-ai-decide-halloween-design.md).

Four things are the owner's to do before the ElevenLabs voice works on a deployed app; the repo can only
reference them:

1. **Enable `ai_decide` on the workspace** (admin, Previews). The agent's `DATABRICKS_TOKEN` must also be
   allowed to call the `ai-functions` API. Without `ai_decide` the default engine returns errors; explicit
   requests still work, or set `UG_DECIDE_ENGINE=uaig_chat`.
2. **Create the ElevenLabs key** as a secret in the `ug-voice-studio` scope and attach it to the app as the
   resource `elevenlabs-api-key`. `app.yaml` reads it with `valueFrom`; the value is never committed. Attach it
   before deploying, since `valueFrom` only resolves once the resource exists.
3. **Pick a voice and set `UG_HALLOWEEN_VOICE_ID`** (a commented placeholder in `app.yaml`). Until it is set,
   Halloween mode uses the Deepgram fallback voice.
4. **Confirm the app can reach `api.elevenlabs.io`.** Outbound egress to it has not been verified yet.

## What's inside

| Path | What it is |
|---|---|
| `app/` | The LiveKit agent worker (`agent.py`), its tools and tracing, the web tier (`web_server.py`) and the studio UI (`web/public/`) |
| `src/` | Routing policy, the governance prompt, the synthetic-world generator, and the Lakebase / gateway / retrieval services |
| `infra/` | The Lakebase schema, ANN and BM25 indexes, and `apply_schema.py` |
| `start_app.py`, `app.yaml` | The single-container Databricks App launcher and spec |
| `skills/building-voice-agents-on-databricks/` | The agent skill: deploy, worker + tools, observability ([what you can ask it](#agent-skill)) |
| `docs/discovery/` | Verified contracts for the platform behavior the code depends on |
| `docs/superpowers/` | The design spec and implementation plans |
| `tests/` | 82 tests; the one live integration test skips unless `LAKEBASE_ENDPOINT` is set |

## Agent skill

The skill lives at [`skills/building-voice-agents-on-databricks/`](skills/building-voice-agents-on-databricks/SKILL.md)
([install notes](skills/README.md)). Copy it into `~/.cursor/skills/` or `~/.claude/skills/`, or just leave it in
the repo and open the workspace. Then ask your agent things like:

| Ask it to… | What it does | Reference |
|---|---|---|
| **Deploy DIVA (or your fork) as a Databricks App** | Compiles worker deps for Apps Python 3.11, attaches secret resources, grants the app SP, runs `databricks sync --full`, then `apps deploy` | [deployment.md](skills/building-voice-agents-on-databricks/references/deployment.md) |
| **Package a LiveKit worker + browser token server in one container** | Uses the `start_app.py` / `app.yaml` templates: the web tier grabs the port right away, the worker boots in `/tmp/agent-venv` | [templates/](skills/building-voice-agents-on-databricks/templates/) |
| **Wire the LLM through Unity Gateway** | Builds `openai.responses.LLM` against `{host}/ai-gateway/openai/v1` with tools, once the participant identity is known | [agent-and-tools.md](skills/building-voice-agents-on-databricks/references/agent-and-tools.md) |
| **Add Lakebase-backed voice tools** | A `build_tools(pool, ctx)` factory that fails soft when `pool=None`, scopes lookups to the customer, and skips `from __future__ import annotations` | [agent-and-tools.md](skills/building-voice-agents-on-databricks/references/agent-and-tools.md) |
| **Mint LiveKit tokens with agent dispatch** | Stdlib HS256 JWT, a unique room per visit, `roomConfig.agents` matching the worker name | [agent-and-tools.md](skills/building-voice-agents-on-databricks/references/agent-and-tools.md) |
| **Trace every call into MLflow / Unity Catalog** | LiveKit OTel to Databricks OTLP `/api/2.0/otel/v1/traces` with a UC-table header; quietly skips if unset | [observability.md](skills/building-voice-agents-on-databricks/references/observability.md) |
| **Debug "the call connects but no agent joins"** | Checks py3.11 vs 3.12 numpy pins, Silero `download-files` egress, LiveKit Cloud outbound, SP folder grants | [SKILL.md](skills/building-voice-agents-on-databricks/SKILL.md) |
| **Fix `NameError: RunContext` on every tool turn** | Drops postponed annotations in the tools module so LiveKit's `get_type_hints()` can resolve `RunContext` | [agent-and-tools.md](skills/building-voice-agents-on-databricks/references/agent-and-tools.md) |

A few prompts to try:

```text
Deploy this voice agent as a Databricks App using the skill.
Add a Lakebase record_lookup tool the way the skill does.
The call connects but no agent joins. Follow the skill.
```

## Prerequisites

- A Databricks workspace with Unity Gateway (chat models for the tiers and for generation, plus an embedding
  model) and a Lakebase database with [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
  turned on (`lakebase_vector` + `lakebase_text`).
- A LiveKit project (LiveKit Cloud works fine) and a Deepgram API key.
- The Databricks CLI with a profile you pick (`--profile <name>`; nothing here picks one for you), and
  [`uv`](https://docs.astral.sh/uv/) (Python 3.12+).

## Quickstart (local)

```bash
uv sync
cp .env.example .env.local     # fill in LiveKit, Deepgram, Databricks, Lakebase and model settings;
                               # the agent worker and web tier load it automatically

# One-time Lakebase setup. These scripts read config from the environment, not .env.local.
export DATABRICKS_CONFIG_PROFILE=<your-profile>
export LAKEBASE_ENDPOINT=projects/<project>/branches/<branch>/endpoints/<endpoint>
export LAKEBASE_DATABASE=databricks_postgres UG_SCHEMA=ug
uv run python infra/apply_schema.py                  # schema + ANN/BM25 indexes
uv run python generate.py "Northwind Outfitters"     # optional: generate a world from the CLI

uv run python app/agent.py dev                       # agent worker
uv run python app/web_server.py                      # studio UI on http://localhost:8000 (PORT overrides)
uv run pytest -q
```

## Deploy as a Databricks App

The full walkthrough is in the skill at
[`skills/building-voice-agents-on-databricks/references/deployment.md`](skills/building-voice-agents-on-databricks/references/deployment.md),
or you can hand it to a coding agent with the skill loaded. The short version:

1. Compile the worker dependencies for the Apps runtime (Python 3.11):
   `uv pip compile agent-requirements.in --python-version 3.11 -o agent-requirements.txt`.
2. Put the 5 secrets (LiveKit URL, key and secret; Deepgram key; Databricks token) in a secret scope and attach
   them to the app as resources. `app.yaml` reads them through `valueFrom`.
3. Give the app's service principal `READ` on the scope and `CAN_MANAGE` on the workspace source folder.
4. Start the app, `databricks sync` a staging folder with `--full`, then run `databricks apps deploy`.
5. Tail `databricks apps logs`. You should see the web tier listening, then `registered worker` 30–60 seconds
   later.

## Configuration

For local runs, copy `.env.example` to `.env.local`. The deployed app reads the same settings from `app.yaml`.
Swap these placeholders for your own values:

| Placeholder | Replace with |
|---|---|
| `https://your-workspace.cloud.databricks.com` | your workspace URL |
| `projects/your-project/branches/production/endpoints/primary` | your Lakebase endpoint (`LAKEBASE_ENDPOINT`; database `databricks_postgres`) |
| `your_catalog`, `your_schema` | the catalog and schema for trace tables |
| `/Users/you@example.com/ugvoice` | your MLflow experiment path |
| `your-sql-warehouse-id` | the SQL warehouse that reads traces |

You'll see `ReferenceApp` / `<reference-app>` in a few comments and docs. That's an earlier LiveKit-on-Databricks
app this blueprint grew out of; it isn't in this repo.

## Further reading

If you're sharing DIVA with someone, these are the posts and docs worth sending along. The repo is
[datasciencemonkey/DIVA](https://github.com/datasciencemonkey/DIVA), and the skill is
[`skills/building-voice-agents-on-databricks`](skills/building-voice-agents-on-databricks/SKILL.md)
(it follows the [Agent Skills](https://agentskills.io) format).

**Unity Gateway**

- [Unity Gateway is Generally Available](https://www.databricks.com/blog/unity-ai-gateway-generally-available): cost, control and choice across agents, models, MCPs, skills and tools
- [AI governance at Data + AI Summit 2026: What's new with Unity Gateway](https://www.databricks.com/blog/ai-governance-data-ai-summit-2026-whats-new-unity-ai-gateway)
- [Unity Gateway: Governance Layer for Agentic AI](https://www.databricks.com/blog/ai-gateway-governance-layer-agentic-ai)
- [What's new in Unity Gateway: service policies, guardrails, observability, and cost controls](https://www.databricks.com/blog/whats-new-unity-ai-gateway-service-policies-guardrails-observability-and-cost-controls-ai)
- [Governing coding agent sprawl with Unity Gateway](https://www.databricks.com/blog/governing-coding-agent-sprawl-unity-ai-gateway): Cursor, Codex and Claude Code going through the same gateway DIVA uses
- Docs: [Unity Gateway](https://docs.databricks.com/aws/en/unity-gateway/) · [AI governance with Unity Gateway](https://docs.databricks.com/aws/en/ai-gateway/)

**Databricks Apps**

- [Announcing General Availability of Databricks Apps](https://www.databricks.com/blog/announcing-general-availability-databricks-apps)
- [How to Build Production-Ready Data and AI Apps with Databricks Apps and Lakebase](https://www.databricks.com/blog/how-build-production-ready-data-and-ai-apps-databricks-apps-and-lakebase)
- [Build Apps with Lakebase and Databricks Apps](https://www.databricks.com/blog/how-use-lakebase-transactional-data-layer-databricks-apps)
- Docs: [Databricks Apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/) · [Deploy a Databricks app](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)

**Lakebase and voice**

- [Lakebase Search: state-of-the-art full text and vector search for Postgres](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres): `lakebase_vector` (ANN) + `lakebase_text` (BM25). DIVA builds both indexes; the voice tool uses ANN.
- [LiveKit Agents](https://docs.livekit.io/agents/): the realtime framework the worker runs on
- [Deepgram](https://developers.deepgram.com/docs/getting-started): STT (`nova-3`) and TTS (`aura-2`)

**In this repo**

- [Architecture walkthrough](docs/architecture.md) · [Agent skill](#agent-skill) · [Design spec](docs/superpowers/specs/2026-09-24-unity-gateway-voice-studio-design.md) · [Plan (wave 1)](docs/superpowers/plans/2026-09-24-ug-voice-studio-plan-1-foundations.md)

One convention: run everything with `uv`, and always pass `--profile <name>` yourself. Nothing here picks a
Databricks profile for you.

## License

MIT. See [LICENSE](LICENSE).
