"""T11 prewarm (app/agent.py): warm the ElevenLabs connection on the earliest cue, keep it warm, never raise.

No network: the TTS and its stream are fakes. `warm_tts_connection` must open a stream, end input with no
text (nothing synthesized or billed), drain it, close it, and swallow ANY vendor error (C10: a vendor hiccup
stays outside the session's error counter and must never break a turn). The `_HalloweenPrewarmer` on_cue hook
warms once (idempotent), calls `prewarm()` when the TTS exposes one, schedules the warm on a background task,
latches only after a successful schedule (so a cue before the agent is ready can retry), and never raises.
"""
import asyncio
from types import SimpleNamespace

from app.agent import _PREWARM_CONN_OPTIONS, _HalloweenPrewarmer, warm_tts_connection


# --------------------------------------------------------------------------------------- fakes
class FakeStream:
    """A SynthesizeStream stand-in: async context manager + async iterator. Records end_input, yields the
    parent's frames, and can fail on enter / on end_input / on iteration, or hang until a gate is set."""

    def __init__(self, parent):
        self._parent = parent

    async def __aenter__(self):
        if self._parent.fail_enter:
            raise RuntimeError("ws connect failed")
        return self

    async def __aexit__(self, *exc):
        self._parent.closed = True
        return False

    def end_input(self):
        self._parent.ended = True
        if self._parent.fail_end_input:
            raise RuntimeError("end_input failed")

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        if self._parent.gate is not None:
            await self._parent.gate.wait()          # hang so a shutdown cancel can be exercised
        if self._parent.fail_iter:
            raise RuntimeError("stream iteration failed")
        for f in self._parent.frames:
            yield SimpleNamespace(frame=f)


class FakeTTS:
    """A streaming TTS stand-in with a prewarm() counter. Records the conn_options stream() was handed and
    whether the stream was ended / closed."""

    def __init__(self, *, frames=(), fail_stream=False, fail_enter=False, fail_end_input=False,
                 fail_iter=False, gate=None):
        self.frames = list(frames)
        self.fail_stream = fail_stream
        self.fail_enter = fail_enter
        self.fail_end_input = fail_end_input
        self.fail_iter = fail_iter
        self.gate = gate
        self.conn_options = []
        self.ended = False
        self.closed = False
        self.prewarm_calls = 0

    def stream(self, *, conn_options=None):
        self.conn_options.append(conn_options)
        if self.fail_stream:
            raise RuntimeError("could not open stream")
        return FakeStream(self)

    def prewarm(self):
        self.prewarm_calls += 1


class StreamOnlyTTS:
    """A TTS with no prewarm() method, to prove the warm still schedules when prewarm() is absent."""

    def __init__(self):
        self.conn_options = []
        self.ended = False
        self.closed = False
        self.frames = []
        self.fail_stream = self.fail_enter = self.fail_end_input = self.fail_iter = False
        self.gate = None

    def stream(self, *, conn_options=None):
        self.conn_options.append(conn_options)
        return FakeStream(self)


# =============================================================================== warm_tts_connection
async def test_warm_opens_ends_drains_and_closes():
    tts = FakeTTS(frames=["x", "y"])            # any frames are drained + discarded (never routed to a room)

    await warm_tts_connection(tts)

    assert tts.conn_options == [_PREWARM_CONN_OPTIONS]   # opened once, with the tight warm options
    assert tts.ended is True                              # input ended with no text pushed (no synthesis)
    assert tts.closed is True                             # the context manager closed the connection


async def test_warm_forwards_custom_conn_options():
    tts = FakeTTS()
    sentinel = object()

    await warm_tts_connection(tts, sentinel)

    assert tts.conn_options == [sentinel]


async def test_warm_never_raises_when_stream_cannot_open():
    tts = FakeTTS(fail_stream=True)
    await warm_tts_connection(tts)                        # must not raise
    assert tts.conn_options == [_PREWARM_CONN_OPTIONS]
    assert tts.closed is False                            # never opened


async def test_warm_never_raises_when_connect_fails():
    tts = FakeTTS(fail_enter=True)
    await warm_tts_connection(tts)                        # must not raise


async def test_warm_never_raises_when_iteration_fails_and_still_closes():
    tts = FakeTTS(fail_iter=True)
    await warm_tts_connection(tts)                        # must not raise
    assert tts.ended is True
    assert tts.closed is True                             # async with still closed the connection


async def test_warm_tolerates_end_input_failure():
    tts = FakeTTS(fail_end_input=True)
    await warm_tts_connection(tts)                        # must not raise
    assert tts.closed is True


# =============================================================================== _HalloweenPrewarmer
async def test_prewarmer_warms_once_and_calls_prewarm():
    tts = FakeTTS()
    builds = []
    prewarmer = _HalloweenPrewarmer(lambda: builds.append(1) or tts)

    prewarmer()                                           # earliest cue
    assert builds == [1]                                  # the Halloween TTS was built
    assert tts.prewarm_calls == 1                         # prewarm() invoked
    assert prewarmer._task is not None
    await prewarmer._task                                 # let the background warm run
    assert tts.conn_options == [_PREWARM_CONN_OPTIONS] and tts.ended and tts.closed

    prewarmer()                                           # a later cue is a no-op (idempotent keep-warm)
    assert builds == [1]                                  # get_tts not called again
    assert tts.prewarm_calls == 1


async def test_prewarmer_schedules_even_without_a_prewarm_method():
    tts = StreamOnlyTTS()
    prewarmer = _HalloweenPrewarmer(lambda: tts)

    prewarmer()                                           # must not crash on the missing prewarm()
    assert prewarmer._task is not None
    await prewarmer._task
    assert tts.ended and tts.closed


async def test_prewarmer_does_not_latch_when_tts_not_ready_and_retries_later():
    tts = FakeTTS()
    seq = iter([None, tts])                               # agent not ready on the first cue, ready on the next
    prewarmer = _HalloweenPrewarmer(lambda: next(seq))

    prewarmer()                                           # TTS not ready -> nothing scheduled, not latched
    assert prewarmer._task is None

    prewarmer()                                           # a later cue retries and warms
    assert prewarmer._task is not None
    await prewarmer._task
    assert tts.closed is True


async def test_prewarmer_never_raises_when_get_tts_raises():
    def boom():
        raise RuntimeError("tts build blew up")

    prewarmer = _HalloweenPrewarmer(boom)
    prewarmer()                                           # must not raise
    assert prewarmer._task is None                        # nothing scheduled; not latched


async def test_prewarmer_aclose_cancels_a_running_warm():
    gate = asyncio.Event()                                # never set -> the warm hangs draining the stream
    tts = FakeTTS(gate=gate)
    prewarmer = _HalloweenPrewarmer(lambda: tts)

    prewarmer()
    task = prewarmer._task
    assert task is not None and not task.done()

    await prewarmer.aclose()                              # cancels + drains the warm task
    assert task.cancelled() or task.done()


async def test_prewarmer_aclose_is_safe_when_nothing_scheduled():
    prewarmer = _HalloweenPrewarmer(lambda: None)
    prewarmer()                                           # nothing scheduled
    await prewarmer.aclose()                              # must not raise
