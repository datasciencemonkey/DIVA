import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.tracing import (_SPAN_TYPES, _RootFlushSpanProcessor, _SpanEnrichmentExporter,
                         fill_ug_metadata, flush_open_traces, register_trace_enrichment)
from app.voice_mode import VoiceModeController, VoiceModeState
from app.voice_profiles import VoiceProfile
from src.policy.voice_mode import HALLOWEEN


def test_fill_sets_ug_attrs_without_leaking_pii():
    enr = {}
    bind = SimpleNamespace(data_generation_id="G1", company="Acme", tier="VIP",
                           model="databricks-gpt-6-sol", customer_id="C1", courtesy_name="Grace")
    sess = SimpleNamespace(last_retrieval={"kind": "semantic", "hits": 3})
    fill_ug_metadata(enr, bind, sess)
    assert enr["ug.data_generation_id"] == "G1"
    assert enr["ug.company"] == "Acme"
    assert enr["ug.loyalty_tier"] == "VIP"
    assert enr["ug.routed_model"] == "databricks-gpt-6-sol"
    # courtesy name is PII — must NOT be emitted to the trace
    assert "Grace" not in str(enr)


def test_fill_is_fail_soft_on_bad_input():
    fill_ug_metadata({}, None, None)  # must not raise


# ---------------------------------------------------------------------------------------
# The ug.ai_decide span type (spec §7.11)
# ---------------------------------------------------------------------------------------
def _export(enrichment, *names):
    """Run nested spans (`names[0]` is the root) through the real `_SpanEnrichmentExporter` into memory and
    return {span name: exported attributes}."""
    sink = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_SpanEnrichmentExporter(sink, lambda _trace_id: enrichment)))
    tracer = provider.get_tracer("test")
    with ExitStack() as spans:
        for name in names:
            spans.enter_context(tracer.start_as_current_span(name))
    provider.shutdown()
    return {span.name: span.attributes for span in sink.get_finished_spans()}


def test_ai_decide_span_is_typed_chain_through_the_json_encoding_exporter():
    assert _SPAN_TYPES["ug.ai_decide"] == "CHAIN"
    assert _SPAN_TYPES["job_entrypoint"] == "AGENT"
    assert {k: _SPAN_TYPES[k] for k in ("agent_session", "llm_node", "function_tool")} == {
        "agent_session": "AGENT", "llm_node": "LLM", "function_tool": "TOOL"}   # the LiveKit entries are untouched

    spans = _export({}, "agent_session", "ug.ai_decide")
    # The exporter JSON-encodes the type (so it has to come from the map, not a bare string on the span).
    assert spans["ug.ai_decide"]["mlflow.spanType"] == json.dumps("CHAIN")
    assert spans["agent_session"]["mlflow.spanType"] == json.dumps("AGENT")
    # A CHAIN span gets no inputs/outputs mapping: the utterance is not copied onto it.
    assert "mlflow.spanInputs" not in spans["ug.ai_decide"]
    assert "mlflow.spanOutputs" not in spans["ug.ai_decide"]


# ---------------------------------------------------------------------------------------
# The ug.voice_* root attrs (spec §7.11): the voice-mode controller's final state
# ---------------------------------------------------------------------------------------
VOICE_KEYS = {"ug.voice_mode", "ug.voice_mode_transitions", "ug.decide_engine", "ug.expressive_tags",
              "ug.voice_degraded"}


def voice_state(**overrides):
    """A real `VoiceModeState` as a degraded Halloween call leaves it (the engine rides on the last evidence)."""
    fields = dict(mode="halloween", transitions=3, tags_spoken=7, degraded=True, last={"engine": "ai_decide"})
    return VoiceModeState(**{**fields, **overrides})


@pytest.mark.parametrize("carrier", [
    pytest.param(lambda state: state, id="state"),
    pytest.param(lambda state: SimpleNamespace(state=state), id="controller"),
])
def test_fill_sets_the_voice_mode_root_attrs(carrier):
    enr = {}
    fill_ug_metadata(enr, None, None, voice_mode=carrier(voice_state()))
    assert enr == {"ug.voice_mode": "halloween", "ug.voice_mode_transitions": 3, "ug.decide_engine": "ai_decide",
                   "ug.expressive_tags": 7, "ug.voice_degraded": True}


def test_fill_voice_mode_records_zero_and_false_and_omits_an_engine_with_no_recorded_decision():
    enr = {}
    fill_ug_metadata(enr, None, None, voice_mode=VoiceModeState())   # a call that never left standard
    assert enr == {"ug.voice_mode": "standard", "ug.voice_mode_transitions": 0,
                   "ug.expressive_tags": 0, "ug.voice_degraded": False}


@pytest.mark.parametrize("carrier", [
    pytest.param(None, id="none"),
    pytest.param(object(), id="bare-object"),
    pytest.param(SimpleNamespace(), id="no-fields"),
    pytest.param(SimpleNamespace(mode=None, transitions=None, tags_spoken=None, degraded=None, last=None),
                 id="all-none"),
])
def test_fill_voice_mode_is_fail_soft_on_an_absent_carrier(carrier):
    enr = {}
    fill_ug_metadata(enr, None, None, voice_mode=carrier)   # Task 10 may not have wired the source: no raise
    assert enr == {}


def test_fill_voice_mode_sets_only_what_is_present():
    enr = {}
    fill_ug_metadata(enr, None, None, voice_mode=SimpleNamespace(mode="halloween", last="not an evidence dict"))
    assert enr == {"ug.voice_mode": "halloween"}


def test_fill_voice_mode_failures_are_isolated_from_the_other_attrs():
    class Exploding:
        @property
        def state(self):
            raise RuntimeError("boom")

    bind = SimpleNamespace(data_generation_id="G1", company="Acme", tier="VIP", model="m")
    enr = {}
    fill_ug_metadata(enr, bind, None, voice_mode=Exploding())   # a broken carrier costs only its own attrs
    assert enr["ug.company"] == "Acme"
    assert VOICE_KEYS.isdisjoint(enr)

    enr = {}
    fill_ug_metadata(enr, object(), None, voice_mode=voice_state())   # and a broken bind costs none of them
    assert VOICE_KEYS.issubset(enr)


def test_fill_voice_mode_never_copies_caller_or_agent_words():
    carrier = SimpleNamespace(
        mode="halloween", transitions=1, tags_spoken=2, degraded=False,
        last={"engine": "ai_decide", "decided_by": "Databricks AI Decide", "voice": "ElevenLabs", "reason": "enter"},
        last_agent_line="Welcome back Grace, your balance is 9412",
        utterance="my card ends 4242", transcript="make it spooky, I'm Grace Hopper")
    enr = {}
    fill_ug_metadata(enr, None, None, voice_mode=carrier)
    assert set(enr) == VOICE_KEYS
    assert not any(word in str(enr) for word in ("Grace", "9412", "4242", "spooky", "Welcome"))


def test_fill_reads_the_real_voice_mode_controller():
    """Pins the Task 7 interface Task 10 passes in: the state's fields, and the engine on the last evidence."""
    profiles = {"standard": VoiceProfile("standard", "Deepgram", "deepgram", frozenset(), None),
                "halloween": VoiceProfile("halloween", "ElevenLabs", "elevenlabs", frozenset({"whispers"}), None)}
    controller = VoiceModeController(SimpleNamespace(engine="fake_engine", label="Fake decide"), profiles,
                                     lambda profile: profile.key)
    controller.state.mode, controller.state.transitions = HALLOWEEN, 1   # production writes these via _transition
    controller.count_tag("whispers")
    controller.mark_degraded("vendor down")   # publishes the snapshot that carries the engine

    enr = {}
    fill_ug_metadata(enr, None, None, voice_mode=controller)
    assert enr == {"ug.voice_mode": "halloween", "ug.voice_mode_transitions": 1, "ug.decide_engine": "fake_engine",
                   "ug.expressive_tags": 1, "ug.voice_degraded": True}


def test_voice_mode_attrs_land_on_the_root_span_only():
    enr = {}
    fill_ug_metadata(enr, None, None, voice_mode=voice_state())
    spans = _export(enr, "agent_session", "ug.ai_decide")

    root = spans["agent_session"]
    assert root["ug.voice_mode"] == "halloween"
    assert root["ug.voice_mode_transitions"] == 3
    assert root["ug.decide_engine"] == "ai_decide"
    assert root["ug.expressive_tags"] == 7
    assert root["ug.voice_degraded"] is True
    assert VOICE_KEYS.isdisjoint(spans["ug.ai_decide"])


# ---------------------------------------------------------------------------------------
# livekit-agents 1.8.3: job_entrypoint ends after the user shutdown callback
# ---------------------------------------------------------------------------------------
def _batched_provider(sink, enrichment, *, root_flush: bool) -> TracerProvider:
    """Production processor order: BatchSpanProcessor, then optional root flush. Auto-export
    is delayed so only force_flush / on_end moves spans into the sink."""
    provider = TracerProvider()
    exporter = _SpanEnrichmentExporter(sink, lambda _trace_id: enrichment)
    provider.add_span_processor(BatchSpanProcessor(exporter, schedule_delay_millis=60_000))
    if root_flush:
        provider.add_span_processor(_RootFlushSpanProcessor(provider, timeout_millis=2_000))
    return provider


def _job_open_through_shutdown_callback(tracer):
    """LiveKit's ordering at the user shutdown callback: root still open, agent_session
    already ended, job_shutdown still covering the callbacks."""
    root = tracer.start_span("job_entrypoint")
    ctx = trace.set_span_in_context(root)
    with tracer.start_as_current_span("agent_session", context=ctx):
        pass
    shutdown = tracer.start_span("job_shutdown", context=ctx)
    return root, shutdown


def _names(sink) -> set[str]:
    return {span.name for span in sink.get_finished_spans()}


def test_shutdown_in_the_callback_drops_the_late_root():
    """The 1.8.3 regression: shutting the provider in the callback loses job_entrypoint."""
    sink = InMemorySpanExporter()
    enrichment = {"ug.routed_model": "system.ai.gpt-5-5", "ug.voice_mode": "halloween",
                  "ug.expressive_tags": 3}
    provider = _batched_provider(sink, enrichment, root_flush=False)
    tracer = provider.get_tracer("test")
    root, shutdown = _job_open_through_shutdown_callback(tracer)

    fill_ug_metadata(enrichment, None, None)  # callback fills, then...
    flush_open_traces(provider)
    provider.shutdown()  # the old callback

    shutdown.end()
    root.end()

    assert "job_entrypoint" not in _names(sink)
    assert "job_shutdown" not in _names(sink)
    # agent_session was already finished, so the callback flush exported it as an orphan
    assert "agent_session" in _names(sink)


def test_late_root_is_exported_with_enrichment_when_the_callback_does_not_shutdown():
    sink = InMemorySpanExporter()
    enrichment = {}
    provider = _batched_provider(sink, enrichment, root_flush=True)
    tracer = provider.get_tracer("test")
    root, shutdown = _job_open_through_shutdown_callback(tracer)

    fill_ug_metadata(enrichment, SimpleNamespace(
        data_generation_id="G1", company="Acme", tier="VIP", model="system.ai.gpt-5-5"), None,
        voice_mode=voice_state())
    flush_open_traces(provider)  # the new callback: flush only

    shutdown.end()
    root.end()  # RootFlushSpanProcessor force-flushes here

    spans = {span.name: span for span in sink.get_finished_spans()}
    assert set(spans) >= {"job_entrypoint", "job_shutdown", "agent_session"}

    root_attrs = spans["job_entrypoint"].attributes
    assert root_attrs["mlflow.spanType"] == json.dumps("AGENT")
    assert root_attrs["ug.routed_model"] == "system.ai.gpt-5-5"
    assert root_attrs["ug.voice_mode"] == "halloween"
    assert root_attrs["ug.expressive_tags"] == 7
    assert "ug.voice_mode" not in (spans["agent_session"].attributes or {})
    assert "ug.voice_mode" not in (spans["job_shutdown"].attributes or {})
    provider.shutdown()


def test_agent_shutdown_callback_does_not_shut_the_provider():
    text = Path("app/agent.py").read_text()
    assert "flush_open_traces(trace_provider)" in text
    assert "trace_provider.shutdown()" not in text


# ---------------------------------------------------------------------------------------
# livekit-agents 1.8.3 opens job_entrypoint before the entrypoint runs
# ---------------------------------------------------------------------------------------
@pytest.fixture
def lk_tracer():
    """LiveKit's own `_DynamicTracer`, restored afterwards (set_tracer_provider swaps it globally)."""
    from livekit.agents.telemetry import traces
    previous = traces.tracer._tracer_provider
    yield traces.tracer
    traces.tracer.set_provider(previous)


def _ours(sink):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_SpanEnrichmentExporter(sink)))
    return provider


def _run_job(lk_tracer, *, install_ours_before_root: bool, sink):
    """LiveKit's order: the root is started on whatever provider is installed (LiveKit Cloud's when we
    have not installed ours yet), the entrypoint runs under it, the root ends after shutdown."""
    from livekit.agents.telemetry import set_tracer_provider
    cloud_sink = InMemorySpanExporter()
    cloud = TracerProvider()
    cloud.add_span_processor(SimpleSpanProcessor(cloud_sink))
    ours = _ours(sink)
    set_tracer_provider(ours if install_ours_before_root else cloud)

    root = lk_tracer.start_span("job_entrypoint")
    with lk_tracer.use_span(root, end_on_exit=False):
        if not install_ours_before_root:
            set_tracer_provider(ours)          # the old entrypoint
        enrichment = {"ug.voice_mode": "halloween"}
        register_trace_enrichment(enrichment)
        with lk_tracer.start_as_current_span("agent_session"):
            pass
        enrichment["ug.routed_model"] = "system.ai.gpt-5-5"   # filled late, read when the root exports
    root.end()
    return {span.name: span for span in sink.get_finished_spans()}, _names(cloud_sink)


def test_a_provider_set_from_the_entrypoint_never_sees_the_root(lk_tracer):
    sink = InMemorySpanExporter()
    spans, cloud = _run_job(lk_tracer, install_ours_before_root=False, sink=sink)
    assert "job_entrypoint" not in spans and "job_entrypoint" in cloud
    assert spans["agent_session"].parent is not None   # the orphan MLflow cannot list


def test_a_provider_installed_before_the_job_gets_the_enriched_root(lk_tracer):
    sink = InMemorySpanExporter()
    spans, _ = _run_job(lk_tracer, install_ours_before_root=True, sink=sink)
    root = spans["job_entrypoint"]
    assert root.parent is None
    assert spans["agent_session"].parent.span_id == root.context.span_id
    assert root.attributes["ug.voice_mode"] == "halloween"
    assert root.attributes["ug.routed_model"] == "system.ai.gpt-5-5"
    assert root.attributes["mlflow.spanType"] == json.dumps("AGENT")
    assert "ug.voice_mode" not in spans["agent_session"].attributes


def test_root_enrichment_is_per_trace():
    sink = InMemorySpanExporter()
    provider = _ours(sink)
    tracer = provider.get_tracer("test")
    for mode in ("halloween", "standard"):
        with tracer.start_as_current_span("job_entrypoint"):
            register_trace_enrichment({"ug.voice_mode": mode})
    assert [s.attributes["ug.voice_mode"] for s in sink.get_finished_spans()] == ["halloween", "standard"]


def test_agent_installs_the_provider_in_the_process_setup_not_the_entrypoint():
    text = Path("app/agent.py").read_text()
    assert "AgentServer(setup_fnc=_setup_process)" in text
    entrypoint = text.split("async def entrypoint", 1)[1]
    assert "set_tracer_provider(" not in entrypoint
    assert "build_tracer_provider(" not in entrypoint
