"""Voice-mode controller (spec §7.2 / §7.3 / §7.4).

The controller orchestrates one turn: it starts the decision in the background on the final transcript
(`prefetch`), then at the turn boundary (`on_turn`) it waits for the verdict within the cue budget, resolves
it against the explicit-command rule, and applies the mode switch either in the same reply (same-turn) or in
a short announcement right after it (announced). It is the single writer of `state.mode`, and `on_turn` never
raises (a raise would drop the turn).

No LiveKit session, no network. The classifier, the agent, the profiles and the sinks are fakes; the
same-turn patch is asserted against a REAL `livekit.agents.llm.ChatContext` through the 1.8.3
`update_instructions` helper (the one production uses), so the test pins the actual integration point.
"""
import asyncio
import json
import time
from types import SimpleNamespace

from livekit.agents import llm
from livekit.agents.voice.generation import INSTRUCTIONS_MESSAGE_ID

from app.expressive import SPOOKY_TAGS
from app.voice_mode import VoiceModeController, VoiceModeState
from app.voice_profiles import VoiceProfile
from src.agent_prompt import ANNOUNCE_OFF, ANNOUNCE_ON, HALLOWEEN_PERSONA, OFF_NOTE, ON_NOTE
from src.policy.voice_mode import HALLOWEEN, STANDARD, IntentVerdict

# ---------------------------------------------------------------------------------------
# Verdicts the fake engine can return (source "model": it is what resolve_verdict acts on)
# ---------------------------------------------------------------------------------------
ENTER = IntentVerdict("enter", 0.9, "model", {"enter": 0.9, "exit": 0.03, "none": 0.07})
EXIT = IntentVerdict("exit", 0.8, "model", {"enter": 0.05, "exit": 0.8, "none": 0.15})
NONE = IntentVerdict("none", 0.2, "model", {"enter": 0.1, "exit": 0.1, "none": 0.8})

EVIDENCE_KEYS = {"mode", "voice", "decided_by", "engine", "path", "confidence", "probabilities",
                 "latency_ms", "reason", "degraded"}


# ---------------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------------
class FakeClassifier:
    """Stands in for DecideClient. Records each `classify` (so G10 — only caller/agent-line/mode — can be
    asserted) and replies from a script: the n-th call uses the n-th response, the last repeating."""

    def __init__(self, *responses, label="Fake Decide", engine="ai_decide"):
        self.label, self.engine = label, engine
        self._responses = list(responses) or [{"verdict": NONE}]
        self.calls: list[SimpleNamespace] = []

    async def classify(self, utterance, last_agent_line, current_mode):
        self.calls.append(SimpleNamespace(utterance=utterance, agent_line=last_agent_line, mode=current_mode))
        r = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if delay := r.get("delay", 0.0):
            await asyncio.sleep(delay)
        if r.get("raises") is not None:
            raise r["raises"]
        return r["verdict"], r.get("latency_ms", 12.0)


class FakeSession:
    def __init__(self):
        self.replies: list[str | None] = []

    def generate_reply(self, *, instructions=None, **kwargs):
        self.replies.append(instructions)
        return SimpleNamespace()  # a stand-in SpeechHandle; the controller never awaits it


class FakeAgent:
    def __init__(self):
        self.session = FakeSession()
        self.steady: list[str] = []  # instructions pushed for LATER turns (update_instructions)

    async def update_instructions(self, instructions):
        self.steady.append(instructions)


class EvidenceSink:
    def __init__(self):
        self.payloads: list[dict] = []

    def __call__(self, payload):
        self.payloads.append(payload)


class FakeSpan:
    def __init__(self, name):
        self.name, self.attributes = name, {}

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTracer:
    def __init__(self):
        self.spans: list[FakeSpan] = []

    def start_as_current_span(self, name):
        span = FakeSpan(name)
        self.spans.append(span)
        return span


# ---------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------
def make_profiles():
    return {
        "standard": VoiceProfile("standard", "Deepgram · andromeda", "deepgram", frozenset(), None),
        "halloween": VoiceProfile("halloween", "ElevenLabs · v3", "elevenlabs",
                                  frozenset(SPOOKY_TAGS), HALLOWEEN_PERSONA),
        "halloween_fallback": VoiceProfile("halloween_fallback", "Deepgram · zeus", "deepgram",
                                          frozenset(), HALLOWEEN_PERSONA),
    }


def instructions_for(profile):
    """A recognisable stand-in for build_instructions(..): the per-mode steady instructions."""
    return f"INSTR[{profile.key}]"


def make_controller(classifier, *, enabled=True, cue_wait_s=0.2, on_cue=None, evidence_sink=None,
                    tracer=None, mode=STANDARD):
    controller = VoiceModeController(
        classifier, make_profiles(), instructions_for,
        evidence_sink=evidence_sink, tracer=tracer, on_cue=on_cue, enabled=enabled, cue_wait_s=cue_wait_s)
    if mode != STANDARD:
        controller.state.mode = mode  # seed the precondition; production only writes mode via _transition
    return controller


def user_msg(text):
    return llm.ChatMessage(role="user", content=[text])


# ---------------------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------------------
async def test_disabled_makes_no_classify_calls_and_stays_standard():
    fake = FakeClassifier({"verdict": ENTER})
    controller = make_controller(fake, enabled=False)
    agent = FakeAgent()

    controller.prefetch("Can you do a spooky voice")
    await asyncio.sleep(0.01)
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("Can you do a spooky voice"))

    assert fake.calls == []                      # zero calls to the engine
    assert controller.state.mode == STANDARD     # never leaves standard
    assert controller.state.transitions == 0
    assert agent.session.replies == []


# ---------------------------------------------------------------------------------------
# prefetch: multi-segment accumulation + reuse
# ---------------------------------------------------------------------------------------
async def test_prefetch_accumulates_segments_and_on_turn_reuses_the_matching_answer():
    fake = FakeClassifier({"verdict": ENTER})
    controller = make_controller(fake)
    agent = FakeAgent()

    controller.prefetch("Can you do a spooky voice")
    await asyncio.sleep(0.01)                     # STT segment 1 processed; the first classify runs
    controller.prefetch("Also, where is my order?")  # the committed text grows -> re-ask for all of it
    await asyncio.sleep(0.01)

    full = "Can you do a spooky voice Also, where is my order?"
    assert fake.calls[-1].utterance == full       # the engine was asked for the GROWING text
    asked = len(fake.calls)

    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg(full))

    assert len(fake.calls) == asked               # on_turn reused the prefetched answer: no new classify
    assert controller.state.mode == HALLOWEEN      # and consumed its verdict


async def test_on_turn_reasks_when_committed_text_differs_from_the_prefetch():
    fake = FakeClassifier({"verdict": ENTER})
    controller = make_controller(fake)
    agent = FakeAgent()

    controller.prefetch("make it spooky")
    await asyncio.sleep(0.01)
    asked = len(fake.calls)

    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("make it super spooky please"))

    assert len(fake.calls) == asked + 1           # committed text differs -> the engine is asked again
    assert fake.calls[-1].utterance == "make it super spooky please"
    assert controller.state.mode == HALLOWEEN


# ---------------------------------------------------------------------------------------
# The cue budget: wait on a cue, skip the wait otherwise, and cue==has_cue OR mode==halloween
# ---------------------------------------------------------------------------------------
async def test_cue_wait_is_capped_and_a_slow_answer_defers_to_the_announced_path():
    fake = FakeClassifier({"verdict": ENTER, "delay": 0.4})   # slower than the cue budget
    on_cue_calls = []
    controller = make_controller(fake, cue_wait_s=0.05, on_cue=lambda: on_cue_calls.append(1))
    agent = FakeAgent()

    # A cue that is NOT an explicit command: the engine (not the rule) must drive it, so a slow engine
    # genuinely defers rather than letting the explicit-rule safety net switch same-turn.
    start = time.perf_counter()
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("change your voice to something creepy"))
    elapsed = time.perf_counter() - start

    assert elapsed < 0.3                          # capped at the cue budget, not the engine's 0.4 s
    assert on_cue_calls == [1]                    # a cue prewarmed the Halloween TTS
    assert controller.state.mode == STANDARD       # not switched in-turn (the answer wasn't in yet)
    assert controller._late_task is not None       # but handed to the announced path
    await controller.aclose()


async def test_non_cue_turn_skips_the_wait_entirely():
    fake = FakeClassifier({"verdict": NONE, "delay": 0.4})
    on_cue_calls = []
    controller = make_controller(fake, cue_wait_s=0.05, on_cue=lambda: on_cue_calls.append(1))
    agent = FakeAgent()

    start = time.perf_counter()
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("where is my order"))
    elapsed = time.perf_counter() - start

    assert elapsed < 0.05                          # did not wait for the engine
    assert on_cue_calls == []                       # no cue, no prewarm
    assert controller.state.mode == STANDARD
    await controller.aclose()


async def test_cue_is_true_in_halloween_even_without_has_cue():
    # A terse "stop" is not a has_cue match, but in Halloween it must still get the cue budget (G-exit).
    fake = FakeClassifier({"verdict": EXIT, "delay": 0.4})
    on_cue_calls = []
    controller = make_controller(fake, cue_wait_s=0.05, on_cue=lambda: on_cue_calls.append(1), mode=HALLOWEEN)
    agent = FakeAgent()

    start = time.perf_counter()
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("stop"))
    elapsed = time.perf_counter() - start

    assert on_cue_calls == [1]                      # cue == (has_cue OR mode==halloween) -> prewarm fired
    assert 0.04 < elapsed < 0.3                      # and the turn waited the cue budget
    await controller.aclose()


# ---------------------------------------------------------------------------------------
# Same-turn switch: real ChatContext patch + steady update_instructions
# ---------------------------------------------------------------------------------------
async def test_same_turn_enter_patches_a_real_chatcontext_and_updates_instructions():
    fake = FakeClassifier({"verdict": ENTER})
    controller = make_controller(fake)
    agent = FakeAgent()
    turn_ctx = llm.ChatContext.empty()             # a REAL livekit ChatContext

    await controller.on_turn(agent, turn_ctx, user_msg("make it spooky"))

    assert controller.state.mode == HALLOWEEN
    assert controller.state.transitions == 1
    assert controller.state.entries == 1

    # THIS reply: turn_ctx patched via the 1.8.3 helper with steady + the ON note
    patched = turn_ctx.get_by_id(INSTRUCTIONS_MESSAGE_ID)
    assert patched is not None
    assert "INSTR[halloween]" in patched.text_content
    assert ON_NOTE in patched.text_content

    # LATER turns: steady instructions set on the agent, WITHOUT the one-reply note
    assert agent.steady == ["INSTR[halloween]"]
    assert ON_NOTE not in agent.steady[0]
    assert agent.session.replies == []             # same-turn: no separate generate_reply


async def test_same_turn_exit_uses_the_off_note():
    fake = FakeClassifier({"verdict": EXIT})
    controller = make_controller(fake, mode=HALLOWEEN)
    agent = FakeAgent()
    turn_ctx = llm.ChatContext.empty()

    await controller.on_turn(agent, turn_ctx, user_msg("go back to your normal voice"))

    assert controller.state.mode == STANDARD
    patched = turn_ctx.get_by_id(INSTRUCTIONS_MESSAGE_ID)
    assert "INSTR[standard]" in patched.text_content
    assert OFF_NOTE in patched.text_content
    assert agent.steady == ["INSTR[standard]"]
    assert controller.state.entries == 0           # entries count enters only


# ---------------------------------------------------------------------------------------
# Announced switch + the turn_seq race
# ---------------------------------------------------------------------------------------
async def test_announced_enter_calls_generate_reply_once_with_announce_on():
    fake = FakeClassifier({"verdict": ENTER, "delay": 0.1})   # answers just after the cue budget
    controller = make_controller(fake, cue_wait_s=0.02)
    agent = FakeAgent()

    # cue but not an explicit command, so the (slow) engine's answer drives the switch, not the rule
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("change your voice to something creepy"))
    assert controller.state.mode == STANDARD        # the reply went out in the current mode

    await controller._late_task                      # the answer lands after the reply started

    assert controller.state.mode == HALLOWEEN         # applied as an announced switch
    assert agent.session.replies == [ANNOUNCE_ON]     # exactly one announcement, ANNOUNCE_ON
    assert agent.steady == ["INSTR[halloween]"]       # steady updated for later turns too
    assert controller.state.transitions == 1


async def test_late_answer_after_a_newer_turn_is_dropped():
    # Turn 1's engine is slow; before it lands, turn 2 starts (turn_seq advances) -> the answer is dropped.
    fake = FakeClassifier({"verdict": ENTER, "delay": 0.2},   # turn 1: slow enter
                          {"verdict": NONE})                   # turn 2: immediate none
    sink = EvidenceSink()
    controller = make_controller(fake, cue_wait_s=0.02, evidence_sink=sink)
    agent = FakeAgent()

    # turn 1: a cue the engine answers slowly (not an explicit command) -> deferred to the announced path
    await controller.on_turn(agent, llm.ChatContext.empty(),
                             user_msg("change your voice to something creepy"))
    first_late = controller._late_task

    controller.prefetch("where is my order")          # turn 2's transcript
    await asyncio.sleep(0.01)
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("where is my order"))  # turn_seq -> 2

    await first_late                                   # turn 1's answer finally lands

    assert controller.state.mode == STANDARD           # never applied
    assert agent.session.replies == []                 # no announcement
    assert sink.payloads[-1]["voice_mode"]["path"] == "late_dropped"
    await controller.aclose()


# ---------------------------------------------------------------------------------------
# resolve_verdict carry-forwards: G12 exit wins; the STANDARD exit-rule backstop
# ---------------------------------------------------------------------------------------
async def test_explicit_exit_rule_beats_the_engine_in_halloween():
    # The engine says "enter" with high confidence, but the caller explicitly asked to stop (G12).
    fake = FakeClassifier({"verdict": IntentVerdict("enter", 0.99, "model")})
    controller = make_controller(fake, mode=HALLOWEEN)
    agent = FakeAgent()

    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("stop the spooky voice"))

    assert controller.state.mode == STANDARD           # the explicit exit wins over the engine


async def test_standard_drops_the_exit_rule_so_an_engine_enter_is_not_shadowed():
    # In STANDARD a spurious exit-rule match would, via resolve_verdict's G12 priority, shadow the engine's
    # enter (an exit is a no-op in STANDARD anyway). The controller drops the rule exit before resolving.
    fake = FakeClassifier({"verdict": ENTER})
    controller = make_controller(fake)                 # STANDARD
    agent = FakeAgent()

    # "go back to your normal voice" is an explicit EXIT rule AND a has_cue; the engine says enter.
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("go back to your normal voice"))

    assert controller.state.mode == HALLOWEEN           # the enter was NOT shadowed by the dropped exit rule


# ---------------------------------------------------------------------------------------
# on_turn never raises
# ---------------------------------------------------------------------------------------
async def test_on_turn_swallows_exceptions_and_the_turn_continues():
    fake = FakeClassifier({"verdict": ENTER, "raises": RuntimeError("engine blew up")})
    controller = make_controller(fake, cue_wait_s=0.1)
    agent = FakeAgent()

    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("make it spooky"))  # must not raise

    assert controller.state.mode == STANDARD            # no transition on failure
    assert controller.state.transitions == 0


# ---------------------------------------------------------------------------------------
# Governance: evidence is PII-free (§7.2); the engine sees only the three allowed fields (G10)
# ---------------------------------------------------------------------------------------
async def test_transition_evidence_has_no_transcript_or_pii():
    fake = FakeClassifier({"verdict": ENTER})
    sink = EvidenceSink()
    controller = make_controller(fake, evidence_sink=sink)
    agent = FakeAgent()

    await controller.on_turn(agent, llm.ChatContext.empty(),
                             user_msg("Can you do a spooky voice? Also, where is my order?"))

    assert len(sink.payloads) == 1
    voice_mode = sink.payloads[0]["voice_mode"]
    assert set(voice_mode) == EVIDENCE_KEYS             # exactly the §7.2 fields, nothing more
    assert voice_mode["mode"] == "halloween"
    assert voice_mode["voice"] == "ElevenLabs · v3"
    assert voice_mode["decided_by"] == "Fake Decide"
    assert voice_mode["engine"] == "ai_decide"
    assert voice_mode["path"] == "same_turn"
    assert voice_mode["reason"] == "enter"
    assert voice_mode["confidence"] == 0.9
    assert voice_mode["probabilities"] == {"enter": 0.9, "exit": 0.03, "none": 0.07}
    assert voice_mode["degraded"] is False

    blob = json.dumps(sink.payloads).lower()            # no caller words / order text anywhere
    for leaked in ("order", "spooky", "can you"):
        assert leaked not in blob


async def test_classifier_receives_only_caller_words_agent_line_and_mode():
    fake = FakeClassifier({"verdict": ENTER})
    controller = make_controller(fake)
    agent = FakeAgent()

    controller.note_agent_line("Sure thing. [whispers] How can I help?")  # tags are stripped for the decider
    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("make it spooky"))

    call = fake.calls[-1]
    assert call.utterance == "make it spooky"
    assert call.agent_line == "Sure thing. How can I help?"   # strip_tags applied, no [whispers]
    assert call.mode == STANDARD
    # The classify signature carries only these three; nothing else (no tier/name/customer) can reach it.


# ---------------------------------------------------------------------------------------
# mark_degraded (§7.4: sets only `degraded`; §7.10: publishes evidence + routes to fallback)
# ---------------------------------------------------------------------------------------
async def test_mark_degraded_sets_only_degraded_and_routes_to_the_fallback_voice():
    sink = EvidenceSink()
    controller = make_controller(FakeClassifier(), evidence_sink=sink, mode=HALLOWEEN)
    assert controller.profile.key == "halloween"

    controller.mark_degraded("elevenlabs 500")

    assert controller.state.degraded is True
    assert controller.state.mode == HALLOWEEN           # untouched
    assert controller.state.entries == 0                # untouched
    assert controller.state.transitions == 0            # untouched
    assert controller.profile.key == "halloween_fallback"   # now routes to the dark Deepgram voice
    assert controller.vocabulary() == frozenset()        # which performs no tags
    assert sink.payloads[-1]["voice_mode"]["degraded"] is True
    assert sink.payloads[-1]["voice_mode"]["voice"] == "Deepgram · zeus"


async def test_mark_degraded_is_idempotent():
    sink = EvidenceSink()
    controller = make_controller(FakeClassifier(), evidence_sink=sink, mode=HALLOWEEN)
    controller.mark_degraded("boom")
    controller.mark_degraded("boom again")
    assert len(sink.payloads) == 1                      # published once


# ---------------------------------------------------------------------------------------
# Readers that track the mode; the span; and the tag counter
# ---------------------------------------------------------------------------------------
async def test_profile_vocabulary_and_instructions_track_the_mode():
    controller = make_controller(FakeClassifier())
    assert controller.profile.key == "standard"
    assert controller.vocabulary() == frozenset()
    assert controller.instructions() == "INSTR[standard]"

    controller.state.mode = HALLOWEEN
    assert controller.profile.key == "halloween"
    assert controller.vocabulary() == frozenset(SPOOKY_TAGS)
    assert controller.instructions() == "INSTR[halloween]"


async def test_count_tag_increments_tags_spoken():
    controller = make_controller(FakeClassifier())
    controller.count_tag("whispers")
    controller.count_tag("sighs")
    assert controller.state.tags_spoken == 2


async def test_transition_emits_the_ug_ai_decide_span_and_is_a_noop_without_a_tracer():
    tracer = FakeTracer()
    controller = make_controller(FakeClassifier({"verdict": ENTER}), tracer=tracer)
    agent = FakeAgent()

    await controller.on_turn(agent, llm.ChatContext.empty(), user_msg("make it spooky"))

    assert [s.name for s in tracer.spans] == ["ug.ai_decide"]
    attrs = tracer.spans[0].attributes
    assert attrs["ug.voice_mode.before"] == "standard"
    assert attrs["ug.voice_mode.after"] == "halloween"
    assert attrs["ug.decide.path"] == "same_turn"
    assert attrs["ug.decide.intent"] == "enter"

    # tracer=None path must not raise
    quiet = make_controller(FakeClassifier({"verdict": ENTER}))
    await quiet.on_turn(FakeAgent(), llm.ChatContext.empty(), user_msg("make it spooky"))
    assert quiet.state.mode == HALLOWEEN


def test_state_defaults_are_one_per_call():
    state = VoiceModeState()
    assert state.mode == STANDARD
    assert state.entries == 0 and state.transitions == 0
    assert state.degraded is False
    assert state.last_agent_line == ""
    assert state.tags_spoken == 0 and state.turn_seq == 0
    assert state.last is None
