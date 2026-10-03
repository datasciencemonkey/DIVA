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
    the controller is told (`mark_degraded`), and the rest of the utterance is spoken by the EXPLICIT
    fallback profile (Deepgram dark voice). The profile-owned TTS sits OUTSIDE the session's error counter
    (C10): a vendor hiccup must never close the session, so nothing propagates out of `tts_node`. A caller
    cancellation (`CancelledError`, a `BaseException`) is NOT a vendor error — it is left to propagate and
    does not degrade.

  - `transcription_node` strips expressive tags (`strip_tags_stream`) for EVERY profile so a `[tag]` never
    reaches the transcript (G13) — belt-and-braces, standard mode included.

Degrade specifics:
  * The fallback target is the EXPLICIT `halloween_fallback` profile (captured at construction), never a
    re-read of `controller.profile` — a mid-utterance flip to STANDARD must not turn the "fallback" into the
    standard voice (L2).
  * The Deepgram fallback performs no tags, so the degrade-path text is run through `strip_tags_stream` when
    the fallback profile carries no tags — otherwise Deepgram would SPEAK a decoded `[whispers]` (G13 / M1a).
  * Frames already played by the failed voice are not re-spoken (M1c); the remainder of the utterance may be
    cut off (accepted, spec §8). The same-voice retry is skipped (L3).

Build-once: a profile's TTS instance is built on first use and cached by profile key, never per utterance.
Subscribing to its `error`/`metrics` events and `aclose()`-ing it at shutdown is Task 10's wiring job; here
we only route and degrade. The native `expressive` subsystem is left untouched (Task 10 keeps it off).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator

from livekit import rtc
from livekit.agents import Agent, APIConnectOptions, ModelSettings, llm, tts
from livekit.agents.utils import aio

from app.expressive import strip_tags_stream
from app.voice_mode import VoiceModeController
from app.voice_profiles import VoiceProfile, build_tts

logger = logging.getLogger(__name__)

# A hung Halloween vendor must degrade fast: one retry, 5 s cap (spec §7.7). The session's own TTS keeps its
# own (looser) options — this is only for the profile-owned voices.
_CONN_OPTIONS = APIConnectOptions(max_retry=1, timeout=5.0)


async def _record(src: AsyncIterator[str], buffer: list[str]) -> AsyncIterator[str]:
    """Yield each chunk from `src`, recording it in `buffer` first so a degrade can replay what the failed
    voice had already consumed (the append precedes the yield with no await between, so a cancellation at the
    pull boundary cannot drop an already-recorded chunk)."""
    async for chunk in src:
        buffer.append(chunk)
        yield chunk


async def _chain(chunks: list[str], rest: AsyncIterator[str]) -> AsyncIterator[str]:
    """One stream: the already-buffered `chunks`, then the not-yet-consumed `rest`."""
    for chunk in chunks:
        yield chunk
    async for chunk in rest:
        yield chunk


class StudioAgent(Agent):
    """Overrides the TTS / transcription / turn nodes; everything else is the stock Agent."""

    def __init__(
        self,
        controller: VoiceModeController,
        *,
        fallback_profile: VoiceProfile | None = None,
        build_tts_fn=build_tts,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._controller = controller
        self._build_tts = build_tts_fn
        # The degrade target, captured deterministically at construction (never a mid-utterance re-read).
        # Prefer the explicit arg; otherwise take "halloween_fallback" from the controller's profiles.
        self._fallback_profile = fallback_profile or self._fallback_from_controller(controller)
        self._tts_by_key: dict[str, tts.TTS] = {}   # profile.key -> its TTS, built once

    @staticmethod
    def _fallback_from_controller(controller: VoiceModeController) -> VoiceProfile | None:
        profiles = getattr(controller, "_profiles", None)
        if isinstance(profiles, dict):
            return profiles.get("halloween_fallback")
        return None

    # ---------------------------------------------------------------- the turn hook
    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
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
        buffer: list[str] = []            # chunks consumed so far, so a fallback can replay them
        text_iter = text.__aiter__()
        yielded_any = False
        try:
            profile_tts = self._tts_for(profile)      # may raise ValueError/ImportError from build_tts
            async for frame in self._synthesize(profile_tts, _record(text_iter, buffer)):
                yielded_any = True
                yield frame
            return
        except Exception as exc:  # noqa: BLE001 - a vendor failure degrades; CancelledError still propagates
            logger.warning("profile TTS failed; degrading to the fallback voice", exc_info=True)
            self._controller.mark_degraded(f"{profile.vendor} TTS error: {type(exc).__name__}")

        # Degraded. Target the EXPLICIT fallback profile; never retry the same voice that just failed.
        fallback = self._fallback_profile
        if fallback is None or fallback.key == profile.key:
            return
        replay = [] if yielded_any else list(buffer)  # M1c: don't re-speak frames already played
        degrade_text: AsyncIterator[str] = _chain(replay, text_iter)
        if not fallback.tags:                          # M1a (G13): a tag-less voice must not SPEAK [tags]
            degrade_text = strip_tags_stream(degrade_text)
        try:
            fallback_tts = self._tts_for(fallback)
            async for frame in self._synthesize(fallback_tts, degrade_text):
                yield frame
        except Exception:  # noqa: BLE001 - even the fallback must not crash the audio path or the session
            logger.warning("fallback TTS failed; the utterance is dropped (session kept alive)",
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
        self, tts_obj: tts.TTS, text: AsyncIterator[str]
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        """Mirror the 1.8.3 default `tts_node` for a profile-owned TTS: wrap in a `StreamAdapter` only when
        the TTS can't stream natively; forward `text` on a side task (push_text per chunk, then end_input);
        yield frames. Any vendor error propagates to the caller, which degrades."""
        adapter: tts.StreamAdapter | None = None
        wrapped = tts_obj
        if not tts_obj.capabilities.streaming:
            adapter = tts.StreamAdapter(tts=tts_obj)
            wrapped = adapter
        try:
            async with wrapped.stream(conn_options=_CONN_OPTIONS) as stream:

                async def _forward() -> None:
                    async for chunk in text:
                        stream.push_text(chunk)
                    stream.end_input()

                forward_task = asyncio.create_task(_forward())
                try:
                    async for ev in stream:
                        yield ev.frame
                finally:
                    await aio.cancel_and_wait(forward_task)
        finally:
            if adapter is not None:
                await adapter.aclose()                    # closes the temp adapter only, not the wrapped TTS
