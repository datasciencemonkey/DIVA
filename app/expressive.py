"""Expressive audio tags (spec §7.8): keep `[whispers]`-style cues out of the stock TTS filters, out of the
transcript, and out of the voice when the TTS can't perform them.

The stock `filter_markdown` treats any `[` that is not a complete `[text](url)` link as unfinished markdown
and holds back the rest of the reply until the stream ends (C7), which would stall streaming TTS on the first
cue. So the session installs

    tts_text_transforms = [encode_tags(vocabulary, on_tag), "filter_markdown", "filter_emoji", decode_tags]

`encode_tags` rewrites each allowed `[tag]` to one opaque private-use character (U+E000..U+F8FF) before the
stock filters run, and drops every other bracketed group: an unknown tag, or a stage direction such as
`[whispers menacingly into the microphone]`. Neither stock filter matches that block, so a placeholder passes
straight through them, and `decode_tags` turns it back into `[tag]` for the TTS. The vocabulary is the only
thing that can put bracketed text on the air (matched case- and spacing-insensitively; only members ever reach
the voice), which is G13: a cue the TTS does not perform is never read aloud.

Pure: standard library only, no LiveKit import. The stock filters only appear in the tests.
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable

from src.expressive_palette import PALETTE, flatten

logger = logging.getLogger(__name__)

# The audio tags the Halloween voice performs: the verified palette (src/expressive_palette.py), flattened. Left
# out on purpose: crying (distressing), sound effects such as gunshot or explosion (startling, off-brand) and
# accent tags (risk of offensive impressions).
SPOOKY_TAGS: tuple[str, ...] = flatten(PALETTE)

# The longest `[name]`, brackets included, that can be a tag: a name is at most MAX_TAG_LEN - 2 characters.
MAX_TAG_LEN = 34
# The longest closed `[...]`, brackets included, that is dropped when it is neither a tag nor a markdown link
# (G13: a stage direction must never be read out). Also the most that is ever held back waiting for a `]`,
# so it bounds the delay a stray `[` can cause.
MAX_GROUP_LEN = 120

# Deliberately not matched (see `_scan`): a group nested in another, one that spans a newline, one longer than
# MAX_GROUP_LEN. A tag glued to a parenthetical (`[sighs](softly)`) is read as a markdown link.
_INNER = rf"[^\[\]\n]{{0,{MAX_GROUP_LEN - 2}}}"
_GROUP = re.compile(rf"\[({_INNER})\]")   # a closed `[...]`
_OPEN = re.compile(rf"\[{_INNER}\Z")      # `[...` at the end of the text: could still be closed

# ------------------------------------------------------------------ placeholders

_PRIVATE_USE = re.compile(r"[\ue000-\uf8ff]")
_PLACEHOLDER: dict[str, str] = {}  # tag name -> its private-use character
_DECODE: dict[int, str] = {}       # that character's ordinal -> "[name]" (a str.translate table)


def canonical_tag(raw: str) -> str:
    """The form a tag is matched in: lowercase, inner whitespace collapsed to single spaces, ends trimmed. The
    model may write `[Whispers]` or `[ building   tension ]`; both reach the voice as the canonical tag."""
    return " ".join(raw.split()).lower()


def _placeholder(name: str) -> str:
    """The private-use character standing in for `[name]`; one per name, for the life of the process.

    Names come from the server-side vocabulary (a few dozen), never from the model, so the 6,400 characters
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

    `is_tag` marks a closed `[...]` of at most MAX_GROUP_LEN characters that is not followed by `(`: a tag if
    it is a short allowed name, otherwise something the caller drops. That is what keeps a stage direction
    (`[whispers menacingly into the microphone]`) off the air. `[text](url)` is a markdown link, which stays
    text however long its text (up to MAX_GROUP_LEN).

    The tail is at most one partial `[...` (up to MAX_GROUP_LEN - 1 characters), or a closed group waiting
    for the one character that tells a tag from a link. With `final=True` nothing more is coming: a closed
    group counts, and a cut-off `[...` is dropped if it is at most MAX_TAG_LEN characters (a tag that never
    finished: at most 33 characters after the `[` are lost) and kept as text if longer (a stray `[`).

    Not recognised, by design (low likelihood; the prompt's cue rules ask for no other bracketed text):
      - a group nested in another (`[a [b]]`): the outer `[` is plain text, the inner group is handled;
      - a group that spans a newline: plain text, so a stray `[` cannot hold a whole paragraph;
      - a group longer than MAX_GROUP_LEN: plain text, left for the stock filter;
      - a tag glued to a parenthetical (`[sighs](softly)`): it parses as a markdown link, so `sighs` is spoken.
    """
    pieces: list[tuple[str, bool]] = []
    pos = 0
    while (start := text.find("[", pos)) >= 0:
        if start > pos:
            pieces.append((text[pos:start], False))
        if group := _GROUP.match(text, start):
            end = group.end()
            if end == len(text) and not final:
                return pieces, text[start:]
            if end < len(text) and text[end] == "(":
                pieces.append((group.group(), False))
            else:
                pieces.append((group.group(1), True))
            pos = end
        elif _OPEN.match(text, start):
            if not final:
                return pieces, text[start:]
            if len(text) - start > MAX_TAG_LEN:
                pieces.append((text[start:], False))  # too long to be a cut-off tag: a stray `[`, keep its text
            return pieces, ""
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
        # a group: a short allowed name is a tag; anything else (an unknown tag, a direction) is dropped
        elif len(name := canonical_tag(text)) <= MAX_TAG_LEN - 2 and name in allowed:
            out.append(_placeholder(name))
            if on_tag is not None:
                try:
                    on_tag(name)
                except Exception:  # a counter must never be able to silence the reply
                    logger.warning("on_tag callback failed for %r", name, exc_info=True)
    return "".join(out)


def encode_tags(
    vocabulary: Callable[[], frozenset[str]],
    on_tag: Callable[[str], None] | None = None,
) -> Callable[[AsyncIterable[str]], AsyncIterator[str]]:
    """Stage 1: hide the allowed `[tag]`s from the stock filters; drop every other bracketed group.

    Streams: it holds back at most one partial `[...` (MAX_GROUP_LEN characters) with one character of
    look-ahead, so a tag is told from a markdown link (`](`), which passes through untouched. Allowed tags
    become placeholders and `on_tag(name)` is called for each, with the canonical name (`canonical_tag`: the
    model's case and spacing are forgiven). Every other closed `[...]` up to MAX_GROUP_LEN characters (an
    unknown tag, or a stage direction) is dropped so the voice never reads it, and an empty vocabulary
    (standard, fallback, OpenAI) drops every tag. What `_scan` cannot judge is left as text for the stock
    filter (see its list); the prompt's cue rules ask for no other bracketed text.
    """
    async def encode(text: AsyncIterable[str]) -> AsyncIterator[str]:
        # Read once per reply: the TTS for an utterance is chosen when it starts, so a voice-mode flip
        # mid-reply must not let tags reach a TTS that would read them out.
        allowed = frozenset(canonical_tag(t) for t in vocabulary())
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

    def feed(self, chunk: str, *, final: bool = False) -> str:
        pieces, self._held = _scan(self._held + chunk, final=final)
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
    """`text` without any `[tag]` or bracketed direction (the groups `encode_tags` drops, so the caller never
    reads what the voice does not say): for the transcript and for trace previews. Markdown links stay."""
    return _TagStripper().feed(text, final=True).strip()


async def strip_tags_stream(text: AsyncIterable[str]) -> AsyncIterator[str]:
    """Streaming `strip_tags`: groups split across chunks are removed too. Holds back the same one partial
    `[...` as `encode_tags`; it does not trim the ends of the text."""
    stripper = _TagStripper()
    async for chunk in text:
        if out := stripper.feed(chunk):
            yield out
    if out := stripper.feed("", final=True):
        yield out
