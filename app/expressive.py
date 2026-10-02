"""Expressive audio tags (spec §7.8): keep `[whispers]`-style cues out of the stock TTS filters, out of the
transcript, and out of the voice when the TTS can't perform them.

The stock `filter_markdown` treats any `[` that is not a complete `[text](url)` link as unfinished markdown
and holds back the rest of the reply until the stream ends (C7), which would stall streaming TTS on the first
cue. So the session installs

    tts_text_transforms = [encode_tags(vocabulary, on_tag), "filter_markdown", "filter_emoji", decode_tags]

`encode_tags` rewrites each allowed `[tag]` to one opaque private-use character (U+E000..U+F8FF) before the
stock filters run and drops every other tag. Neither stock filter matches that block, so a placeholder passes
straight through them, and `decode_tags` turns it back into `[tag]` for the TTS. The vocabulary is the only
thing that can put a tag on the air, which is G13: a tag the TTS does not perform is never read aloud.

Pure: standard library only, no LiveKit import. The stock filters only appear in the tests.
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable

logger = logging.getLogger(__name__)

# ElevenLabs audio tags for the Halloween voice. Left out on purpose: crying (distressing), sound effects such
# as gunshot or explosion (startling, off-brand) and accent tags (risk of offensive impressions).
SPOOKY_TAGS: tuple[str, ...] = (
    "whispers", "sighs", "laughs", "mischievously", "nervously", "exhales", "inhales deeply",
)

# The longest `[...]`, brackets included, that can be a tag; so also the most that is ever held back.
MAX_TAG_LEN = 34

_NAME = rf"[^\[\]\n]{{0,{MAX_TAG_LEN - 2}}}"
_TAG = re.compile(rf"\[({_NAME})\]")   # a finished `[name]`
_OPEN = re.compile(rf"\[{_NAME}\Z")    # `[na` at the end of the text: could still become one

# ------------------------------------------------------------------ placeholders

_PRIVATE_USE = re.compile(r"[\ue000-\uf8ff]")
_PLACEHOLDER: dict[str, str] = {}  # tag name -> its private-use character
_DECODE: dict[int, str] = {}       # that character's ordinal -> "[name]" (a str.translate table)


def _placeholder(name: str) -> str:
    """The private-use character standing in for `[name]`; one per name, for the life of the process.

    Names come from the server-side vocabulary (a handful), never from the model, so the 6,400 characters
    of the block are never a constraint.
    """
    placeholder = _PLACEHOLDER.get(name)
    if placeholder is None:
        code = 0xE000 + len(_PLACEHOLDER)
        placeholder = _PLACEHOLDER[name] = chr(code)
        _DECODE[code] = f"[{name}]"
    return placeholder


for _spooky in SPOOKY_TAGS:  # the standard vocabulary always gets the same characters: U+E000 + its index
    _placeholder(_spooky)


# ------------------------------------------------------------------ scanning

def _scan(text: str, *, final: bool) -> tuple[list[tuple[str, bool]], str]:
    """Split `text` into [(piece, is_tag)] and the tail to hold back until more text arrives.

    A tag is a short `[name]` that is not followed by `(`: `[text](url)` is a markdown link, which stays
    text. The tail is at most one partial `[...` (MAX_TAG_LEN characters), or a finished `[name]` waiting
    for the one character that tells a tag from a link. A `[` that cannot become a tag (too long, nested,
    or across a newline) is plain text. With `final=True` nothing more is coming: a finished `[name]` is a
    tag, and a cut-off `[na` is dropped, since it was a tag that never finished.
    """
    pieces: list[tuple[str, bool]] = []
    pos = 0
    while (start := text.find("[", pos)) >= 0:
        if start > pos:
            pieces.append((text[pos:start], False))
        if tag := _TAG.match(text, start):
            end = tag.end()
            if end == len(text) and not final:
                return pieces, text[start:]
            if end < len(text) and text[end] == "(":
                pieces.append((tag.group(), False))
            else:
                pieces.append((tag.group(1), True))
            pos = end
        elif _OPEN.match(text, start):
            return pieces, "" if final else text[start:]
        else:
            pieces.append(("[", False))
            pos = start + 1
    if pos < len(text):
        pieces.append((text[pos:], False))
    return pieces, ""


# ------------------------------------------------------------------ stages of tts_text_transforms

def _encode_pieces(pieces, allowed, on_tag) -> str:
    out: list[str] = []
    for text, is_tag in pieces:
        if not is_tag:
            # a private-use character that arrives in the text must not decode into a tag the vocabulary
            # never allowed, so it is dropped here
            out.append(_PRIVATE_USE.sub("", text))
        elif text in allowed:
            out.append(_placeholder(text))
            if on_tag is not None:
                try:
                    on_tag(text)
                except Exception:  # a counter must never be able to silence the reply
                    logger.warning("on_tag callback failed for %r", text, exc_info=True)
    return "".join(out)


def encode_tags(
    vocabulary: Callable[[], frozenset[str]],
    on_tag: Callable[[str], None] | None = None,
) -> Callable[[AsyncIterable[str]], AsyncIterator[str]]:
    """Stage 1: hide the allowed `[tag]`s from the stock filters; drop every other tag.

    Streams: it holds back at most one partial `[...` (MAX_TAG_LEN characters) with one character of
    look-ahead, so a tag is told from a markdown link (`](`), which passes through untouched. Allowed tags
    become placeholders and `on_tag(name)` is called for each; unknown tags are dropped so the voice never
    reads them; an empty vocabulary (standard, fallback, OpenAI) drops every tag.

    Bracketed text longer than MAX_TAG_LEN is not a tag and is left for the stock filter (which holds it
    until the stream ends, and the TTS then reads it): the prompt's cue rules say to use no other bracketed
    text.
    """
    async def encode(text: AsyncIterable[str]) -> AsyncIterator[str]:
        # Read once per reply: the TTS for an utterance is chosen when it starts, so a voice-mode flip
        # mid-reply must not let tags reach a TTS that would read them out.
        allowed = vocabulary()
        held = ""
        async for chunk in text:
            pieces, held = _scan(held + chunk, final=False)
            if out := _encode_pieces(pieces, allowed, on_tag):
                yield out
        pieces, _ = _scan(held, final=True)
        if out := _encode_pieces(pieces, allowed, on_tag):
            yield out

    return encode


async def decode_tags(text: AsyncIterable[str]) -> AsyncIterator[str]:
    """Stage 4: turn each placeholder back into `[tag]` for the TTS. A placeholder is one character, so no
    chunk boundary can split it."""
    async for chunk in text:
        if out := chunk.translate(_DECODE):
            yield out


# ------------------------------------------------------------------ stripping (transcript, trace previews)

class _TagStripper:
    """Removes tags from text fed in chunks, and the space a removed tag would leave doubled."""

    def __init__(self) -> None:
        self._held = ""
        self._after_space = True   # nothing emitted yet, or what was emitted ends in whitespace
        self._skip_space = False   # a tag was just removed after whitespace: drop the space that follows it

    def feed(self, chunk: str) -> str:
        # what is still held when the text ends is a tag (or the start of one) that is removed anyway
        pieces, self._held = _scan(self._held + chunk, final=False)
        out: list[str] = []
        for text, is_tag in pieces:
            if is_tag:
                self._skip_space = self._after_space
                continue
            if self._skip_space:
                text = text.lstrip(" \t")
            if text:
                self._skip_space = False
                self._after_space = text[-1].isspace()
                out.append(text)
        return "".join(out)


def strip_tags(text: str) -> str:
    """`text` without any `[tag]`: for the transcript and for trace previews. Markdown links are not tags."""
    return _TagStripper().feed(text).strip()


async def strip_tags_stream(text: AsyncIterable[str]) -> AsyncIterator[str]:
    """Streaming `strip_tags`: tags split across chunks are removed too. Holds back the same one partial
    `[...` as `encode_tags`; it does not trim the ends of the text."""
    stripper = _TagStripper()
    async for chunk in text:
        if out := stripper.feed(chunk):
            yield out
