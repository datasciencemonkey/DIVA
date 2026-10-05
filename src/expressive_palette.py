"""The Halloween voice's expressive-cue palette: the ElevenLabs v3 audio tags that
docs/discovery/expressive-tags-contract.md shows the configured voice PERFORMS (never reads aloud), grouped by
purpose. Pure data and two helpers; standard library only.

`app.expressive.SPOOKY_TAGS` is this palette flattened (the only tags that may reach the TTS, G13), and
`src.agent_prompt` renders it grouped for the model. Change it only by re-running tools/expressive_bakeoff.py and
updating the contract; a test keeps the two equal.
"""
from __future__ import annotations

from collections.abc import Iterable

# category -> tags, in the order they are shown to the model. Lowercase, at most 32 characters, unique overall.
PALETTE: dict[str, tuple[str, ...]] = {
    "breath": (
        "deep breaths", "exhales", "inhales deeply", "sighs", "gasps", "gulps", "clears throat", "heavy breathing",
        "shaky breath", "laughs", "laughing", "chuckles", "evil laugh", "maniacal laughter", "giggles", "snorts",
        "wheezing", "groans", "exhales sharply", "panting", "menacing laugh",
    ),
    "volume": (
        "whispers", "whisper", "whispering", "soft", "softly", "quietly", "hushed", "low voice", "deep voice", "growls",
        "raspy", "rumbling",
    ),
    "emotion": (
        "dismissive", "mischievously", "nervously", "menacing", "sinister", "ominous", "eerie", "sarcastic", "curious",
        "amused", "dramatic", "somber", "fearful", "cold", "gleeful", "smug", "playful", "excited", "panicking",
        "deadpan", "thoughtful",
    ),
    "pacing": (
        "building tension", "pause", "long pause", "dramatic pause", "slowly", "suspenseful", "hesitates",
        "trailing off", "rushed", "measured", "slow", "short pause", "drawn out",
    ),
}

# category -> "Label (when to use it)", printed before the group's tags in the prompt.
GROUP_HINTS: dict[str, str] = {
    "breath": "Breath and sounds (reactions, laughter, breathing)",
    "volume": "Volume (how softly or heavily a line is spoken)",
    "emotion": "Attitude (the feeling behind a line)",
    "pacing": "Pacing and tension (timing and suspense)",
}

OTHER = "other"
OTHER_HINT = "Other cues"


def flatten(palette: dict[str, tuple[str, ...]] = PALETTE) -> tuple[str, ...]:
    """Every tag, category by category, in display order."""
    return tuple(tag for members in palette.values() for tag in members)


def grouped(tags: Iterable[str]) -> list[tuple[str, tuple[str, ...]]]:
    """[(category, tags)] for the given tags: palette order, empty categories left out, and any tag the palette
    does not know collected (sorted) under a final `OTHER` category."""
    wanted = set(tags)
    out = []
    for category, members in PALETTE.items():
        present = tuple(t for t in members if t in wanted)
        if present:
            out.append((category, present))
    unknown = tuple(sorted(wanted - set(flatten())))
    if unknown:
        out.append((OTHER, unknown))
    return out
