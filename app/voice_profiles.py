"""Voice profiles and the vendor router (spec §7.7): which voice speaks, which expressive tags it performs,
and which persona the prompt wears.

    profile             voice                                               tags               persona
    standard            Deepgram aura-2-andromeda-en, the session's own     none               none
    halloween           UG_HALLOWEEN_TTS: ElevenLabs (default), OpenAI,     SPOOKY_TAGS,       HALLOWEEN_PERSONA
                        or Deepgram                                         ElevenLabs only
    halloween_fallback  Deepgram UG_HALLOWEEN_FALLBACK_VOICE                none               HALLOWEEN_PERSONA

`resolve_profiles(env)` only reads the mapping it is given. It picks a vendor only after seeing that the
vendor's settings are there: at LiveKit 1.8.3 the ElevenLabs plugin raises ValueError at construction without
ELEVEN_API_KEY. A Halloween voice whose settings are missing therefore *is* the Deepgram fallback: the same
spooky persona in a darker Deepgram voice, with no expressive tags, and nothing crashes.

`build_tts(profile)` imports the vendor plugin only when called, so this module imports without LiveKit, and
constructs the TTS without connecting (ElevenLabs opens its WebSocket on first use; `prewarm()` is a no-op
there). A profile carries only what the prompt and the UI need, so the model, voice and stability are read
from the environment at build time.
"""
from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.expressive import SPOOKY_TAGS
from src.agent_prompt import HALLOWEEN_PERSONA

if TYPE_CHECKING:
    from livekit.agents import tts

logger = logging.getLogger(__name__)

STANDARD_VOICE = "aura-2-andromeda-en"          # app/agent.py builds the session's own TTS with this voice
DEFAULT_FALLBACK_VOICE = "aura-2-zeus-en"       # the darker Deepgram voice the spec names for Halloween
DEFAULT_VENDOR = "elevenlabs"
DEFAULT_ELEVENLABS_MODEL = "eleven_v3_conversational"   # a dialogue model: it performs and streams [tags]
DEFAULT_STABILITY = 0.5
OPENAI_MODEL = "gpt-4o-mini-tts"
OPENAI_STYLE = ("Speak as a playful, spooky Halloween host: eerie and theatrical, with slow dramatic pauses. "
                "Keep it fun, never menacing.")

# What a vendor needs before it can be used. Anything missing sends the Halloween voice to the fallback.
_REQUIRED = {
    "elevenlabs": ("ELEVEN_API_KEY", "UG_HALLOWEEN_VOICE_ID"),
    "openai": ("OPENAI_API_KEY",),
    "deepgram": (),
}


@dataclass(frozen=True)
class VoiceProfile:
    key: str               # "standard" | "halloween" | "halloween_fallback"
    label: str             # shown on the Control pillar, e.g. "ElevenLabs · eleven_v3_conversational"
    vendor: str            # "deepgram" | "elevenlabs" | "openai"
    tags: frozenset[str]   # expressive tags this TTS performs; empty: the tag filter drops every cue
    persona: str | None    # persona text for build_instructions; None: the standard voice


# ------------------------------------------------------------------ settings

def _setting(env: Mapping[str, str], name: str, default: str = "") -> str:
    """The trimmed value of `name`, or `default` when it is unset or blank."""
    return (env.get(name) or "").strip() or default


def _fallback_voice(env: Mapping[str, str]) -> str:
    return _setting(env, "UG_HALLOWEEN_FALLBACK_VOICE", DEFAULT_FALLBACK_VOICE)


def _elevenlabs_model(env: Mapping[str, str]) -> str:
    return _setting(env, "UG_HALLOWEEN_TTS_MODEL", DEFAULT_ELEVENLABS_MODEL)


def _vendor(env: Mapping[str, str]) -> str:
    """The configured Halloween vendor. A value we don't know is a typo, not a reason to end the call."""
    vendor = _setting(env, "UG_HALLOWEEN_TTS", DEFAULT_VENDOR).lower()
    if vendor not in _REQUIRED:
        logger.warning("UG_HALLOWEEN_TTS=%r is not one of %s; using %s", vendor, ", ".join(_REQUIRED), DEFAULT_VENDOR)
        return DEFAULT_VENDOR
    return vendor


def _stability(env: Mapping[str, str]) -> float:
    raw = _setting(env, "UG_HALLOWEEN_STABILITY")
    if not raw:
        return DEFAULT_STABILITY
    try:
        value = float(raw)
    except ValueError:
        value = math.nan   # fails the range test below, like inf
    if not 0.0 <= value <= 1.0:
        logger.warning("UG_HALLOWEEN_STABILITY=%r is not a number from 0 to 1; using %s", raw, DEFAULT_STABILITY)
        return DEFAULT_STABILITY
    return value


# ------------------------------------------------------------------ resolving

def _deepgram(key: str, voice: str, persona: str | None) -> VoiceProfile:
    return VoiceProfile(key, f"Deepgram · {voice}", "deepgram", frozenset(), persona)


def _halloween(env: Mapping[str, str], fallback: VoiceProfile) -> VoiceProfile:
    vendor = _vendor(env)
    if missing := [name for name in _REQUIRED[vendor] if not _setting(env, name)]:
        # names only: the values are secrets
        logger.warning("Halloween voice: %s not set, so %s is unavailable; using %s (no expressive tags)",
                       ", ".join(missing), vendor, fallback.label)
        return replace(fallback, key="halloween")
    if vendor == "elevenlabs":
        return VoiceProfile("halloween", f"ElevenLabs · {_elevenlabs_model(env)}", "elevenlabs",
                            frozenset(SPOOKY_TAGS), HALLOWEEN_PERSONA)
    if vendor == "openai":
        return VoiceProfile("halloween", f"OpenAI · {OPENAI_MODEL}", "openai", frozenset(), HALLOWEEN_PERSONA)
    return replace(fallback, key="halloween")   # UG_HALLOWEEN_TTS=deepgram


def resolve_profiles(env: Mapping[str, str]) -> dict[str, VoiceProfile]:
    """The three profiles for `env`. Never raises: a Halloween voice that cannot be built is the fallback."""
    fallback = _deepgram("halloween_fallback", _fallback_voice(env), HALLOWEEN_PERSONA)
    return {
        "standard": _deepgram("standard", STANDARD_VOICE, None),
        "halloween": _halloween(env, fallback),
        "halloween_fallback": fallback,
    }


# ------------------------------------------------------------------ building

def _elevenlabs_tts() -> tts.TTS:
    from livekit.agents import NOT_GIVEN
    from livekit.plugins import elevenlabs

    env = os.environ
    if not (voice_id := _setting(env, "UG_HALLOWEEN_VOICE_ID")):
        raise ValueError("UG_HALLOWEEN_VOICE_ID is required for the ElevenLabs Halloween voice")
    return elevenlabs.TTS(
        model=_elevenlabs_model(env),
        voice_id=voice_id,
        # A dialogue model (eleven_v3*) honors `stability` alone: the plugin drops similarity_boost, style and
        # speed, and warns about each one that is set. The dataclass makes similarity_boost a required field,
        # so it is passed as NOT_GIVEN, which the plugin discards along with the other unset fields.
        voice_settings=elevenlabs.VoiceSettings(stability=_stability(env), similarity_boost=NOT_GIVEN),
    )


def _openai_tts() -> tts.TTS:
    from livekit.plugins import openai

    return openai.TTS(model=OPENAI_MODEL, instructions=OPENAI_STYLE)


def _deepgram_tts(profile: VoiceProfile) -> tts.TTS:
    from livekit.plugins import deepgram

    voice = STANDARD_VOICE if profile.key == "standard" else _fallback_voice(os.environ)
    return deepgram.TTS(model=voice)


def build_tts(profile: VoiceProfile) -> tts.TTS:
    """Construct the TTS a profile speaks with, without connecting. The caller owns it: the session wires up
    only its own TTS, so the caller subscribes to this one's `error` and `metrics_collected` events and
    `aclose()`s it at shutdown."""
    if profile.vendor == "elevenlabs":
        return _elevenlabs_tts()
    if profile.vendor == "openai":
        return _openai_tts()
    if profile.vendor == "deepgram":
        return _deepgram_tts(profile)
    raise ValueError(f"no TTS for vendor {profile.vendor!r} (profile {profile.key!r})")
