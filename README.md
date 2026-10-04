# DIVA — Databricks Intelligent Voice Agents

**A blueprint for running real-time voice agents on Databricks.**

DIVA is a working, end-to-end blueprint. A caller talks to an AI support agent from the browser. The agent's
models are governed and served through **Unity AI Gateway**, its answers come from data in **Lakebase
Postgres** via [**Lakebase Search**](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
(`lakebase_vector` ANN + `lakebase_text` BM25), every call is traced to **Unity Catalog / MLflow**, and everything ships as **one Databricks App**.
Voice transport is **LiveKit**; speech-to-text and text-to-speech are **Deepgram**.

The reference implementation is the **Unity Gateway Voice Studio**: a governed voice-agent studio presenting
**Unity AI Gateway** — Choice / Control / Context / Costs — through a LiveKit customer-support agent with
loyalty→model routing, generic Lakebase-scoped tools, and generated per-company datasets. Fork it, point it at
your workspace, and swap in your own data, prompts and tools.

This repo also ships the **`building-voice-agents-on-databricks`** [Agent Skill](https://agentskills.io) so a
coding agent (Cursor, Claude Code, or anything that loads [Agent Skills](https://agentskills.io)) can reuse the
patterns without re-learning the traps. See **[Agent skill](#agent-skill)** below.

## What the blueprint covers

| Pillar | What you see | How it's built |
|---|---|---|
| **Choice** | Different callers get different models | App-side routing: the caller's loyalty tier (Standard / Premium / VIP) maps to a model through env vars; Unity AI Gateway serves it |
| **Control** | Governed, observable LLM calls | Every chat and embedding call goes through Unity AI Gateway; OpenTelemetry spans land in a Unity Catalog table and render as MLflow traces |
| **Context** | Answers grounded in real data | Read-only tools over Lakebase: `semantic_search` ([**Lakebase Search**](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres) ANN, `lakebase_vector`) and `record_lookup` (the caller's own records) |
| **Costs** | Spend you can watch | Cumulative token usage streamed to the UI with a cost projection |

## How a call works

Numbers mark the order things happen: step 0 runs once per world, steps 1–4 establish the connection, and 5–9
run on every call. The full walkthrough is in [`docs/architecture.md`](docs/architecture.md).

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
| LiveKit Cloud    |--[4]---->| | Agent worker               | |-[6][7]-->| Unity AI Gateway     |
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

    Browser --POST /api/generate--> Web tier --draft + embed--> Unity AI Gateway
                                       |
                                       +--write in 1 txn (data_generation_id)--> Lakebase Postgres
```

0. **Generate a world.** Name a fictional company, a role and a system prompt. The generator drafts documents,
   customers and records through Unity AI Gateway, embeds them, and writes them to Lakebase in one transaction
   under a fresh `data_generation_id`. Indexes are
   [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres):
   `lakebase_ann` (cosine ANN via `lakebase_vector`) and `lakebase_bm25` (BM25 via `lakebase_text`).
1. **Load the studio.** The browser fetches the studio from the web tier and lists the worlds stored in
   Lakebase.
2. **Start a call.** The web tier mints a short-lived LiveKit token (`GET /api/token`) that carries
   `data_generation_id` and `customer_id`, and dispatches the agent into a fresh room.
3. **Join the room.** The browser connects to LiveKit Cloud over WebRTC with that token.
4. **Dispatch the agent.** LiveKit hands the job to the agent worker, which stays registered as `ug-agent`
   over a WebSocket.
5. **Bind and route.** The agent worker looks up the caller's tier in Lakebase (never from speech), and
   `route_for(tier)` picks the model. The model never sees the tier.
6. **Talk.** Deepgram STT → the routed LLM through the gateway's Responses API → Deepgram TTS.
7. **Ground.** `semantic_search` is
   [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
   ANN (`ORDER BY embedding <=> $q` on the `lakebase_ann` index), with the question embedded through the
   gateway first; `record_lookup` is plain SQL and only returns the caller's own records. Both tools are
   scoped to the bound dataset.
8. **Show.** PII-free evidence (routing decision, retrieval hits, token usage) streams to the UI over the
   LiveKit data channel.
9. **Trace.** The spans flush over OTLP into a Unity Catalog table, where they read as MLflow traces.

## What's inside

| Path | What it is |
|---|---|
| `app/` | The LiveKit agent worker (`agent.py`), its tools and tracing, the web tier (`web_server.py`) and the studio UI (`web/public/`) |
| `src/` | Routing policy, the governance prompt, the synthetic-world generator, and the Lakebase / gateway / retrieval services |
| `infra/` | The Lakebase schema, ANN and BM25 indexes, and `apply_schema.py` |
| `start_app.py`, `app.yaml` | The single-container Databricks App launcher and spec |
| `skills/building-voice-agents-on-databricks/` | Agent skill: deploy, worker + tools, observability — [what you can ask it to do](#agent-skill) |
| `docs/discovery/` | Verified contracts for the platform behavior the code relies on |
| `docs/superpowers/` | The design spec and implementation plans |
| `tests/` | 82 tests; the one live integration test is skipped unless `LAKEBASE_ENDPOINT` is set |

## Agent skill

Shipped at [`skills/building-voice-agents-on-databricks/`](skills/building-voice-agents-on-databricks/SKILL.md)
([install notes](skills/README.md)). Point Cursor, Claude Code, or any Agent Skills loader at that folder
(copy it to `~/.cursor/skills/` or `~/.claude/skills/`, or leave it in the repo). Then ask the agent to:

| Ask it to… | It will… | Reference |
|---|---|---|
| **Deploy DIVA (or your fork) as a Databricks App** | Compile worker deps for Apps Python 3.11, attach secret resources, grant the app SP, `databricks sync --full`, then `apps deploy` | [deployment.md](skills/building-voice-agents-on-databricks/references/deployment.md) |
| **Package a LiveKit worker + browser token server in one container** | Use the `start_app.py` / `app.yaml` templates: web binds the port immediately; the worker boots in `/tmp/agent-venv` | [templates/](skills/building-voice-agents-on-databricks/templates/) |
| **Wire the LLM through Unity AI Gateway** | Build `openai.responses.LLM` against `{host}/ai-gateway/openai/v1` with tools, after the participant identity is known | [agent-and-tools.md](skills/building-voice-agents-on-databricks/references/agent-and-tools.md) |
| **Add Lakebase-backed voice tools** | Factory `build_tools(pool, ctx)` with fail-soft `pool=None`, customer-scoped lookups, no `from __future__ import annotations` | [agent-and-tools.md](skills/building-voice-agents-on-databricks/references/agent-and-tools.md) |
| **Mint LiveKit tokens with agent dispatch** | Stdlib HS256 JWT, unique room per visit, `roomConfig.agents` matching the worker name | [agent-and-tools.md](skills/building-voice-agents-on-databricks/references/agent-and-tools.md) |
| **Trace every call into MLflow / Unity Catalog** | LiveKit OTel → Databricks OTLP `/api/2.0/otel/v1/traces` with a UC-table header; fail-soft if unset | [observability.md](skills/building-voice-agents-on-databricks/references/observability.md) |
| **Debug "the call connects but no agent joins"** | Check py3.11 vs 3.12 numpy pins, Silero `download-files` egress, LiveKit Cloud outbound, SP folder grants | [SKILL.md](skills/building-voice-agents-on-databricks/SKILL.md) |
| **Fix `NameError: RunContext` on every tool turn** | Drop postponed annotations in the tools module so LiveKit `get_type_hints()` can resolve `RunContext` | [agent-and-tools.md](skills/building-voice-agents-on-databricks/references/agent-and-tools.md) |

Example prompts: *“Deploy this voice agent as a Databricks App using the skill.”* · *“Add a Lakebase `record_lookup` tool the way the skill does.”* · *“The call connects but no agent joins — follow the skill.”*

## Prerequisites

- A Databricks workspace with Unity AI Gateway (chat models for the tiers and for generation, plus an embedding
  model) and a Lakebase database with [Lakebase Search](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres)
  enabled (`lakebase_vector` + `lakebase_text`).
- A LiveKit project (for example LiveKit Cloud) and a Deepgram API key.
- The Databricks CLI with a profile you choose (`--profile <name>`; nothing here auto-selects one), and
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

The full walkthrough is in the skill:
[`skills/building-voice-agents-on-databricks/references/deployment.md`](skills/building-voice-agents-on-databricks/references/deployment.md).
Or ask a coding agent with that skill loaded to deploy it. In short:

1. Compile the worker dependencies for the Apps runtime (Python 3.11):
   `uv pip compile agent-requirements.in --python-version 3.11 -o agent-requirements.txt`.
2. Put the five secrets (LiveKit URL, key and secret; Deepgram key; Databricks token) in a secret scope and attach
   them to the app as resources; `app.yaml` reads them through `valueFrom`.
3. Give the app's service principal `READ` on the scope and `CAN_MANAGE` on the workspace source folder.
4. Start the app, `databricks sync` a staging folder with `--full`, then `databricks apps deploy`.
5. Watch `databricks apps logs` for the web tier listening and `registered worker` about 30–60 seconds later.

## Configuration

Copy `.env.example` to `.env.local` for local runs; the deployed app reads the same settings from `app.yaml`.
Replace these placeholders with your own values:

| Placeholder | Replace with |
|---|---|
| `https://your-workspace.cloud.databricks.com` | your workspace URL |
| `projects/your-project/branches/production/endpoints/primary` | your Lakebase endpoint (`LAKEBASE_ENDPOINT`; database `databricks_postgres`) |
| `your_catalog`, `your_schema` | the catalog and schema for trace tables |
| `/Users/you@example.com/ugvoice` | your MLflow experiment path |
| `your-sql-warehouse-id` | the SQL warehouse that reads traces |

Some comments and docs mention `ReferenceApp` / `<reference-app>`: an earlier LiveKit-on-Databricks app this
blueprint generalizes. It isn't part of this repo.

## Docs

- **Lakebase Search:** [Lakebase Search: state-of-the-art full text and vector search for Postgres](https://www.databricks.com/blog/lakebase-search-state-art-full-text-and-vector-search-postgres) — `lakebase_vector` (ANN) + `lakebase_text` (BM25); this blueprint uses both indexes, and the voice tool path is ANN
- **Agent skill:** [`skills/building-voice-agents-on-databricks/SKILL.md`](skills/building-voice-agents-on-databricks/SKILL.md) — [what you can ask it to do](#agent-skill)
- **Design spec:** `docs/superpowers/specs/2026-09-24-unity-gateway-voice-studio-design.md`
- **Plan (wave 1):** `docs/superpowers/plans/2026-09-24-ug-voice-studio-plan-1-foundations.md`
- **Conventions:** run everything with `uv`; the Databricks profile is user-chosen (`--profile <name>`), never auto-selected.

## License

MIT — see [LICENSE](LICENSE).
