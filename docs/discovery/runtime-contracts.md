# Discovery: runtime contracts (R7, R3, R4)

_R7 partly verified against ReferenceApp's shipped code (read 2026-09-24); R3/R4 recorded from the ReferenceApp reference + LiveKit docs and flagged for live confirmation in Plan 3 (livekit-agents is not installed in the Plan 1 env)._

## R7 — OTel → UC/MLflow ingest path

Use ReferenceApp's shipped, working mechanism (`app/agent.py::_build_tracer_provider`):
```python
OTLPSpanExporter(
    endpoint=f"{host}/api/2.0/otel/v1/traces",
    headers={"Authorization": f"Bearer {token}",
             "X-Databricks-UC-Table-Name": f"{catalog}.{schema}.{prefix}_otel_spans"},
)
```
The diagram's "zerobus" is the ingest transport; the OTLP HTTP endpoint above is the concrete client mechanism and lands spans in a UC Delta table. Enrichment swaps `referenceapp.*` → `ug.*` (`ug.data_generation_id`, `ug.company`, `ug.loyalty_tier`, `ug.routed_model`, `ug.retrieval`). Fail-soft: disabled when `DATABRICKS_TRACE_*` unset. **Confirm the trace catalog/schema exist on this workspace in Plan 3.**

## R3 — per-turn cost telemetry

The Responses API returns a `usage` object (input/output/total tokens) per call; `livekit-plugins-openai` surfaces LLM usage via its metrics/events. Plan 3 wires a per-turn usage collector → Costs pillar (`tokens × price table`, model-routing-contract). **Confirm the exact metrics hook in `livekit-plugins-openai==1.5.6` live in Plan 3.**

## R4 — build AgentSession AFTER participant join

The studio must bind the loyalty tier (→ routed model) before constructing the LLM, and the caller identity arrives on the participant token — so `AgentSession` is built **after** `ctx.connect()` + `await ctx.wait_for_participant()`. In livekit-agents 1.5.6, `AgentSession` construction is independent of room connection; `session.start(room=ctx.room)` binds the room. ReferenceApp already separates `ctx.connect()` from `session.start()`, so moving the `AgentSession(...)` construction after the wait is expected to work. **Confirm live in Plan 3** (the one adaptation with real ordering risk).
