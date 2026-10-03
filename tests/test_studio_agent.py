"""StudioAgent node overrides (spec §7.1 / §7.7) — the LiveKit audio-path layer.

Three overrides are exercised with fakes (no network, no AgentSession):
  - `tts_node` routes by the ACTIVE profile, snapshotted ONCE at the start of the utterance: standard
    delegates to `Agent.default.tts_node` (the session's own TTS); any other profile streams through its
    own TTS and, on ANY vendor error, degrades to the fallback (Deepgram dark voice) WITHOUT raising.
  - `transcription_node` strips expressive tags for every profile so a `[tag]` never shows.
  - `on_user_turn_completed` hands the turn to the controller.

Fakes: a controller exposing `.profile` / `.mark_degraded` / `.on_turn`; streaming TTS instances that
honor push_text/end_input and yield frames (or raise a vendor error); and a spy on
`Agent.default.tts_node`. The profile-owned TTS lifecycle (error/metrics subscription, aclose) is Task 10's
wiring job and is out of scope here.
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
    """Stands in for VoiceModeController: exposes `.profile` (switching to the fallback once degraded),
    records `mark_degraded`/`on_turn`, and counts `.profile` reads so the per-utterance snapshot can be
    asserted."""

    def __init__(self, profile, *, fallback=None):
        self._profile = profile
        self._fallback = fallback
        self.degraded = False
        self.degrade_reasons: list[str] = []
        self.on_turn_args: list[tuple] = []
        self.profile_reads = 0

    @property
    def profile(self):
        self.profile_reads += 1
        if self.degraded and self._fallback is not None:
            return self._fallback
        return self._profile

    def mark_degraded(self, reason):
        self.degraded = True
        self.degrade_reasons.append(reason)

    async def on_turn(self, agent, turn_ctx, new_message):
        self.on_turn_args.append((agent, turn_ctx, new_message))


class FakeStream:
    """A SynthesizeStream stand-in: push_text buffers tokens, end_input unblocks iteration, and iteration
    yields one `.frame` per configured frame — or raises a vendor error (after end_input, as a real vendor
    would once it has the text)."""

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
        for frame in self._parent.frames:
            yield SimpleNamespace(frame=frame)


class FakeTTS:
    """A streaming TTS stand-in. `streaming=False` forces the StreamAdapter branch; `fail=True` makes its
    stream raise a vendor error. Records pushed text and the conn_options it was handed."""

    def __init__(self, frames=(), *, streaming=True, fail=False, on_iter=None):
        self.capabilities = SimpleNamespace(streaming=streaming, aligned_transcript=False)
        self.frames = list(frames)
        self.fail = fail
        self.on_iter = on_iter
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


def make_agent(controller, mapping):
    return StudioAgent(controller, build_tts=make_build_tts(mapping), instructions="test")


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
    agent = StudioAgent(controller, build_tts=build, instructions="test")

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
    agent = StudioAgent(controller, build_tts=build, instructions="test")

    await collect(agent.tts_node(atext("one"), None))
    await collect(agent.tts_node(atext("two"), None))

    assert build.calls == ["halloween"]             # built exactly once
    assert tts.pushed == ["one", "two"]             # same instance reused


# =============================================================================== degrade (never raise)
async def test_vendor_error_degrades_without_raising_and_fallback_continues():
    """A vendor error on the profile TTS → mark_degraded + fallback audio, and NO exception escapes."""
    controller = FakeController(HALLOWEEN, fallback=FALLBACK)
    boom = FakeTTS(frames=["never"], fail=True)
    fallback = FakeTTS(frames=["f1", "f2"])
    agent = make_agent(controller, {"halloween": boom, "halloween_fallback": fallback})

    frames = await collect(agent.tts_node(atext("spooky ", "please"), None))

    assert controller.degraded is True                      # degrade latched
    assert len(controller.degrade_reasons) == 1             # marked exactly once
    assert frames == ["f1", "f2"]                           # fallback audio continued
    assert fallback.pushed == ["spooky ", "please"]         # the full utterance text reached the fallback


@pytest.mark.parametrize("exc", [ValueError("missing voice id"), ImportError("no plugin")])
async def test_build_tts_failure_degrades_to_fallback(exc):
    """build_tts raising ValueError/ImportError is also a degrade, not a crash (spec §7.7)."""
    controller = FakeController(HALLOWEEN, fallback=FALLBACK)
    fallback = FakeTTS(frames=["only-fallback"])
    agent = make_agent(controller, {"halloween": exc, "halloween_fallback": fallback})

    frames = await collect(agent.tts_node(atext("make ", "it ", "spooky"), None))

    assert controller.degraded is True
    assert frames == ["only-fallback"]
    assert fallback.pushed == ["make ", "it ", "spooky"]    # the whole utterance still spoken, by the fallback


async def test_tts_node_never_raises_even_if_the_fallback_also_fails():
    """Belt-and-braces: even if the fallback TTS fails too, tts_node yields nothing rather than raising
    (a vendor outage must never crash the audio path or the session)."""
    controller = FakeController(HALLOWEEN, fallback=FALLBACK)
    boom = FakeTTS(frames=["x"], fail=True)
    boom_fallback = FakeTTS(frames=["y"], fail=True)
    agent = make_agent(controller, {"halloween": boom, "halloween_fallback": boom_fallback})

    frames = await collect(agent.tts_node(atext("uh oh"), None))   # must not raise

    assert frames == []
    assert controller.degraded is True


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
