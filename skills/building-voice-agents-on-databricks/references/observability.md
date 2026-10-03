# MLflow / OpenTelemetry observability

LiveKit Agents already emit OpenTelemetry spans for the session, LLM node, and tool calls. Point them at Databricks and every call lands as an **MLflow trace stored in a Unity Catalog Delta table** — no per-turn instrumentation needed.

## Wiring (3 steps)

**1. Build an OTLP tracer provider to Databricks.** Databricks ingests OTLP over HTTP at `/api/2.0/otel/v1/traces`; the target UC table is named in a header. Make it **fail-soft** — return `None` (tracing off) when the trace env vars are absent, so local runs and misconfig never break the voice pipeline.

```python
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

def build_tracer_provider():
    catalog = os.getenv("DATABRICKS_TRACE_CATALOG")
    schema  = os.getenv("DATABRICKS_TRACE_SCHEMA")
    prefix  = os.getenv("DATABRICKS_TRACE_TABLE_PREFIX")
    if not (catalog and schema and prefix):
        return None                                   # tracing disabled, fail-soft
    exporter = OTLPSpanExporter(
        endpoint=f'{os.environ["DATABRICKS_HOST"].rstrip("/")}/api/2.0/otel/v1/traces',
        headers={
            "Authorization": f'Bearer {os.environ["DATABRICKS_TOKEN"]}',
            "X-Databricks-UC-Table-Name": f"{catalog}.{schema}.{prefix}_otel_spans",
        },
    )
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "voice-agent")}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider
```

**2. Register it with LiveKit and flush on shutdown** (in the worker entrypoint):

```python
from livekit.agents.telemetry import set_tracer_provider

provider = build_tracer_provider()
if provider is not None:
    set_tracer_provider(provider, metadata={"livekit.agent_name": AGENT_NAME})
    async def _flush():
        provider.force_flush(); provider.shutdown()
    ctx.add_shutdown_callback(_flush)                 # spans export when the call ends
```

**3. Enrich spans so MLflow renders them natively.** Marked "optional" but in practice **required for a useful MLflow view** — without it you get rows in the UC Delta table but no typed trace tree. LiveKit's raw span names/attrs don't carry MLflow attributes, so wrap the exporter and add them at export time. Full implementation (adapt as needed):

```python
import ast, json
from opentelemetry.sdk.trace.export import SpanExporter

_SPAN_TYPES = {"agent_session": "AGENT", "llm_node": "LLM", "function_tool": "TOOL"}

def _json_value(raw, wrap_key):
    if not raw:
        return None
    for parse in (json.loads, ast.literal_eval):     # LiveKit attrs are JSON-ish strings
        try:
            return json.dumps(parse(raw))
        except Exception:
            continue
    return json.dumps({wrap_key: raw})

class SpanEnrichmentExporter(SpanExporter):
    """Add MLflow-native attributes to LiveKit spans at export time. Never raises."""
    def __init__(self, inner, enrichment):
        self._inner = inner
        self._enrichment = enrichment            # dict you fill live during the call (identity, etc.)

    def export(self, spans):
        for span in spans:
            try:
                self._enrich(span)
            except Exception:
                pass                             # enrichment must never break the export path
        return self._inner.export(spans)

    def _enrich(self, span):
        attrs = dict(span.attributes or {})
        if span.parent is None and self._enrichment:          # ROOT span: identity + trace metadata
            attrs.update(self._enrichment)                    # e.g. {"session.id":..., "user.id":...}
        st = _SPAN_TYPES.get(span.name)
        if st:
            attrs["mlflow.spanType"] = json.dumps(st)         # typed trace tree (AGENT/LLM/TOOL)
        if span.name == "function_tool":
            attrs["gen_ai.operation.name"] = "execute_tool"
            for gk, lk in (("gen_ai.tool.name", "lk.function_tool.name"),
                           ("gen_ai.tool.call.arguments", "lk.function_tool.arguments"),
                           ("gen_ai.tool.call.result", "lk.function_tool.output")):
                if (v := attrs.get(lk)) is not None:
                    attrs[gk] = v
            inp = _json_value(attrs.get("lk.function_tool.arguments"), "arguments")
            out = _json_value(attrs.get("lk.function_tool.output"), "output")
        elif span.name == "llm_node":
            inp = _json_value(attrs.get("lk.chat_ctx"), "input")
            out = _json_value(attrs.get("lk.response.text") or attrs.get("lk.response.function_calls"), "output")
        else:
            inp = out = None
        if inp: attrs["mlflow.spanInputs"] = inp              # preview in the trace UI
        if out: attrs["mlflow.spanOutputs"] = out
        span._attributes = attrs

    def force_flush(self, timeout_millis=30000): return self._inner.force_flush(timeout_millis)
    def shutdown(self): self._inner.shutdown()
```

Wire it into `build_tracer_provider` by wrapping the OTLP exporter **before** the `BatchSpanProcessor`:

```python
    enrichment = {}                                          # fill live: enrichment["session.id"] = ...
    exporter = SpanEnrichmentExporter(exporter, enrichment)
    provider.add_span_processor(BatchSpanProcessor(exporter))
```

The `enrichment` dict is captured by reference — mutate it during the call (e.g. once you know the caller) and the root span picks it up at export.

## Custom spans and `ug.*` attributes

Your own spans (for example one per AI decision, see [agent-and-tools.md](agent-and-tools.md)) come from the same tracer provider. These rules keep them useful in MLflow:

- **Type them through the map.** The exporter writes `mlflow.spanType` as `json.dumps(...)`, so a custom span's type belongs in `_SPAN_TYPES` (`"ug.ai_decide": "CHAIN"`), not in a bare string set on the span.
- **Keep the span PII-free.** One span per classified turn with the engine, cue, source, intent, confidence, probabilities, latency, path (same-turn / announced / late-dropped), reason, and the mode before and after. Never copy utterance text: LiveKit's own user-turn spans already hold the transcript.
- **Put per-call rollups on the root span** through the `enrichment` dict (`ug.voice_mode`, `ug.voice_mode_transitions`, `ug.decide_engine`, `ug.expressive_tags`, `ug.voice_degraded`). Attribute values can be `str`, `bool`, `int` or `float`, so the dict need not be string-only. Fill the final values in the shutdown callback, before `force_flush`.
- **Enrichment is fail-soft, so a typo fails silently.** Assert in a test that the keys you expect really land on the root span.

(Source anchors for these notes are in the Voice Studio repo's `docs/gotchas.md`, section "Repo (voice-agents-ug-demo)".)

## Config (app.yaml)

Set all three or tracing stays off: `DATABRICKS_TRACE_CATALOG`, `DATABRICKS_TRACE_SCHEMA`, `DATABRICKS_TRACE_TABLE_PREFIX`, plus `OTEL_SERVICE_NAME`. `DATABRICKS_HOST`/`DATABRICKS_TOKEN` are already present. The app SP / PAT user must be able to write to the target UC schema.

## Where it lands & how to read it

- Delta table: `<catalog>.<schema>.<prefix>_otel_spans` (raw spans) — queryable in SQL.
- MLflow: the traces appear under the MLflow experiment (set `MLFLOW_EXPERIMENT_NAME` if reading via MLflow); typed spans (AGENT/LLM/TOOL) show inputs/outputs per node.
- Common miss: no traces = one of the three `DATABRICKS_TRACE_*` vars unset (fail-soft silently disabled it), or spans never flushed (missing the shutdown callback).
