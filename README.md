# DIVA — Databricks Intelligent Voice Agents

**A blueprint for running real-time voice agents on Databricks.**

DIVA is a working, end-to-end blueprint. A caller talks to an AI support agent from the browser. The agent's
models are governed and served through **Unity AI Gateway**, its answers come from data in **Lakebase
Postgres**, every call is traced to **Unity Catalog / MLflow**, and everything ships as **one Databricks App**.
Voice transport is **LiveKit**; speech-to-text and text-to-speech are **Deepgram**.

The reference implementation is the **Unity Gateway Voice Studio**: a governed voice-agent studio presenting
**Unity AI Gateway** — Choice / Control / Context / Costs — through a LiveKit customer-support agent with
loyalty→model routing, generic Lakebase-scoped tools, and generated per-company datasets. Fork it, point it at
your workspace, and swap in your own data, prompts and tools.

## What the blueprint covers

| Pillar | What you see | How it's built |
|---|---|---|
| **Choice** | Different callers get different models | App-side routing: the caller's loyalty tier (Standard / Premium / VIP) maps to a model through env vars; Unity AI Gateway serves it |
| **Control** | Governed, observable LLM calls | Every chat and embedding call goes through Unity AI Gateway; OpenTelemetry spans land in a Unity Catalog table and render as MLflow traces |
| **Context** | Answers grounded in real data | Read-only tools over Lakebase: `semantic_search` (pgvector ANN) and `record_lookup` (the caller's own records) |
| **Costs** | Spend you can watch | Cumulative token usage streamed to the UI with a cost projection |

## How a call works

1. **Generate a world.** Name a fictional company, a role and a system prompt. The generator drafts documents,
   customers and records through Unity AI Gateway, embeds them, and writes them to Lakebase in one transaction
   under a fresh `data_generation_id`.
2. **Start a call.** The web tier mints a short-lived LiveKit token that carries `data_generation_id` and
   `customer_id`, and dispatches the agent into a fresh room.
3. **Bind and route.** The agent worker looks up the caller's tier in Lakebase (never from speech), and
   `route_for(tier)` picks the model. The model never sees the tier.
4. **Talk.** Deepgram STT → the routed LLM through the gateway's Responses API → Deepgram TTS. Both tools are
   scoped to the bound dataset, and `record_lookup` only returns the caller's own records.
5. **Show and trace.** PII-free evidence (routing decision, retrieval hits, token usage) streams to the UI, and
   the spans flush over OTLP into a Unity Catalog table.

## What's inside

| Path | What it is |
|---|---|
| `app/` | The LiveKit agent worker (`agent.py`), its tools and tracing, the web tier (`web_server.py`) and the studio UI (`web/public/`) |
| `src/` | Routing policy, the governance prompt, the synthetic-world generator, and the Lakebase / gateway / retrieval services |
| `infra/` | The Lakebase schema, ANN and BM25 indexes, and `apply_schema.py` |
| `start_app.py`, `app.yaml` | The single-container Databricks App launcher and spec |
| `skills/building-voice-agents-on-databricks/` | A reusable agent skill: deployment, agent-and-tools and observability patterns, plus templates |
| `docs/discovery/` | Verified contracts for the platform behavior the code relies on |
| `docs/superpowers/` | The design spec and implementation plans |
| `tests/` | 82 tests; the one live integration test is skipped unless `LAKEBASE_ENDPOINT` is set |

## Prerequisites

- A Databricks workspace with Unity AI Gateway (chat models for the tiers and for generation, plus an embedding
  model) and a Lakebase database with Lakebase Search enabled.
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

The full walkthrough is in [`skills/building-voice-agents-on-databricks/references/deployment.md`](skills/building-voice-agents-on-databricks/references/deployment.md).
In short:

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

- **Design spec:** `docs/superpowers/specs/2026-09-24-unity-gateway-voice-studio-design.md`
- **Plan (wave 1):** `docs/superpowers/plans/2026-09-24-ug-voice-studio-plan-1-foundations.md`
- **Conventions:** run everything with `uv`; the Databricks profile is user-chosen (`--profile <name>`), never auto-selected.

## License

MIT — see [LICENSE](LICENSE).
