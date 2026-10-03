"""Governance invariants for Plan 5 (spec §9), as STRUCTURAL tests on the wired configuration, plus the
Task-10 evidence-sink bridge.

  G9  — Mode is independent of tier: `bind` is a frozen dataclass the controller never touches.
  G10 — AI Decide sees only the caller: {current_mode, agent_said, caller_said}, truncated, tags stripped;
        never the tier, model, customer id, dataset prompt, or retrieved documents.
  G11 — The persona can't override governance: build_instructions keeps the governance block last.
  G12 — An exit is always honored: an explicit exit rule beats the engine and is never capped.
  G13 — Expressive tags are never read aloud (an empty vocabulary strips them from the audio path) and never
        shown (StudioAgent.transcription_node strips them from the transcript).

Evidence bridge (the Task-10 test): the controller calls its evidence sink SYNCHRONOUSLY, but the entrypoint's
`_make_evidence_sink` is async, so the wiring bridges them with `app.agent._bridge_sink`
(`lambda frag: asyncio.ensure_future(sink(frag))`). Here we drive a transition AND a degrade through the REAL
async sink + a recording fake room and assert a `{"type":"ug_evidence","voice_mode":{…}}` fragment reaches
topic `ug_evidence` each time — else no voice_mode evidence reaches the UI.

Also pins the two confirmed Task-10 gotchas: StudioAgent takes `fallback_profile=` (NOT `profiles=`, which is a
TypeError), and the 1.8.3 AgentSession transforms kwarg is `tts_text_transforms`.

No live LiveKit / ElevenLabs / ai_decide: fakes + the real pure modules only.
"""
import asyncio
import dataclasses
import inspect
import json
from types import SimpleNamespace

import pytest
from livekit.agents import AgentSession, llm

import app.agent as agent_mod
from app.agent import _make_evidence_sink
from app.expressive import SPOOKY_TAGS, decode_tags, encode_tags
from app.studio_agent import StudioAgent
from app.voice_mode import VoiceModeController
from app.voice_profiles import VoiceProfile, build_tts
from src.agent_prompt import HALLOWEEN_PERSONA, build_instructions
from src.policy.voice_mode import (
    EXIT_THRESHOLD,
    HALLOWEEN,
    MAX_ENTRIES_PER_CALL,
    STANDARD,
    IntentVerdict,
    decide_mode,
    explicit_command,
    resolve_verdict,
)
from src.services.ai_decide import build_ai_decide_body
from src.services.session_bind import BindContext

ENTER = IntentVerdict("enter", 0.95, "model", {"enter": 0.95, "exit": 0.02, "none": 0.03})


# --------------------------------------------------------------------------------------- fakes
class FakeClassifier:
    """Records every `classify` so G10 can assert the engine saw only the three allowed fields."""

    label, engine = "Spy Decide", "ai_decide"

    def __init__(self, verdict=ENTER):
        self._verdict = verdict
        self.calls: list[SimpleNamespace] = []

    async def classify(self, utterance, last_agent_line, current_mode):
        self.calls.append(SimpleNamespace(utterance=utterance, agent_line=last_agent_line, mode=current_mode))
        return self._verdict, 10.0


class FakeSession:
    def __init__(self):
        self.replies: list = []

    def generate_reply(self, *, instructions=None, **kwargs):
        self.replies.append(instructions)
        return SimpleNamespace()


class FakeAgent:
    def __init__(self):
        self.session = FakeSession()
        self.steady: list[str] = []

    async def update_instructions(self, instructions):
        self.steady.append(instructions)


class _ProfileController:
    """A minimal controller stand-in: `transcription_node` only needs `.profile` to construct the agent."""

    def __init__(self, profile):
        self._profile = profile

    @property
    def profile(self):
        return self._profile


class FakeLocalParticipant:
    def __init__(self):
        self.published: list[SimpleNamespace] = []

    async def publish_data(self, data, *, reliable=True, topic=None):
        self.published.append(SimpleNamespace(data=data, reliable=reliable, topic=topic))


class FakeRoom:
    def __init__(self):
        self.local_participant = FakeLocalParticipant()


def make_profiles():
    return {
        "standard": VoiceProfile("standard", "Deepgram · andromeda", "deepgram", frozenset(), None),
        "halloween": VoiceProfile("halloween", "ElevenLabs · v3", "elevenlabs",
                                  frozenset(SPOOKY_TAGS), HALLOWEEN_PERSONA),
        "halloween_fallback": VoiceProfile("halloween_fallback", "Deepgram · zeus", "deepgram",
                                          frozenset(), HALLOWEEN_PERSONA),
    }


def user_msg(text):
    return llm.ChatMessage(role="user", content=[text])


async def _astream(*chunks):
    for chunk in chunks:
        yield chunk


async def _collect(agen):
    return [item async for item in agen]


async def _drain():
    """Let the `ensure_future`-scheduled publish coroutines (the bridge) run to completion."""
    for _ in range(10):
        await asyncio.sleep(0)


# =============================================================================== G9 — mode ⟂ tier
async def test_g9_bind_is_frozen_and_untouched_across_a_transition():
    bind = BindContext(
        data_generation_id="G1", customer_id="C1", company="Acme",
        system_prompt="You are Acme support.", courtesy_name="Grace",
        tier="VIP-SECRET", model="databricks-secret-model",
        directives={"recognition_tone": "warm", "thoroughness": "concise"})
    before = dataclasses.asdict(bind)

    # The controller only ever receives a closure over bind (instructions_for), never bind itself.
    def instructions_for(profile):
        return build_instructions(bind.system_prompt, bind.directives, bind.courtesy_name,
                                  persona=profile.persona,
                                  expressive_tags=tuple(sorted(profile.tags)), voice_requests=True)

    controller = VoiceModeController(FakeClassifier(ENTER), make_profiles(), instructions_for, enabled=True)
    agent = FakeAgent()

    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("make it spooky"))

    assert controller.state.mode == HALLOWEEN          # the voice mode changed...
    assert dataclasses.asdict(bind) == before          # ...but the bind (tier/model/directives) did not

    with pytest.raises(dataclasses.FrozenInstanceError):
        bind.model = "tampered"                        # and bind is immutable by construction


# =============================================================================== G10 — engine sees only caller
async def test_g10_classifier_receives_only_caller_words_agent_line_and_mode():
    # The instructions carry tier/model/customer/dataset-prompt — none of it may reach the engine.
    def instructions_for(profile):
        return "You are Acme support. tier=VIP-SECRET model=databricks-secret-model customer=C1"

    spy = FakeClassifier(ENTER)
    controller = VoiceModeController(spy, make_profiles(), instructions_for, enabled=True)
    agent = FakeAgent()

    controller.note_agent_line("Sure thing. [whispers] How can I help?")   # a tagged agent line
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("make it spooky"))

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call.utterance == "make it spooky"
    assert call.agent_line == "Sure thing. How can I help?"   # [whispers] stripped before the engine sees it
    assert call.mode == STANDARD
    # The ONLY text the engine ever saw is these three arguments; the bind never reaches it.
    seen = f"{call.utterance}|{call.agent_line}|{call.mode}"
    for secret in ("VIP-SECRET", "databricks-secret-model", "customer=C1", "You are Acme support"):
        assert secret not in seen


def test_g10_ai_decide_body_state_has_only_the_three_allowed_fields_truncated():
    caller = "spooky " * 100          # > 300 chars
    agent_line = "x" * 500 + " [whispers] tail"   # > 200 chars, with a tag
    state = build_ai_decide_body(caller.strip(), agent_line, HALLOWEEN)["state"]

    assert set(state) == {"current_mode", "agent_said", "caller_said"}   # nothing else is sent to the engine
    assert state["current_mode"] == HALLOWEEN
    assert len(state["caller_said"]) <= 300        # caller is truncated at the start
    assert len(state["agent_said"]) <= 200         # agent line truncated at the end
    assert "[whispers]" not in state["agent_said"] and "whispers" not in state["agent_said"]  # tag stripped


# =============================================================================== G11 — governance stays last
def test_g11_persona_and_cues_cannot_move_governance_off_the_end():
    instr = build_instructions("DATASET PROMPT", {"recognition_tone": "warm"},
                               persona=HALLOWEEN_PERSONA, expressive_tags=SPOOKY_TAGS, voice_requests=True)
    gov_marker = "Governance (non-negotiable):"

    assert gov_marker in instr
    assert "Halloween persona" in instr and "Expressive cues" in instr
    assert instr.index(gov_marker) > instr.index("Halloween persona")   # governance after the persona
    assert instr.index(gov_marker) > instr.index("Expressive cues")     # and after the cue rules
    # With no courtesy name, the governance block is the final text — nothing optional trails it.
    assert instr.strip().endswith("No lists, asterisks, emojis, or tables.")


# =============================================================================== G12 — an exit always wins
def test_g12_explicit_exit_rule_beats_the_engine():
    confident_enter = IntentVerdict("enter", 0.99, "model")
    rule = explicit_command("stop the spooky voice")
    assert rule is not None and rule.intent == "exit"
    assert resolve_verdict(confident_enter, rule) is rule   # the explicit exit wins over a confident enter


def test_g12_an_exit_is_never_capped_by_the_entry_limit():
    exit_verdict = IntentVerdict("exit", EXIT_THRESHOLD, "model")
    decision = decide_mode(HALLOWEEN, exit_verdict, entries_so_far=MAX_ENTRIES_PER_CALL + 5)
    assert decision.mode_after == STANDARD and decision.reason == "exit"   # the cap counts enters only


# =============================================================================== G13 — tags never aired/shown
async def _audio_chain(vocabulary, *chunks):
    """The tag-handling head and tail of the TTS transform chain (encode … decode) — the two stock string
    filters in between (`filter_markdown`, `filter_emoji`) are resolved by the session and left untouched by
    tags, so the vocabulary alone decides what bracketed text reaches the voice."""
    encode = encode_tags(vocabulary, None)
    return "".join(await _collect(decode_tags(encode(_astream(*chunks)))))


async def test_g13_an_empty_vocabulary_strips_every_tag_from_the_audio_path():
    standard = make_profiles()["standard"]            # the standard profile performs no tags
    out = await _audio_chain(lambda: standard.tags, "Hello ", "[whispers]", " there")
    assert "[" not in out and "whispers" not in out   # the cue never reaches the TTS
    assert "Hello" in out and "there" in out


async def test_g13_only_the_vocabulary_puts_bracketed_text_on_the_air():
    hall = make_profiles()["halloween"]               # performs SPOOKY_TAGS, and only those
    out = await _audio_chain(lambda: hall.tags, "Boo ", "[explodes]", " ha ", "[whispers]", " on")
    assert "[explodes]" not in out and "explodes" not in out   # an unknown tag is dropped (fail closed)
    assert "[whispers]" in out                                 # the one allowed cue survives for the voice


async def test_g13_tags_never_reach_the_transcript_in_any_mode():
    profiles = make_profiles()
    for key in ("halloween", "standard"):
        agent = StudioAgent(_ProfileController(profiles[key]), build_tts_fn=lambda p: None, instructions="t")
        out = "".join(await _collect(agent.transcription_node(
            _astream("Welcome ", "[whispers]", " to the ", "[laughs]", "haunted house"), None)))
        assert "[" not in out and "]" not in out
        assert "whispers" not in out and "laughs" not in out
        assert "Welcome" in out and "haunted house" in out


# =============================================================================== the evidence-sink bridge
async def test_evidence_sink_bridge_publishes_voice_mode_on_transition_and_on_degrade():
    room = FakeRoom()
    session_ctx = SimpleNamespace(last_retrieval=None)
    evidence_async = _make_evidence_sink(room, session_ctx)     # the REAL async sink from app/agent.py
    controller_sink = agent_mod._bridge_sink(evidence_async)    # the Task-10 sync→async bridge

    controller = VoiceModeController(FakeClassifier(ENTER), make_profiles(), lambda p: "INSTR",
                                     evidence_sink=controller_sink, enabled=True)
    agent = FakeAgent()

    # 1) a same-turn transition publishes a voice_mode fragment
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("make it spooky"))
    await _drain()
    assert controller.state.mode == HALLOWEEN

    # 2) a degrade publishes another snapshot
    controller.mark_degraded("elevenlabs 500")
    await _drain()

    published = room.local_participant.published
    assert [p.topic for p in published] == ["ug_evidence", "ug_evidence"]   # both reached the data channel
    payloads = [json.loads(p.data.decode("utf-8")) for p in published]
    for payload in payloads:
        assert payload["type"] == "ug_evidence"
        assert "voice_mode" in payload
    assert payloads[0]["voice_mode"]["path"] == "same_turn"
    assert payloads[0]["voice_mode"]["mode"] == "halloween"
    assert payloads[1]["voice_mode"]["degraded"] is True            # the degrade snapshot
    assert payloads[1]["voice_mode"]["voice"] == "Deepgram · zeus"  # routed to the fallback voice


def test_bridge_sink_is_callable_synchronously_and_schedules_the_async_sink():
    # The controller invokes the sink synchronously; the bridge must not itself be a coroutine function.
    assert not inspect.iscoroutinefunction(agent_mod._bridge_sink(lambda frag: None))


# =============================================================================== the two confirmed gotchas
def test_studioagent_is_built_with_fallback_profile_not_a_profiles_kwarg():
    profiles = make_profiles()
    controller = VoiceModeController(FakeClassifier(), profiles, lambda p: "INSTR")

    agent = StudioAgent(instructions=controller.instructions(), controller=controller,
                        fallback_profile=profiles["halloween_fallback"], build_tts_fn=build_tts)
    assert agent._fallback_profile is profiles["halloween_fallback"]

    with pytest.raises(TypeError):   # the plan's line ~355 (`profiles=`) is a TypeError, not valid wiring
        StudioAgent(instructions="x", controller=controller, profiles=profiles)


def test_agentsession_transforms_kwarg_is_tts_text_transforms_at_1_8_3():
    params = inspect.signature(AgentSession.__init__).parameters
    assert "tts_text_transforms" in params   # the confirmed 1.8.3 kwarg name the wiring uses
    assert "expressive" in params            # and the native expressive subsystem is explicitly set off
