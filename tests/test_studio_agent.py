"""StudioAgent node overrides (spec §7.1 / §7.7) — the LiveKit audio-path layer.

Three overrides are exercised with fakes (no network, no AgentSession):
  - `tts_node` routes by the ACTIVE profile, snapshotted ONCE at the start of the utterance: standard
    delegates to `Agent.default.tts_node` (the session's own TTS); any other profile streams through its
    own TTS and, on ANY vendor error, degrades to the EXPLICIT fallback (Deepgram dark voice) WITHOUT
    raising — and, because that voice performs no tags, with the degrade-path text tag-stripped (G13).
  - `transcription_node` strips expressive tags for every profile so a `[tag]` never shows.
  - `on_user_turn_completed` hands the turn to the controller.

A caller cancellation (`CancelledError`) is NOT a vendor error: it propagates and does not degrade.

Fakes: a controller exposing `.profile` / `.mark_degraded` / `.on_turn`; streaming TTS instances that
honor push_text/end_input and yield frames (optionally failing before, after N frames, or blocking on a
gate); and a spy on `Agent.default.tts_node`. The profile-owned TTS lifecycle (error/metrics subscription,
aclose) is Task 10's wiring job and is out of scope here.
"""
import asyncio
from types import SimpleNamespace

import pytest
from livekit.agents import Agent

import app.studio_agent as sa
from app.expressive import SPOOKY_TAGS
from app.studio_agent import StudioAgent
from app.voice_profiles import VoiceProfile
from src.agent_prompt import HALLOWEEN_PERSONA

# --------------------------------------------------------------------------------------- profiles
STANDARD = VoiceProfile("standard", "Deepgram · andromeda", "deepgram", frozenset(), None)
HALLOWEEN = VoiceProfile("halloween", "ElevenLabs · v3", "elevenlabs", frozenset(SPOOKY_TAGS), HALLOWEEN_PERSONA)
FALLBACK = VoiceProfile("halloween_fallback", "Deepgram · zeus", "deepgram", frozenset(), HALLOWEEN_PERSONA)


# --------------------------------------------------------------------------------------- fakes
class FakeController:
    """Stands in for VoiceModeController: `.profile` returns the active profile (and counts reads, so the
    per-utterance snapshot can be asserted — the degrade path must NOT re-read it); records
    `mark_degraded`/`on_turn`."""

    def __init__(self, profile):
        self._profile = profile
        self.degraded = False
        self.degrade_reasons: list[str] = []
        self.on_turn_args: list[tuple] = []
        self.profile_reads = 0

    @property
    def profile(self):
        self.profile_reads += 1
        return self._profile

    def mark_degraded(self, reason):
        self.degraded = True
        self.degrade_reasons.append(reason)

    async def on_turn(self, agent, turn_ctx, new_message):
        self.on_turn_args.append((agent, turn_ctx, new_message))


class FakeStream:
    """A SynthesizeStream stand-in: push_text buffers tokens, end_input unblocks iteration, and iteration
    yields one `.frame` per configured frame — or fails before any frame (`fail`), after N frames
    (`fail_after`), and/or blocks on a `gate` after each frame (to test mid-stream cancellation)."""

    def __init__(self, parent):
        self._parent = parent
        self._ready = asyncio.Event()

    def push_text(self, token):
        self._parent.pushed.append(token)

    def end_input(self):
        self._ready.set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._parent.stream_exited = True
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        await self._ready.wait()                 # mirror the real node: text is forwarded concurrently
        if self._parent.on_iter is not None:
            self._parent.on_iter()               # a hook to flip mode mid-utterance (snapshot test)
        if self._parent.fail:
            raise RuntimeError("vendor stream exploded")
        for i, frame in enumerate(self._parent.frames):
            yield SimpleNamespace(frame=frame)
            if self._parent.gate is not None:
                await self._parent.gate.wait()   # block so a consumer can be cancelled mid-stream
            if self._parent.fail_after is not None and (i + 1) >= self._parent.fail_after:
                raise RuntimeError("vendor stream exploded mid-synthesis")


class FakeTTS:
    """A streaming TTS stand-in. `streaming=False` forces the StreamAdapter branch. Records pushed text and
    the conn_options it was handed."""

    def __init__(self, frames=(), *, streaming=True, fail=False, fail_after=None, on_iter=None, gate=None):
        self.capabilities = SimpleNamespace(streaming=streaming, aligned_transcript=False)
        self.frames = list(frames)
        self.fail = fail
        self.fail_after = fail_after
        self.on_iter = on_iter
        self.gate = gate
        self.pushed: list[str] = []
        self.conn_options: list = []
        self.closed = False
        self.stream_exited = False

    def stream(self, *, conn_options=None):
        self.conn_options.append(conn_options)
        return FakeStream(self)

    async def aclose(self):
        self.closed = True


def make_build_tts(mapping):
    """A build_tts stand-in: returns the fake TTS for a profile key, or raises the mapped exception.
    Records the keys it was asked to build so build-once (caching) can be asserted."""
    calls: list[str] = []

    def build(profile):
        calls.append(profile.key)
        value = mapping[profile.key]
        if isinstance(value, Exception):
            raise value
        return value

    build.calls = calls
    return build


async def atext(*chunks):
    for chunk in chunks:
        yield chunk


async def collect(agen):
    return [item async for item in agen]


async def _until(pred, *, tries=400, delay=0.005):
    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(delay)
    raise AssertionError("condition not met in time")


def make_agent(controller, mapping, *, fallback_profile=None):
    return StudioAgent(controller, build_tts_fn=make_build_tts(mapping),
                       fallback_profile=fallback_profile, instructions="test")


# =============================================================================== standard: delegate
async def test_standard_profile_delegates_to_default_tts_node(monkeypatch):
    """standard → the session's own TTS via Agent.default.tts_node; the profile TTS is never built."""
    seen = {}

    async def spy(agent, text, model_settings):
        seen["agent"] = agent
        seen["model_settings"] = model_settings
        seen["text"] = [chunk async for chunk in text]
        yield "SESSION_FRAME"

    monkeypatch.setattr(Agent.default, "tts_node", staticmethod(spy))
    controller = FakeController(STANDARD)
    build = make_build_tts({})
    agent = StudioAgent(controller, build_tts_fn=build, instructions="test")

    sentinel = object()
    frames = await collect(agent.tts_node(atext("Hi ", "there"), sentinel))

    assert frames == ["SESSION_FRAME"]              # the session TTS produced the audio
    assert seen["agent"] is agent                   # delegated with self
    assert seen["model_settings"] is sentinel       # model_settings forwarded untouched
    assert seen["text"] == ["Hi ", "there"]         # the text stream forwarded
    assert build.calls == []                        # the profile TTS was NOT built for standard


# =============================================================================== halloween: stream
async def test_halloween_profile_streams_through_profile_tts():
    controller = FakeController(HALLOWEEN)
    tts = FakeTTS(frames=["h1", "h2", "h3"])
    agent = make_agent(controller, {"halloween": tts})

    frames = await collect(agent.tts_node(atext("Boo ", "ha"), None))

    assert frames == ["h1", "h2", "h3"]             # profile TTS produced the audio
    assert tts.pushed == ["Boo ", "ha"]             # every chunk forwarded via push_text, then end_input
    assert controller.degrade_reasons == []         # no degrade on the happy path
    assert tts.closed is False                      # lifecycle (aclose) is Task 10's job, not tts_node's


async def test_profile_tts_uses_tight_connect_options():
    """A hung vendor must degrade fast: APIConnectOptions(max_retry=1, timeout=5.0) (spec §7.7)."""
    controller = FakeController(HALLOWEEN)
    tts = FakeTTS(frames=["x"])
    agent = make_agent(controller, {"halloween": tts})

    await collect(agent.tts_node(atext("hey"), None))

    assert len(tts.conn_options) == 1
    assert tts.conn_options[0].max_retry == 1
    assert tts.conn_options[0].timeout == 5.0


async def test_profile_tts_is_built_once_across_utterances():
    """Build-once: the TTS instance is cached by profile key, not rebuilt per utterance."""
    controller = FakeController(HALLOWEEN)
    tts = FakeTTS(frames=["a"])
    build = make_build_tts({"halloween": tts})
    agent = StudioAgent(controller, build_tts_fn=build, instructions="test")

    await collect(agent.tts_node(atext("one"), None))
    await collect(agent.tts_node(atext("two"), None))

    assert build.calls == ["halloween"]             # built exactly once
    assert tts.pushed == ["one", "two"]             # same instance reused


# =============================================================================== degrade (never raise)
async def test_vendor_error_degrades_without_raising_and_fallback_continues():
    """A vendor error on the profile TTS → mark_degraded + fallback audio, and NO exception escapes."""
    controller = FakeController(HALLOWEEN)
    boom = FakeTTS(frames=["never"], fail=True)
    fallback = FakeTTS(frames=["f1", "f2"])
    agent = make_agent(controller, {"halloween": boom, "halloween_fallback": fallback},
                       fallback_profile=FALLBACK)

    frames = await collect(agent.tts_node(atext("spooky ", "please"), None))

    assert controller.degraded is True                      # degrade latched
    assert len(controller.degrade_reasons) == 1             # marked exactly once
    assert frames == ["f1", "f2"]                           # fallback audio continued
    assert fallback.pushed == ["spooky ", "please"]         # the full utterance text reached the fallback
    assert controller.profile_reads == 1                    # L2: snapshot only — no re-read on degrade


async def test_degrade_to_a_tagless_fallback_strips_tags_so_deepgram_never_speaks_them():
    """G13 / M1a: the decoded [whispers]/[laughs] bound for ElevenLabs must NOT reach the tag-less Deepgram
    fallback as text (Deepgram would read them aloud). The degrade-path text is stripped."""
    controller = FakeController(HALLOWEEN)
    boom = FakeTTS(frames=["x"], fail=True)
    fallback = FakeTTS(frames=["f1"])
    agent = make_agent(controller, {"halloween": boom, "halloween_fallback": fallback},
                       fallback_profile=FALLBACK)

    frames = await collect(agent.tts_node(atext("Welcome ", "[whispers]", " friend ", "[laughs]"), None))

    assert frames == ["f1"]
    joined = "".join(fallback.pushed)
    assert "[" not in joined and "]" not in joined          # no bracketed cue reaches Deepgram
    assert "whispers" not in joined and "laughs" not in joined
    assert "Welcome" in joined and "friend" in joined       # the real words still spoken


async def test_degrade_does_not_replay_frames_already_played():
    """M1c: if the profile voice already played ≥1 frame before dying, the fallback must not re-speak the
    buffered text (no double audio); the un-played tail may be lost (accepted, spec §8)."""
    controller = FakeController(HALLOWEEN)
    primary = FakeTTS(frames=["p1", "p2"], fail_after=1)    # plays one frame, then the vendor dies
    fallback = FakeTTS(frames=[])
    agent = make_agent(controller, {"halloween": primary, "halloween_fallback": fallback},
                       fallback_profile=FALLBACK)

    frames = await collect(agent.tts_node(atext("one ", "two ", "three"), None))

    assert frames == ["p1"]                 # the already-played frame; NOT re-spoken
    assert controller.degraded is True
    assert fallback.pushed == []            # the buffered (already-played) text was not replayed


@pytest.mark.parametrize("exc", [ValueError("missing voice id"), ImportError("no plugin")])
async def test_build_tts_failure_degrades_to_fallback(exc):
    """build_tts raising ValueError/ImportError is also a degrade, not a crash (spec §7.7)."""
    controller = FakeController(HALLOWEEN)
    fallback = FakeTTS(frames=["only-fallback"])
    agent = make_agent(controller, {"halloween": exc, "halloween_fallback": fallback},
                       fallback_profile=FALLBACK)

    frames = await collect(agent.tts_node(atext("make ", "it ", "spooky"), None))

    assert controller.degraded is True
    assert frames == ["only-fallback"]
    assert fallback.pushed == ["make ", "it ", "spooky"]    # the whole utterance still spoken, by the fallback


async def test_tts_node_never_raises_even_if_the_fallback_also_fails():
    """Belt-and-braces: even if the fallback TTS fails too, tts_node yields nothing rather than raising
    (a vendor outage must never crash the audio path or the session)."""
    controller = FakeController(HALLOWEEN)
    boom = FakeTTS(frames=["x"], fail=True)
    boom_fallback = FakeTTS(frames=["y"], fail=True)
    agent = make_agent(controller, {"halloween": boom, "halloween_fallback": boom_fallback},
                       fallback_profile=FALLBACK)

    frames = await collect(agent.tts_node(atext("uh oh"), None))   # must not raise

    assert frames == []
    assert controller.degraded is True


async def test_degrade_targets_the_explicit_fallback_even_after_a_midutterance_flip():
    """L2: the degrade targets the EXPLICIT halloween_fallback, never a re-read of controller.profile. A
    mid-utterance flip to STANDARD must not turn the 'fallback' into the standard voice."""
    controller = FakeController(HALLOWEEN)

    def flip_to_standard():
        controller._profile = STANDARD                       # a mid-utterance mode flip

    boom = FakeTTS(frames=["x"], fail=True, on_iter=flip_to_standard)
    fallback = FakeTTS(frames=["f1"])
    wrong = FakeTTS(frames=["WRONG"])
    build = make_build_tts({"halloween": boom, "halloween_fallback": fallback, "standard": wrong})
    agent = StudioAgent(controller, build_tts_fn=build, fallback_profile=FALLBACK, instructions="test")

    frames = await collect(agent.tts_node(atext("spooky"), None))

    assert frames == ["f1"]                 # the Deepgram fallback, not the standard voice
    assert "standard" not in build.calls    # never routed to the standard voice
    assert controller.profile_reads == 1    # read once (the snapshot); the degrade used the explicit fallback


async def test_degrade_resolves_the_fallback_from_the_controller_when_not_injected():
    """If no explicit fallback_profile is passed, the agent takes 'halloween_fallback' from the controller's
    profiles (what resolve_profiles provides) — the arg is optional for Task 10."""
    controller = FakeController(HALLOWEEN)
    controller._profiles = {"halloween_fallback": FALLBACK}
    boom = FakeTTS(frames=["x"], fail=True)
    fallback = FakeTTS(frames=["f1", "f2"])
    build = make_build_tts({"halloween": boom, "halloween_fallback": fallback})
    agent = StudioAgent(controller, build_tts_fn=build, instructions="test")   # no explicit fallback_profile

    frames = await collect(agent.tts_node(atext("boo"), None))

    assert frames == ["f1", "f2"]
    assert controller.degraded is True


async def test_degrade_skips_retrying_the_same_failing_voice():
    """L3: when the active profile IS the fallback (an already-degraded call), a failure must not retry the
    same voice — that would be seconds of dead air."""
    controller = FakeController(FALLBACK)                    # active profile key == fallback key
    boom = FakeTTS(frames=["x"], fail=True)
    build = make_build_tts({"halloween_fallback": boom})
    agent = StudioAgent(controller, build_tts_fn=build, fallback_profile=FALLBACK, instructions="test")

    frames = await collect(agent.tts_node(atext("boo"), None))

    assert frames == []                                      # no audio, no pointless retry
    assert controller.degraded is True                       # the failure is still recorded
    assert build.calls == ["halloween_fallback"]             # built once; the same voice is NOT retried


# =============================================================================== cancellation (invariant 6)
async def test_cancellation_propagates_and_does_not_degrade():
    """A caller cancellation mid-stream must propagate (CancelledError) and must NOT be mistaken for a
    vendor error — no degrade, no swallowing (uses the framework's aio.cancel_and_wait)."""
    controller = FakeController(HALLOWEEN)
    gate = asyncio.Event()
    primary = FakeTTS(frames=["p1", "p2"], gate=gate)        # plays p1, then blocks on the gate
    agent = make_agent(controller, {"halloween": primary}, fallback_profile=FALLBACK)

    played: list = []

    async def consume():
        async for frame in agent.tts_node(atext("hi"), None):
            played.append(frame)

    task = asyncio.create_task(consume())
    await _until(lambda: played == ["p1"])                   # first frame out; stream now blocked on the gate
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert controller.degrade_reasons == []                  # a cancellation is NOT a vendor error


# =============================================================================== profile snapshot
async def test_profile_is_snapshotted_once_so_a_midutterance_flip_is_ignored(monkeypatch):
    """The active profile is read ONCE at utterance start; a mode flip mid-utterance must not change the
    voice for this utterance (it stays on the profile TTS, never the standard default)."""
    default_called = {"hit": False}

    async def spy(agent, text, model_settings):   # the standard path — must NOT be taken mid-utterance
        default_called["hit"] = True
        yield "STANDARD"

    monkeypatch.setattr(Agent.default, "tts_node", staticmethod(spy))

    controller = FakeController(HALLOWEEN)

    def flip():                                   # a mid-utterance mode flip to standard
        controller._profile = STANDARD

    tts = FakeTTS(frames=["h1", "h2"], on_iter=flip)
    agent = make_agent(controller, {"halloween": tts})

    frames = await collect(agent.tts_node(atext("still ", "spooky"), None))

    assert frames == ["h1", "h2"]                 # stayed on the Halloween profile TTS
    assert default_called["hit"] is False         # never fell through to the standard default
    assert controller.profile_reads == 1          # read exactly once (the snapshot)


# =============================================================================== StreamAdapter routing
async def test_streaming_profile_tts_is_used_directly_without_a_stream_adapter(monkeypatch):
    """ElevenLabs IS streaming → stream() directly; no StreamAdapter is constructed."""
    built = []
    monkeypatch.setattr(sa.tts, "StreamAdapter", lambda **kw: built.append(kw) or SimpleNamespace())
    controller = FakeController(HALLOWEEN)
    tts = FakeTTS(frames=["s"], streaming=True)
    agent = make_agent(controller, {"halloween": tts})

    frames = await collect(agent.tts_node(atext("hey"), None))

    assert frames == ["s"]
    assert built == []                            # a streaming TTS is never wrapped


async def test_non_streaming_profile_tts_is_wrapped_in_a_stream_adapter(monkeypatch):
    """A non-streaming TTS must be wrapped in a StreamAdapter (mirrors the 1.8.3 default node)."""
    instances = []

    class FakeAdapter:
        def __init__(self, *, tts, **kw):
            self.wrapped = tts
            self.closed = False
            instances.append(self)

        def stream(self, *, conn_options=None):
            return FakeStream(self.wrapped)       # drive frames off the wrapped TTS

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(sa.tts, "StreamAdapter", FakeAdapter)
    controller = FakeController(HALLOWEEN)
    tts = FakeTTS(frames=["w1", "w2"], streaming=False)
    agent = make_agent(controller, {"halloween": tts})

    frames = await collect(agent.tts_node(atext("wrap ", "me"), None))

    assert frames == ["w1", "w2"]
    assert len(instances) == 1                    # wrapped exactly once
    assert instances[0].wrapped is tts            # wrapping the profile TTS
    assert instances[0].closed is True            # the temporary adapter is closed after the utterance
    assert tts.pushed == ["wrap ", "me"]          # text forwarded through the adapter to the TTS


# =============================================================================== transcription stripping
async def test_transcription_node_strips_tags_for_halloween():
    controller = FakeController(HALLOWEEN)
    agent = make_agent(controller, {})

    out = "".join(await collect(agent.transcription_node(
        atext("Welcome ", "[whispers]", " to the ", "[laughs]", "haunted house"), None)))

    assert "[" not in out and "]" not in out
    assert "whispers" not in out and "laughs" not in out
    assert "Welcome" in out and "haunted house" in out


async def test_transcription_node_strips_tags_for_standard_too():
    """Belt-and-braces (brief): standard mode also strips any leftover bracketed cue from the transcript."""
    controller = FakeController(STANDARD)
    agent = make_agent(controller, {})

    out = "".join(await collect(agent.transcription_node(
        atext("Hello ", "[sighs]", " world"), None)))

    assert "[sighs]" not in out
    assert "sighs" not in out
    assert "Hello" in out and "world" in out


# =============================================================================== on_user_turn_completed
async def test_on_user_turn_completed_awaits_controller_on_turn():
    controller = FakeController(STANDARD)
    agent = make_agent(controller, {})
    turn_ctx = object()
    new_message = object()

    await agent.on_user_turn_completed(turn_ctx, new_message)

    assert controller.on_turn_args == [(agent, turn_ctx, new_message)]
