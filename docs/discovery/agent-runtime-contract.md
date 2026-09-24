# Discovery: LiveKit agent runtime contract (Plan 3, Task 1)

_Verified 2026-09-24 against the installed stack (livekit-agents 1.5.6 + plugins, opentelemetry 1.39.1)._

## Deps
`livekit==1.1.5`, `livekit-agents==1.5.6`, `livekit-api==1.1.0`, `livekit-plugins-{openai,deepgram,silero}==1.5.6`, `httpx==0.28.1`, `httpx-sse==0.4.3`, `opentelemetry-sdk==1.39.1`, `opentelemetry-exporter-otlp-proto-http==1.39.1`. Import check (`import livekit.agents, livekit.plugins.{openai,deepgram,silero}, opentelemetry.sdk, …otlp…trace_exporter`) → `ok`.

## R4 — build AgentSession AFTER participant join (the spec §6 reorder) — CONFIRMED
`AgentSession.__init__` params (verified via `inspect`): `self, stt, vad, llm, tts, turn_handling, tools, mcp_servers, max_tool_steps, …` — **no `room`/`ctx` argument**. Construction is independent of a connected room, so building `AgentSession` *after* `ctx.connect()` + `await ctx.wait_for_participant()` (once the tier-routed `model` is known) is valid; `session.start(room=ctx.room, agent=…)` binds the room separately. `Agent.__init__(self, instructions, id, chat_ctx, tools, …)` takes `instructions` → feed `build_instructions(...)`. Live confirmation: Task 8.

## R3 — per-turn token usage for the Costs pillar (Plan 4) — AVAILABLE
`livekit.agents.metrics` exports `UsageCollector`, `LLMMetrics`, `LLMModelUsage`, `AgentSessionUsage`, `ModelUsageCollector`, `UsageSummary`. The session emits `metrics_collected` events carrying `LLMMetrics` (token usage). Plan 4's Costs pillar wires a `UsageCollector` (or listens for `metrics_collected`) → tokens × per-model price. Not needed for Plan 3.

## R7 — OTel path
Reuse ReferenceApp's OTLP client (`{host}/api/2.0/otel/v1/traces` + `X-Databricks-UC-Table-Name` header); fail-soft when `DATABRICKS_TRACE_*` unset. Implemented in `app/tracing.py` (Task 5); live-verify the trace catalog/schema in Task 8 if configured.
