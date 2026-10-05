"""OTel tracing enrichment for UG Voice Studio.

Reuses ReferenceApp app/agent.py's span-enrichment seam verbatim:
  - _SPAN_TYPES, _as_json_value, _SpanEnrichmentExporter, build_tracer_provider
Adds: the ug.ai_decide span type (in _SPAN_TYPES), fill_ug_metadata (ug.* attributes, PII-free), and
per-trace root enrichment for a process-wide provider (register_trace_enrichment).
"""
from __future__ import annotations

import ast
import json
import os
import threading
from collections.abc import Callable
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter


# ---------------------------------------------------------------------------
# OTel span-enrichment tracing seam
# (verbatim from ReferenceApp app/agent.py)
# ---------------------------------------------------------------------------

# Span name → MLflow SpanType for typed trace-tree rendering. `ug.ai_decide` is ours (the span the voice-mode
# controller opens on a mode transition or dropped late answer, app/voice_mode.py); the exporter JSON-encodes the type, so it is typed here
# rather than with a bare string on the span.
_SPAN_TYPES = {
    "job_entrypoint": "AGENT",  # livekit-agents 1.8.3 root; without a type the tree does not render
    "agent_session": "AGENT",
    "llm_node": "LLM",
    "function_tool": "TOOL",
    "ug.ai_decide": "CHAIN",
}

# livekit-agents 1.8.3 ends `job_entrypoint` after user shutdown callbacks. A short bound so
# on_end can export the root without waiting on the batch processor's default schedule.
_ROOT_FLUSH_TIMEOUT_MS = 5_000


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


# Root-span enrichment per trace. The provider is process-wide (installed before LiveKit opens the job's
# root), and the THREAD executor can run several jobs in one process, so each job's ug.* dict is keyed by
# its trace id rather than shared.
_ROOT_ENRICHMENT: dict[int, dict[str, Any]] = {}
_ROOT_ENRICHMENT_LOCK = threading.Lock()


def register_trace_enrichment(enrichment: dict[str, Any]) -> None:
    """Attach `enrichment` to the root of the current trace. Call it from the job entrypoint, where the
    current span is LiveKit's `job_entrypoint`. The dict is read at export time, so keep filling it."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        with _ROOT_ENRICHMENT_LOCK:
            _ROOT_ENRICHMENT[ctx.trace_id] = enrichment


def _take_root_enrichment(trace_id: int) -> dict[str, Any] | None:
    """A trace has one root, exported once, so its entry is removed as it is read."""
    with _ROOT_ENRICHMENT_LOCK:
        return _ROOT_ENRICHMENT.pop(trace_id, None)


class _SpanEnrichmentExporter(SpanExporter):
    """Adds MLflow-native attributes to spans at export time.

    Databricks' OTLP ingest maps:
      - Root span: session.id / user.id → trace metadata; mlflow.spanInputs/Outputs → preview
      - Any span: mlflow.spanType → typed trace tree; per-type I/O → span detail view
    """

    def __init__(self, inner: SpanExporter,
                 root_enrichment: Callable[[int], dict[str, Any] | None] = _take_root_enrichment) -> None:
        self._inner = inner
        self._root_enrichment = root_enrichment  # trace id -> the job's ug.* dict, read when the root exports

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
        if span.parent is None and (enrichment := self._root_enrichment(span.context.trace_id)):
            attrs.update(enrichment)
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


class _RootFlushSpanProcessor(SpanProcessor):
    """Force-flush when a root span ends so `job_entrypoint` is exported before the job process exits.

    Register *after* `BatchSpanProcessor`: processors run in add order, so the batch processor
    queues the span first and this flush then exports it. `force_flush` is a no-op here to avoid
    recursing when the provider flushes every processor.
    """

    def __init__(self, provider: TracerProvider, timeout_millis: int = _ROOT_FLUSH_TIMEOUT_MS) -> None:
        self._provider = provider
        self._timeout_millis = timeout_millis

    def on_end(self, span) -> None:
        if span.parent is not None:
            return
        try:
            self._provider.force_flush(timeout_millis=self._timeout_millis)
        except Exception:
            pass  # tracing must never break the voice pipeline

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # noqa: ARG002
        return True


def flush_open_traces(provider: TracerProvider) -> None:
    """Export finished spans. Do not shut the provider down.

    livekit-agents 1.8.3 wraps the job in `job_entrypoint` and ends it *after* user shutdown
    callbacks (and after `job_shutdown`). `TracerProvider.shutdown()` makes the batch processor
    drop anything that ends later, which orphans `agent_session` and hides the call in MLflow.
    LiveKit's own `_shutdown_telemetry` flushes only exporters the framework attached. Process
    atexit shutdown remains the backstop.
    """
    provider.force_flush()


def build_tracer_provider() -> TracerProvider | None:
    """OTLP span exporter to Databricks UC + MLflow. Build it once per process and hand it to LiveKit's
    `set_tracer_provider` before any job starts (the worker's setup_fnc): livekit-agents 1.8.3 opens the
    job's root, `job_entrypoint`, before the entrypoint runs, and a provider set later never sees that root.
    Root enrichment comes from `register_trace_enrichment`.

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
    exporter = _SpanEnrichmentExporter(exporter)
    provider = TracerProvider(
        resource=Resource.create(
            {SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "ug-voice-agent")}
        )
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    provider.add_span_processor(_RootFlushSpanProcessor(provider))
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
