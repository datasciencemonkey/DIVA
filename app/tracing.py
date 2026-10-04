"""OTel tracing enrichment for UG Voice Studio.

Reuses ReferenceApp app/agent.py's span-enrichment seam verbatim:
  - _SPAN_TYPES, _as_json_value, _SpanEnrichmentExporter, build_tracer_provider
Adds: the ug.ai_decide span type (in _SPAN_TYPES) and fill_ug_metadata (ug.* attributes, PII-free).
"""
from __future__ import annotations

import ast
import json
import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter


# ---------------------------------------------------------------------------
# OTel span-enrichment tracing seam
# (verbatim from ReferenceApp app/agent.py)
# ---------------------------------------------------------------------------

# Span name → MLflow SpanType for typed trace-tree rendering. `ug.ai_decide` is ours (the span the voice-mode
# controller opens on a mode transition or dropped late answer, app/voice_mode.py); the exporter JSON-encodes the type, so it is typed here
# rather than with a bare string on the span.
_SPAN_TYPES = {
    "agent_session": "AGENT",
    "llm_node": "LLM",
    "function_tool": "TOOL",
    "ug.ai_decide": "CHAIN",
}


def _as_json_value(raw: str | None, wrap_key: str) -> str | None:
    """Coerce a LiveKit attribute string into a JSON value for mlflow.spanInputs/Outputs."""
    if not raw:
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            return json.dumps(parse(raw))
        except Exception:
            continue
    return json.dumps({wrap_key: raw})


class _SpanEnrichmentExporter(SpanExporter):
    """Adds MLflow-native attributes to spans at export time.

    Databricks' OTLP ingest maps:
      - Root span: session.id / user.id → trace metadata; mlflow.spanInputs/Outputs → preview
      - Any span: mlflow.spanType → typed trace tree; per-type I/O → span detail view
    """

    def __init__(self, inner: SpanExporter, enrichment: dict[str, str]) -> None:
        self._inner = inner
        self._enrichment = enrichment  # filled live as session details become known

    def export(self, spans):
        for span in spans:
            try:
                self._enrich_span(span)
            except Exception:
                pass  # enrichment must never break the export path
        return self._inner.export(spans)

    def _enrich_span(self, span) -> None:
        attrs = dict(span.attributes or {})
        changed = False

        # Root span: identity + conversation previews
        if span.parent is None and self._enrichment:
            attrs.update(self._enrichment)
            changed = True

        # Span-type classification
        span_type = _SPAN_TYPES.get(span.name)
        if span_type:
            attrs["mlflow.spanType"] = json.dumps(span_type)
            changed = True

        # Per-type I/O mapping
        if span.name == "function_tool":
            inp = _as_json_value(attrs.get("lk.function_tool.arguments"), "arguments")
            out = _as_json_value(attrs.get("lk.function_tool.output"), "output")
            attrs["gen_ai.operation.name"] = "execute_tool"
            for genai_key, lk_key in (
                ("gen_ai.tool.name", "lk.function_tool.name"),
                ("gen_ai.tool.call.id", "lk.function_tool.id"),
                ("gen_ai.tool.call.arguments", "lk.function_tool.arguments"),
                ("gen_ai.tool.call.result", "lk.function_tool.output"),
            ):
                if (val := attrs.get(lk_key)) is not None:
                    attrs[genai_key] = val
            changed = True
        elif span.name == "llm_node":
            inp = _as_json_value(attrs.get("lk.chat_ctx"), "input")
            out = _as_json_value(
                attrs.get("lk.response.text") or attrs.get("lk.response.function_calls"),
                "output",
            )
        else:
            inp = out = None

        if inp:
            attrs["mlflow.spanInputs"] = inp
            changed = True
        if out:
            attrs["mlflow.spanOutputs"] = out
            changed = True

        if changed:
            span._attributes = attrs

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)

    def shutdown(self) -> None:
        self._inner.shutdown()


def build_tracer_provider(enrichment: dict[str, str] | None = None) -> TracerProvider | None:
    """OTLP span exporter to Databricks UC + MLflow.

    Fail-soft: returns None (tracing disabled) when DATABRICKS_TRACE_* vars are absent.
    """
    catalog = os.getenv("DATABRICKS_TRACE_CATALOG")
    schema = os.getenv("DATABRICKS_TRACE_SCHEMA")
    prefix = os.getenv("DATABRICKS_TRACE_TABLE_PREFIX")
    if not (catalog and schema and prefix):
        return None

    exporter: SpanExporter = OTLPSpanExporter(
        endpoint=f"{os.environ['DATABRICKS_HOST'].rstrip('/')}/api/2.0/otel/v1/traces",
        headers={
            "Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
            "X-Databricks-UC-Table-Name": f"{catalog}.{schema}.{prefix}_otel_spans",
        },
    )
    if enrichment is not None:
        exporter = _SpanEnrichmentExporter(exporter, enrichment)
    provider = TracerProvider(
        resource=Resource.create(
            {SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "ug-voice-agent")}
        )
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


# ---------------------------------------------------------------------------
# UG-specific trace enrichment
# ---------------------------------------------------------------------------


def fill_ug_metadata(enrichment: dict, bind_ctx, session_ctx, voice_mode=None) -> None:
    """Attach ug.* trace attributes (spec §14; §7.11 for ug.voice_*). Fail-soft; PII-free (no caller name/spend,
    and no utterance or agent line).

    `voice_mode` is the call's VoiceModeController (or its VoiceModeState). Call this again at shutdown so the
    root span carries the final mode; whatever the carrier lacks is simply not set.
    """
    try:
        if bind_ctx is not None:
            enrichment["ug.data_generation_id"] = bind_ctx.data_generation_id
            enrichment["ug.company"] = bind_ctx.company
            enrichment["ug.loyalty_tier"] = bind_ctx.tier      # band label only
            enrichment["ug.routed_model"] = bind_ctx.model
        lr = getattr(session_ctx, "last_retrieval", None)
        if lr is not None:
            import json as _json
            enrichment["ug.retrieval"] = _json.dumps(lr)
    except Exception:
        pass  # enrichment must never break the voice pipeline
    _fill_voice_mode(enrichment, voice_mode)


def _fill_voice_mode(enrichment: dict, voice_mode) -> None:
    """The ug.voice_* root attrs (spec §7.11): the final mode, its transition count, the expressive tags performed,
    whether the call fell back to the fallback voice, and the engine behind the last recorded decision (read from
    the evidence the controller last published, so absent until it has published one). Independent of the
    bind/retrieval attrs: a failure here loses only these."""
    try:
        state = getattr(voice_mode, "state", voice_mode)   # a controller carries its state; a bare state is used as-is
        for key, field in (
            ("ug.voice_mode", "mode"),
            ("ug.voice_mode_transitions", "transitions"),
            ("ug.expressive_tags", "tags_spoken"),
            ("ug.voice_degraded", "degraded"),
        ):
            value = getattr(state, field, None)
            if value is not None:   # 0 and False are real values
                enrichment[key] = value
        last = getattr(state, "last", None)
        engine = last.get("engine") if isinstance(last, dict) else None
        if engine is not None:
            enrichment["ug.decide_engine"] = engine
    except Exception:
        pass  # enrichment must never break the voice pipeline
