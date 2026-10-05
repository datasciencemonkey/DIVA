---
name: building-voice-agents-on-databricks
description: Use when building, deploying, or debugging a real-time voice agent on Databricks — a LiveKit agent worker plus a browser token server packaged as one Databricks App, with the LLM on the Databricks Foundation Model API / Unity Gateway, Lakebase-backed tools, and MLflow/OpenTelemetry tracing into Unity Catalog. Also use when a LiveKit-on-Databricks deploy fails at build/launch, the agent worker won't start, function-tool calls raise NameError, or a call connects but no agent ever joins.
---

# Building Voice Agents on Databricks

## Overview

A real-time voice agent on Databricks is **two processes in one Databricks App container**:

- a **web/token tier** — a tiny stdlib HTTP server that serves the browser page and mints LiveKit access tokens carrying agent-dispatch, and
- a **LiveKit agent worker** — connects out to LiveKit Cloud and runs the speech pipeline (STT → LLM → TTS) plus tools.

LiveKit Cloud is the only meeting point: browser ⇄ LiveKit SFU ⇄ worker over WebRTC/WebSocket. **The web tier never touches media** — it only serves the page and signs tokens. The LLM is a Databricks Foundation Model served over the **Unity Gateway** (OpenAI-compatible), tools read **Lakebase** (Postgres), and every call exports **OpenTelemetry spans to MLflow / Unity Catalog**.

## When to use

- Building a browser-to-agent voice experience where the brain runs on Databricks.
- Deploying a LiveKit `AgentServer` worker + token server to Databricks Apps.
- Adding MLflow tracing or Lakebase tools to such an agent.
- Debugging symptoms: deploy fails `App deployment failed unexpectedly`; worker venv install fails; `NameError` on every tool call; **call connects but no agent joins**.

**Not for:** non-voice Databricks apps (use `databricks-apps` skills), or LiveKit agents that don't touch Databricks.

## Architecture (one container)

```
Browser ──WebRTC──▶ LiveKit Cloud ◀──WebSocket── Agent worker (app/agent.py, `start`)
   ▲                                                  │  STT (Deepgram) → LLM (FM/Unity Gateway) → TTS
   │ GET /api/token (signed, agent-dispatch)          │  tools → Lakebase ; OTel spans → MLflow/UC
   └──────── Web/token tier (app/web_server.py, :DATABRICKS_APP_PORT)
                    └── both launched by start_app.py (single container)
```

## The pieces (quick reference)

| Concern | Pattern | Reference |
|---|---|---|
| Single-container launcher | bind web port fast; boot worker in isolated `/tmp/agent-venv` | [deployment.md](references/deployment.md), [templates/start_app.py](templates/start_app.py) |
| Token / web tier | stdlib HS256 JWT + `roomConfig` agent dispatch; unique room per visit | [agent-and-tools.md](references/agent-and-tools.md) |
| Agent worker | connect → wait for participant → **build session after identity known**; FM over Unity Gateway | [agent-and-tools.md](references/agent-and-tools.md) |
| LLM | `openai.responses.LLM` at `{host}/ai-gateway/openai/v1` (Responses API, for tool-calling) | [agent-and-tools.md](references/agent-and-tools.md) |
| Lakebase tools | `build_tools()` factory; pool from `WorkspaceClient` credential; fail-soft when `pool=None` | [agent-and-tools.md](references/agent-and-tools.md) |
| Observability | LiveKit OTel → OTLP `/api/2.0/otel/v1/traces` + UC-table header → MLflow-native spans | [observability.md](references/observability.md) |
| Deploy | `databricks sync` + `apps deploy`; secret scope + resources; SP grants | [deployment.md](references/deployment.md) |

## Build workflow

1. **Worker** (`app/agent.py`): `AgentServer` + `@server.rtc_session`; connect, wait for the participant, read token metadata, then build `AgentSession` on the chosen model. See [agent-and-tools.md](references/agent-and-tools.md).
2. **Token tier** (`app/web_server.py`): mint an HS256 LiveKit token with `roomConfig.agents` dispatch and a fresh room per visit.
3. **Tools**: write `build_tools(pool, ctx)` returning `function_tool`s; back them with a Lakebase pool. Mind the annotation gotcha below.
4. **Observability**: build an OTLP tracer provider to Databricks, `set_tracer_provider(...)`, flush on shutdown. See [observability.md](references/observability.md).
5. **Deploy**: `start_app.py` launcher + `app.yaml` + a Python-version-correct `agent-requirements.txt`; `databricks sync` then `apps deploy`. See [deployment.md](references/deployment.md).

## Gotchas that actually cost hours

- **Resolve the agent venv deps for the Apps Python (3.11), NOT 3.12.** `uv export` of a lock whose `requires-python>=3.12` pins `numpy==2.5.x` (needs 3.12) → the worker's `/tmp/agent-venv` install fails → worker never starts → **"call connects but no agent joins."** Compile the direct deps for 3.11: `uv pip compile deps.in --python-version 3.11 -o agent-requirements.txt` (yields numpy 2.4.x).
- **LiveKit tool modules must NOT use `from __future__ import annotations`.** LiveKit resolves `context: RunContext` via `typing.get_type_hints()` at session start; a stringized annotation can't see a factory-local `RunContext` → `NameError` on every LLM turn. Use eager annotations.
- **Upload with `databricks sync --full`, not `workspace import-dir`.** It's the path Apps deploy expects; import-dir source can trip the build.
- **In the launcher, `os.environ.pop("DATABRICKS_CLIENT_ID"/"DATABRICKS_CLIENT_SECRET")`** so a PAT is the SDK's single auth method (Apps inject SP OAuth too; the SDK refuses ambiguous auth).
- **Grant the app service principal `CAN_MANAGE` on the workspace source folder + `READ` on the secret scope**, or deploy dies at "Preparing source code" (the UI auto-grants; the CLI does not).
- **Launch the app compute first; the worker registers ~30–60s AFTER deploy succeeds** (venv install + model `download-files`). Don't judge a call attempt in that window. If `download-files` can't reach the model CDN, the worker never registers — same "no agent" symptom.
- **The worker needs outbound to LiveKit Cloud (`wss://…livekit.cloud`) and your STT/TTS provider.** This worked out of the box on a standard serverless workspace (worker registered and received job requests, and ElevenLabs at `api.elevenlabs.io` was reachable), but egress is workspace-config-dependent — if the worker won't register, verify outbound access before debugging code.
- **Generic `App deployment failed unexpectedly` with no container logs, reproducible on a bare hello-world = platform-side.** Retry later; don't keep changing the app.

## Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Call connects, no agent | worker venv failed / still installing / Silero `download-files` failed (no egress) / egress to LiveKit Cloud blocked | check `apps logs` for `registered worker`; fix deps for py3.11; confirm outbound to `wss://…livekit.cloud` and the model CDN |
| `NameError: RunContext` every turn | `from __future__ import annotations` in tool module | remove it (eager annotations) |
| Deploy fails at "Preparing source code" | app SP can't read source folder | grant SP `CAN_MANAGE` on the folder |
| Deploy fails ~7s, no logs, even trivial app | platform build/launch issue | retry later; verify with a probe app |
| SDK "more than one authorization method" | PAT + injected SP creds both present | pop `DATABRICKS_CLIENT_ID/SECRET` in launcher |
| No traces in MLflow | `DATABRICKS_TRACE_*` unset (fail-soft disables) | set catalog/schema/prefix + host/token |
