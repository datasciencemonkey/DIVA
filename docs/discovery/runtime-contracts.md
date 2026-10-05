# Discovery: runtime contracts (R7, R3, R4)

_R7 was read from ReferenceApp's shipped code (2026-09-24); R3 and R4 came from the ReferenceApp reference and the LiveKit docs. Plans 3 and 4 built all three, and they run live._

## R7 — OTel → UC/MLflow ingest path

Use ReferenceApp's shipped, working mechanism (`app/agent.py::_build_tracer_provider`):
```python
OTLPSpanExporter(
    endpoint=f"{host}/api/2.0/otel/v1/traces",
    headers={"Authorization": f"Bearer {token}",
             "X-Databricks-UC-Table-Name": f"{catalog}.{schema}.{prefix}_otel_spans"},
)
```
The diagram's "zerobus" is the ingest transport; the OTLP HTTP endpoint above is the concrete client mechanism and lands spans in a UC Delta table. Enrichment swaps `referenceapp.*` → `ug.*` (`ug.data_generation_id`, `ug.company`, `ug.loyalty_tier`, `ug.routed_model`, `ug.retrieval`). Fail-soft: disabled when `DATABRICKS_TRACE_*` unset. The trace catalog and schema exist on the workspace, and live calls read as MLflow traces.

## R3 — per-turn cost telemetry

The Responses API returns a `usage` object (input/output/total tokens) per call; `livekit-plugins-openai` surfaces LLM usage via its metrics/events. Plan 3 wires a per-turn usage collector → Costs pillar (`tokens × price table`, model-routing-contract). The hook is the session's `session_usage_updated` event (`app/agent.py`), and the Costs pillar fills in live.

## R4 — build AgentSession AFTER participant join

The studio must bind the loyalty tier (→ routed model) before constructing the LLM, and the caller identity arrives on the participant token — so `AgentSession` is built **after** `ctx.connect()` + `await ctx.wait_for_participant()`. In livekit-agents 1.5.6, `AgentSession` construction is independent of room connection; `session.start(room=ctx.room)` binds the room. ReferenceApp already separates `ctx.connect()` from `session.start()`, so moving the `AgentSession(...)` construction after the wait works: `app/agent.py` connects, waits for the participant and then builds the session (the one adaptation with real ordering risk).
