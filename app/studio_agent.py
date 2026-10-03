"""The StudioAgent — the LiveKit audio-path layer for AI-decide / Halloween mode (spec §7.1 / §7.7).

Three node overrides on top of the stock `Agent`:

  - `on_user_turn_completed` hands the turn to the `VoiceModeController`, which decides and applies the
    voice mode. The controller never raises; this override adds no logic that could.

  - `tts_node` routes by the ACTIVE profile, snapshotted ONCE at the start of the utterance (a mid-utterance
    mode flip must not change this utterance's voice):
      * standard  → delegate to `Agent.default.tts_node` (the session's own, unchanged TTS).
      * otherwise → stream through the PROFILE-OWNED TTS, mirroring the 1.8.3 default node: a `StreamAdapter`
        only when the TTS cannot stream natively (ElevenLabs IS streaming → `stream()` directly); forward the
        text (`push_text` per chunk, then `end_input`) on a side task; `yield ev.frame`. Tight
        `APIConnectOptions(max_retry=1, timeout=5.0)` so a hung vendor degrades fast.
    DEGRADE, never raise: any vendor error — or a `ValueError`/`ImportError` from `build_tts` — is caught,
    the controller is told (`mark_degraded`), and the rest of the utterance is spoken by the fallback profile
    (Deepgram dark voice). The profile-owned TTS sits OUTSIDE the session's error counter (C10): a vendor
    hiccup must never close the session, so nothing propagates out of `tts_node`.

  - `transcription_node` strips expressive tags (`strip_tags_stream`) for EVERY profile so a `[tag]` never
    reaches the transcript (G13) — belt-and-braces, standard mode included.

Build-once: a profile's TTS instance is built on first use and cached by profile key, never per utterance.
Subscribing to its `error`/`metrics` events and `aclose()`-ing it at shutdown is Task 10's wiring job; here
we only route and degrade. The native `expressive` subsystem is left untouched (Task 10 keeps it off).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterable

from livekit import rtc
from livekit.agents import Agent, APIConnectOptions, ModelSettings, tts

from app.expressive import strip_tags_stream
from app.voice_mode import VoiceModeController
from app.voice_profiles import VoiceProfile, build_tts

logger = logging.getLogger(__name__)

# A hung Halloween vendor must degrade fast: one retry, 5 s cap (spec §7.7). The session's own TTS keeps its
# own (looser) options — this is only for the profile-owned voices.
_CONN_OPTIONS = APIConnectOptions(max_retry=1, timeout=5.0)


async def _cancel_and_wait(task: asyncio.Task) -> None:
    """Cancel the text-forwarding side task and drain it (its result/exception is not ours to surface — the
    vendor error, if any, comes out of the stream iteration, exactly as in the 1.8.3 default node)."""
    task.cancel()
    try:
        await task
    except BaseException:  # noqa: BLE001 - draining a cancelled forward task on the way out
        pass


class StudioAgent(Agent):
    """Overrides the TTS / transcription / turn nodes; everything else is the stock Agent."""

    def __init__(self, controller: VoiceModeController, *, build_tts=build_tts, **kwargs) -> None:
        super().__init__(**kwargs)
        self._controller = controller
        self._build_tts = build_tts
        self._tts_by_key: dict[str, tts.TTS] = {}   # profile.key -> its TTS, built once

    # ---------------------------------------------------------------- the turn hook
    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        """Decide and apply the voice mode for this turn (the controller never raises)."""
        await self._controller.on_turn(self, turn_ctx, new_message)

    # ---------------------------------------------------------------- audio synthesis
    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        # Snapshot the profile ONCE: a mid-utterance mode flip must not change this utterance's voice.
        profile = self._controller.profile

        if profile.key == "standard":
            async for frame in Agent.default.tts_node(self, text, model_settings):
                yield frame
            return

        # Non-standard (Halloween): stream through the profile TTS; degrade to the fallback on ANY error.
        buffer: list[str] = []            # chunks consumed so far, so the fallback can replay them
        text_iter = text.__aiter__()
        try:
            profile_tts = self._tts_for(profile)      # may raise ValueError/ImportError from build_tts
            async for frame in self._synthesize(profile_tts, buffer, text_iter):
                yield frame
            return
        except Exception as exc:  # noqa: BLE001 - a vendor failure degrades; it NEVER raises out of tts_node
            self._controller.mark_degraded(f"{profile.vendor} TTS error: {type(exc).__name__}")

        # Degraded: speak the rest of the utterance (and the rest of the call) with the fallback voice.
        fallback = self._controller.profile           # now the fallback profile (degraded latched)
        try:
            fallback_tts = self._tts_for(fallback)
            async for frame in self._synthesize(fallback_tts, buffer, text_iter):
                yield frame
        except Exception:  # noqa: BLE001 - even the fallback must not crash the audio path or the session
            logger.warning("voice fallback TTS failed; the utterance is dropped (session kept alive)",
                           exc_info=True)

    # ---------------------------------------------------------------- transcript
    def transcription_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        """Strip expressive tags for every profile (G13), then hand off to the default node."""
        return Agent.default.transcription_node(self, strip_tags_stream(text), model_settings)

    # ---------------------------------------------------------------- helpers
    def _tts_for(self, profile: VoiceProfile) -> tts.TTS:
        """The profile's TTS, built once and cached by key (never per utterance)."""
        instance = self._tts_by_key.get(profile.key)
        if instance is None:
            instance = self._tts_by_key[profile.key] = self._build_tts(profile)
        return instance

    async def _synthesize(
        self, tts_obj: tts.TTS, buffer: list[str], text_iter
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        """Mirror the 1.8.3 default `tts_node` for a profile-owned TTS: wrap in a `StreamAdapter` only when
        the TTS can't stream natively; forward text on a side task (replaying any already-buffered chunks
        first, then draining `text_iter` and buffering each so a later fallback can replay it); yield frames.
        Any vendor error propagates to the caller, which degrades."""
        adapter: tts.StreamAdapter | None = None
        wrapped = tts_obj
        if not tts_obj.capabilities.streaming:
            adapter = tts.StreamAdapter(tts=tts_obj)
            wrapped = adapter
        try:
            async with wrapped.stream(conn_options=_CONN_OPTIONS) as stream:

                async def _forward() -> None:
                    for chunk in list(buffer):            # replay what an earlier (failed) voice consumed
                        stream.push_text(chunk)
                    async for chunk in text_iter:         # the rest of this utterance
                        buffer.append(chunk)
                        stream.push_text(chunk)
                    stream.end_input()

                forward_task = asyncio.create_task(_forward())
                try:
                    async for ev in stream:
                        yield ev.frame
                finally:
                    await _cancel_and_wait(forward_task)
        finally:
            if adapter is not None:
                await adapter.aclose()                    # closes the temp adapter only, not the wrapped TTS
