# DIVA: Databricks Intelligent Voice Agents

[![Agent Bricks](https://img.shields.io/badge/Agent_Bricks-custom_agent-FF3621?labelColor=1B3139&logo=databricks&logoColor=white)](https://www.databricks.com/blog/agent-bricks-dais-2026)
[![Databricks Apps](https://img.shields.io/badge/Databricks_Apps-hosted-FF3621?labelColor=1B3139&logo=databricks&logoColor=white)](https://www.databricks.com/product/databricks-apps)
[![Lakebase](https://img.shields.io/badge/Lakebase-Postgres_%2B_Search-FF3621?labelColor=1B3139&logo=postgresql&logoColor=white)](https://www.databricks.com/product/lakebase)
[![Unity Gateway](https://img.shields.io/badge/Unity_Gateway-LLMs_%2B_embeddings-FF3621?labelColor=1B3139&logo=databricks&logoColor=white)](https://www.databricks.com/blog/unity-ai-gateway-generally-available)
[![MLflow traces](https://img.shields.io/badge/MLflow-traces-FF3621?labelColor=1B3139&logo=mlflow&logoColor=white)](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/trace-unity-catalog)
[![AI Decide](https://img.shields.io/badge/AI_Decide-ai__decide-FF3621?labelColor=1B3139&logo=databricks&logoColor=white)](https://www.databricks.com/blog/introducing-aidecide-make-fast-decisions-your-governed-data)

[![LiveKit Agents](https://img.shields.io/badge/LiveKit-Agents-1B3139?logo=livekit&logoColor=white)](https://docs.livekit.io/agents/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Built with uv](https://img.shields.io/badge/built_with-uv-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![License: MIT](https://img.shields.io/badge/license-MIT-2EA44F)](LICENSE)

**A blueprint for real-time voice agents on Databricks.** You open a browser, click call, and talk to an AI support
agent. Apart from the audio pipe, every layer runs on Databricks: the agent is a custom agent hosted on Databricks
Apps, every chat and embedding call goes through Unity Gateway, its answers come from Lakebase, and every call lands in
MLflow as a trace.

## Built on Databricks

| | On Databricks | What it does here |
|:-:|---|---|
| ✅ | **[Custom agent on Agent Bricks](https://www.databricks.com/blog/agent-bricks-dais-2026)** | The voice agent is your own code, a LiveKit Agents worker. Agent Bricks supports custom agents built with any framework and hosts them on Databricks Apps. |
| ✅ | **[Databricks Apps](https://www.databricks.com/product/databricks-apps)** | The web tier and the agent run as one app in one container, with one deploy. |
| ✅ | **[Lakebase](https://www.databricks.com/product/lakebase) + [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)** | Postgres holds every world (documents, customers, records). ANN vector search grounds each answer in it. |
| ✅ | **[Unity Gateway](https://www.databricks.com/blog/unity-ai-gateway-generally-available)** | Serves every chat and embedding call, so it powers all the LLMs. The caller's loyalty tier picks the model, and token usage and an indicative cost show up live. |
| ✅ | **[MLflow traces](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/trace-unity-catalog)** | Each call is traced over OpenTelemetry into Unity Catalog and read as an MLflow trace. |
| ✅ | **[AI Decide](https://www.databricks.com/blog/introducing-aidecide-make-fast-decisions-your-governed-data)** (`ai_decide`) | Makes a fast decision on every turn, beside the LLM, about whether to change the experience mid-call. Say "switch to the spooky voice" and the whole studio follows. |

The reference implementation is the **Unity Gateway Voice Studio**, a customer-support agent where each fictional
company gets its own generated dataset. On a call the studio shows Unity Gateway's four pillars live: **Choice** (which
model this caller got), **Control** (the governance guarantees and the voice mode), **Context** (what Lakebase
retrieved) and **Costs** (token usage and an indicative cost). LiveKit carries the audio, Deepgram does speech-to-text
and the default voice, and ElevenLabs is the optional Halloween voice. Fork it, point it at your workspace, and swap in
your own data, prompts and tools.

The repo also ships an [Agent Skill](https://agentskills.io), `building-voice-agents-on-databricks`, so your coding
agent already knows the patterns and the traps. See [Agent skill](#agent-skill).

## How it works

```mermaid
flowchart LR
    B["Browser<br/>studio UI"]

    subgraph EXT["Voice infrastructure"]
        LK["LiveKit Cloud<br/>WebRTC + agent dispatch"]
        DG["Deepgram<br/>speech-to-text, default voice"]
        EL["ElevenLabs<br/>Halloween voice (optional)"]
    end

    subgraph APP["Databricks App · one container"]
        W["Web tier<br/>studio, tokens,<br/>world generator"]
        A["Custom voice agent<br/>LiveKit Agents worker"]
    end

    subgraph WS["Your Databricks workspace"]
        UG["Unity Gateway<br/>every chat + embedding call"]
        LB[("Lakebase<br/>+ Lakebase Search")]
        AD["AI Decide<br/>per-turn voice-mode decision"]
        ML[("Unity Catalog + MLflow<br/>call traces")]
    end

    B -->|"token"| W
    B <-->|"WebRTC audio"| LK
    LK -->|"dispatch"| A
    A <-->|"STT + TTS"| DG
    A -.-> EL
    A -->|"chat + embeddings"| UG
    A -->|"tools"| LB
    A -.->|"each turn"| AD
    A -->|"OTLP spans"| ML
    W -->|"generate a world"| UG
    W -->|"write"| LB

    classDef dbx fill:#FF3621,stroke:#B22416,color:#ffffff
    classDef ext fill:#F3F4F6,stroke:#9CA3AF,color:#111827
    class W,A,UG,LB,AD,ML dbx
    class B,LK,DG,EL ext
    style APP fill:#FFF1EE,stroke:#FF3621
    style WS fill:#FFF1EE,stroke:#FF3621
    style EXT fill:#FAFAFA,stroke:#D1D5DB
```

The red boxes run on Databricks. A call goes like this:

1. **Join.** The web tier mints a short-lived LiveKit token, the browser joins a room on LiveKit Cloud, and LiveKit
   dispatches the agent into it.
2. **Bind.** The agent reads the caller's tier from Lakebase (never from what they say) and picks the model for it. The
   model itself never sees the tier.
3. **Talk.** Deepgram turns speech into text, the routed model answers through Unity Gateway, and a voice speaks the
   reply.
4. **Ground.** `semantic_search` runs a Lakebase Search ANN query over the caller's dataset, after embedding the
   question through the gateway. `record_lookup` reads only the caller's own records.
5. **Decide.** On every turn AI Decide runs beside the model and may switch the voice mode. A deterministic policy
   applies the change, and the model, the tier and the governance rules stay as they were.
6. **Show and trace.** Evidence (routing decision, retrieval hits, token usage, voice mode) streams to the UI over the
   LiveKit data channel. Spans flush over OTLP into Unity Catalog, where they read as MLflow traces.

Before any call the studio can generate a world: a fictional company with documents, customers and records. Unity
Gateway drafts them and embeds the documents, and everything is written to Lakebase in one transaction. The full
walkthrough is in
[`docs/architecture.md`](docs/architecture.md), and the running app serves an interactive version at
`/architecture.html`.

## Halloween mode

Say "switch to the spooky voice" and the whole studio changes with it. **[AI Decide](https://www.databricks.com/blog/introducing-aidecide-make-fast-decisions-your-governed-data)**
classifies each turn outside the LLM, the agent switches to an ElevenLabs monster voice that performs audio cues such as
`[whispers]` and `[laughs]`, and the UI cross-fades into an "All Hallows' Console" theme. The tier, the model and the
governance rules don't change. It's optional: without ElevenLabs the call uses a darker Deepgram voice, and
`UG_AI_DECIDE=0` turns it off. Setup, settings, the cue palette and how to verify the voice are in
[`docs/halloween-voice.md`](docs/halloween-voice.md).

## Prerequisites

| You need | For |
|---|---|
| A Databricks workspace with **Unity Gateway** (chat models for the tiers and for generation, plus an embedding model) | Every chat and embedding call |
| A **Lakebase** database with [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres) turned on (`lakebase_vector` + `lakebase_text`) | The data and retrieval |
| A **LiveKit** project (LiveKit Cloud is fine) and a **Deepgram** API key | Audio transport, speech-to-text, the default voice |
| A Databricks personal access token for that workspace, the Databricks CLI, and [`uv`](https://docs.astral.sh/uv/) (Python 3.12+) | The agent and web tier authenticate with the token. The CLI and the setup scripts use a profile you pick, and nothing here picks one for you: pass `--profile <name>` to CLI commands and set `DATABRICKS_CONFIG_PROFILE` for the scripts. |
| *Optional:* a Unity Catalog catalog and schema for trace tables, plus an MLflow experiment and a SQL warehouse to browse them | Traces. Tracing turns on only when `DATABRICKS_TRACE_CATALOG`, `_SCHEMA` and `_TABLE_PREFIX` are all set. |
| *Optional:* `ai_decide` enabled on the workspace (a Previews feature, turned on by an admin), and an ElevenLabs key | Halloween mode |

## Quickstart (local)

```bash
uv sync
cp .env.example .env.local     # fill in LiveKit, Deepgram, Databricks (host + token), Lakebase and model settings

# One-time Lakebase setup. These two scripts read the shell environment, not .env.local.
export DATABRICKS_CONFIG_PROFILE=<your-profile>      # or DATABRICKS_HOST + DATABRICKS_TOKEN
export LAKEBASE_ENDPOINT=projects/<project>/branches/<branch>/endpoints/<endpoint>
export LAKEBASE_DATABASE=databricks_postgres UG_SCHEMA=ug
export UG_GEN_MODEL=system.ai.gpt-5-4 UG_EMBED_MODEL=system.ai.gte-large-en   # the models from .env.local
uv run python infra/apply_schema.py                  # schema + ANN/BM25 indexes
uv run python generate.py "Northwind Outfitters"     # optional: generate a world from the CLI

uv run python app/agent.py dev                       # agent worker
uv run python app/web_server.py                      # studio UI on http://localhost:8000 (PORT overrides)
uv run pytest -q --ignore=tests/test_integration_data_plane.py   # that one test is live and writes two worlds
```

The agent worker reads `.env.local` itself. The web tier reads it too, but only after it has fixed `UG_SCHEMA` and
`UG_EMBED_MODEL`, so export those two in its shell if you changed them.

Open the studio, generate a world (or pick a ready one), enter the call, choose a caller and press **Connect & talk**.
The studio suggests what to ask. Try an order question (Context), a Standard caller against a VIP one (Choice), then
"switch to the spooky voice" (AI Decide).

## Deploy as a Databricks App

The full walkthrough is in the skill at
[`skills/building-voice-agents-on-databricks/references/deployment.md`](skills/building-voice-agents-on-databricks/references/deployment.md),
or you can hand it to a coding agent with the skill loaded. The short version:

1. Compile the worker dependencies for the Apps runtime (Python 3.11):
   `uv pip compile agent-requirements.in --python-version 3.11 -o agent-requirements.txt`.
2. Put the 6 secrets (LiveKit URL, key and secret; Deepgram key; Databricks token; ElevenLabs key) in a secret scope and
   attach each to the app as a resource. `app.yaml` reads them through `valueFrom`, which only resolves once the
   resource exists, so attach all six before the first deploy ([details](docs/halloween-voice.md#turn-it-on)).
3. Give the app's service principal `READ` on the scope and `CAN_MANAGE` on the workspace source folder.
4. Start the app, `databricks sync` a staging folder with `--full`, then run `databricks apps deploy`.
5. Tail `databricks apps logs`. You should see the web tier listening, then `registered worker` 30–60 seconds later.

Stuck? [`docs/gotchas.md`](docs/gotchas.md) lists the traps we hit (LiveKit, the Databricks platform, deploys, tracing)
and the fix for each.

## Make it yours

For local runs, copy `.env.example` to `.env.local`. The deployed app reads the same settings from `app.yaml`. Swap
these placeholders for your own values:

| Placeholder | Replace with |
|---|---|
| `https://your-workspace.cloud.databricks.com` | your workspace URL |
| `projects/your-project/branches/production/endpoints/primary` | your Lakebase endpoint (`LAKEBASE_ENDPOINT`; database `databricks_postgres`) |
| `your_catalog`, `your_schema` | the catalog and schema for trace tables (`DATABRICKS_TRACE_CATALOG`, `DATABRICKS_TRACE_SCHEMA`) |
| `/Users/you@example.com/ugvoice` | your MLflow experiment path (only for reading traces back; export doesn't use it) |
| `your-sql-warehouse-id` | the SQL warehouse that reads traces (same) |

You'll see `ReferenceApp` / `<reference-app>` in a few comments and docs. That's an earlier LiveKit-on-Databricks app
this blueprint grew out of; it isn't in this repo.

## Agent skill

The repo ships [`skills/building-voice-agents-on-databricks/`](skills/building-voice-agents-on-databricks/SKILL.md), an
[Agent Skills](https://agentskills.io) bundle with what it took to get voice agents running on Databricks: the deploy
recipe, the worker and its Lakebase tools, Unity Gateway wiring, MLflow tracing, and the traps that cost the most time.
Copy it into `~/.cursor/skills/` or `~/.claude/skills/`, or leave it in the repo and open the workspace
([install notes](skills/README.md)). Then try:

```text
Deploy this voice agent as a Databricks App using the skill.
Add a Lakebase record_lookup tool the way the skill does.
The call connects but no agent joins. Follow the skill.
```

## What's inside

| Path | What it is |
|---|---|
| `app/` | The LiveKit agent worker (`agent.py`), its tools and tracing, the web tier (`web_server.py`) and the studio UI (`web/public/`) |
| `src/` | Routing policy, the governance prompt, the synthetic-world generator, and the Lakebase / gateway / retrieval services |
| `infra/` | The Lakebase schema, ANN and BM25 indexes, and `apply_schema.py` |
| `start_app.py`, `app.yaml` | The single-container Databricks App launcher and spec |
| `skills/` | The agent skill |
| `tools/` | Manual voice checks: the TTS→STT cue bake-off and the live LLM cue check (see [Halloween mode](docs/halloween-voice.md#verify-the-voice)) |
| `docs/` | Architecture, Halloween mode, [gotchas](docs/gotchas.md), verified platform contracts and design specs ([index](docs/README.md)) |
| `tests/` | Over 1,000 tests. Leave out `test_integration_data_plane.py` unless you mean to run it: it is live and writes two worlds to Lakebase |

## Further reading

The repo is [datasciencemonkey/diva](https://github.com/datasciencemonkey/diva). If you're sharing it with someone, these
are the posts and docs worth sending along.

- **Agent Bricks and Databricks Apps:** [Agent Bricks at Data + AI Summit 2026](https://www.databricks.com/blog/agent-bricks-dais-2026) ·
  [Databricks Apps is Generally Available](https://www.databricks.com/blog/announcing-general-availability-databricks-apps) ·
  [Production-ready data and AI apps with Databricks Apps and Lakebase](https://www.databricks.com/blog/how-build-production-ready-data-and-ai-apps-databricks-apps-and-lakebase) ·
  Docs: [Databricks Apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/) · [Deploy a Databricks app](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy) · [Use agents on Databricks](https://docs.databricks.com/aws/en/agents/custom-agents/build-agents)
- **Unity Gateway:** [Generally Available](https://www.databricks.com/blog/unity-ai-gateway-generally-available) ·
  [What's new at Data + AI Summit 2026](https://www.databricks.com/blog/ai-governance-data-ai-summit-2026-whats-new-unity-ai-gateway) ·
  [Service policies, guardrails, observability and cost controls](https://www.databricks.com/blog/whats-new-unity-ai-gateway-service-policies-guardrails-observability-and-cost-controls-ai) ·
  [Governance layer for agentic AI](https://www.databricks.com/blog/ai-gateway-governance-layer-agentic-ai) ·
  Docs: [Unity Gateway](https://docs.databricks.com/aws/en/unity-gateway/) · [AI governance with Unity Gateway](https://docs.databricks.com/aws/en/ai-gateway/)
- **Lakebase:** [Lakebase Search: full text and vector search for Postgres](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
  (`lakebase_vector` ANN + `lakebase_text` BM25; DIVA builds both and the voice tool uses ANN) ·
  [Build apps with Lakebase and Databricks Apps](https://www.databricks.com/blog/how-use-lakebase-transactional-data-layer-databricks-apps) ·
  Docs: [Lakebase Postgres](https://docs.databricks.com/aws/en/oltp/)
- **MLflow traces:** Docs: [Store OpenTelemetry traces in Unity Catalog](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/trace-unity-catalog) ·
  [Tracing overview](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/overview)
- **AI Decide:** [Introducing ai_decide: make fast decisions on your governed data](https://www.databricks.com/blog/introducing-aidecide-make-fast-decisions-your-governed-data) ·
  Docs: [`ai_decide` SQL function](https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_decide) · [REST API](https://docs.databricks.com/api/ai-functions/v1/ai-decide)
- **The voice stack:** [LiveKit Agents](https://docs.livekit.io/agents/) (the realtime framework the worker runs on) ·
  [Deepgram](https://developers.deepgram.com/docs/models-languages-overview) (STT `nova-3`, TTS `aura-2`)

## License

MIT. See [LICENSE](LICENSE).
