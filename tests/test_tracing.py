import json
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.tracing import _SPAN_TYPES, _SpanEnrichmentExporter, fill_ug_metadata
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
    provider.add_span_processor(SimpleSpanProcessor(_SpanEnrichmentExporter(sink, enrichment)))
    tracer = provider.get_tracer("test")
    with ExitStack() as spans:
        for name in names:
            spans.enter_context(tracer.start_as_current_span(name))
    provider.shutdown()
    return {span.name: span.attributes for span in sink.get_finished_spans()}


def test_ai_decide_span_is_typed_chain_through_the_json_encoding_exporter():
    assert _SPAN_TYPES["ug.ai_decide"] == "CHAIN"
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
    controller = VoiceModeController(SimpleNamespace(engine="uaig_chat", label="Fake decide"), profiles,
                                     lambda profile: profile.key)
    controller.state.mode, controller.state.transitions = HALLOWEEN, 1   # production writes these via _transition
    controller.count_tag("whispers")
    controller.mark_degraded("vendor down")   # publishes the snapshot that carries the engine

    enr = {}
    fill_ug_metadata(enr, None, None, voice_mode=controller)
    assert enr == {"ug.voice_mode": "halloween", "ug.voice_mode_transitions": 1, "ug.decide_engine": "uaig_chat",
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
