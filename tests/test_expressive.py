"""Expressive-tag pipeline (spec §7.8): encode_tags / decode_tags / strip_tags.

The chain under test is the one the session installs as `tts_text_transforms`:

    [encode_tags(vocabulary, on_tag), "filter_markdown", "filter_emoji", decode_tags]

`encode_tags` hides allowed `[tag]`s behind private-use placeholders so the stock filters cannot stall on a
bare `[` (C7); decode_tags restores them for the TTS. The stock filters are imported lazily, inside the tests
that pin behavior against them: the module under test must itself import without LiveKit.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from app.expressive import SPOOKY_TAGS, decode_tags, encode_tags, strip_tags, strip_tags_stream


def all_tags():
    return frozenset(SPOOKY_TAGS)


def no_tags():
    return frozenset()


async def collect(stage, chunks):
    """Everything `stage` yields for `chunks`, joined."""
    async def gen():
        for chunk in chunks:
            yield chunk
    return "".join([piece async for piece in stage(gen())])


async def consumed_at_each_piece(stage, chunks):
    """[(chunks read so far, piece)] for every piece `stage` yields: how early does text get out?"""
    consumed = 0

    async def gen():
        nonlocal consumed
        for chunk in chunks:
            consumed += 1
            yield chunk
    return [(consumed, piece) async for piece in stage(gen())]


def has_pua(s):
    return any("\ue000" <= ch <= "\uf8ff" for ch in s)


# A theatrical stage direction: 39 characters inside the brackets, so it can never be a tag (32 at most)
DIRECTION = "[whispers menacingly into the microphone]"
# A markdown link whose text (95 characters) is far past the tag cap but inside the drop limit
LONG_LINK = ("[our complete returns policy, including the exceptions for opened items and for final sale goods]"
             "(http://x/returns)")


# ------------------------------------------------------------------ vocabulary

def test_spooky_vocabulary_is_the_spec_set():
    # crying, sound effects (gunshot, explosion) and accent tags are left out on purpose (§7.8)
    assert SPOOKY_TAGS == ("whispers", "sighs", "laughs", "mischievously", "nervously", "exhales",
                           "inhales deeply")


# ------------------------------------------------------------------ encode / decode

@pytest.mark.asyncio
async def test_allowed_tag_becomes_pua_placeholder_then_decodes_back():
    mid = await collect(encode_tags(all_tags), ["[whispers] boo"])
    assert "[whispers]" not in mid and "boo" in mid and has_pua(mid)
    assert await collect(decode_tags, [mid]) == "[whispers] boo"


@pytest.mark.asyncio
async def test_unknown_tag_dropped():
    mid = await collect(encode_tags(all_tags), ["[explosion] boo"])
    assert mid == " boo"   # the tag and nothing else; the voice never reads it, and nothing decodes to it


@pytest.mark.asyncio
async def test_empty_brackets_are_dropped_like_any_unknown_tag():
    # left in, a bare `[` would send the stock filter back to holding the rest of the reply
    assert await collect(encode_tags(all_tags), ["a [] b"]) == "a  b"


@pytest.mark.asyncio
async def test_empty_vocabulary_drops_all_tags():
    mid = await collect(encode_tags(no_tags), ["[whispers] boo [sighs]"])
    assert mid == " boo " and not has_pua(mid)
    assert "[" not in await collect(decode_tags, [mid])


@pytest.mark.asyncio
async def test_tag_split_across_chunks_reassembles():
    mid = await collect(encode_tags(all_tags), ["[whi", "spers] hi"])
    assert await collect(decode_tags, [mid]) == "[whispers] hi"


@pytest.mark.asyncio
async def test_multi_word_tag_round_trips():
    mid = await collect(encode_tags(all_tags), ["[inhales deeply] now"])
    assert await collect(decode_tags, [mid]) == "[inhales deeply] now"


@pytest.mark.asyncio
async def test_markdown_link_passes_through_untouched():
    assert "[text](http://x)" in await collect(encode_tags(all_tags), ["see [text](http://x)"])


@pytest.mark.asyncio
async def test_markdown_link_split_right_after_the_closing_bracket_passes_through():
    # the look-ahead case: `[text]` alone could still be a tag, so it waits for the next chunk's first char
    chunks = ["see [text]", "(http://x) and [laughs]", " ok"]
    out = await collect(encode_tags(all_tags), chunks)
    assert out.startswith("see [text](http://x) and ") and "[laughs]" not in out and has_pua(out)


@pytest.mark.asyncio
async def test_tag_at_the_very_end_of_the_stream_is_still_encoded():
    mid = await collect(encode_tags(all_tags), ["goodbye [sighs]"])   # no character ever follows `]`
    assert has_pua(mid) and await collect(decode_tags, [mid]) == "goodbye [sighs]"


@pytest.mark.asyncio
async def test_tag_cut_off_by_the_end_of_the_stream_is_dropped_not_spoken():
    assert await collect(encode_tags(all_tags), ["so sorry [whis"]) == "so sorry "


@pytest.mark.asyncio
async def test_a_tag_name_can_be_32_characters_and_no_longer():
    longest, too_long = "a" * 32, "b" * 33   # `[` + 32 + `]` is 34; one more character is not a tag
    stage = encode_tags(lambda: frozenset({longest, too_long}))
    mid = await collect(stage, [f"[{longest}] x [{too_long}] y"])
    assert has_pua(mid)   # the 32-character name is a tag, exactly as before
    decoded = await collect(decode_tags, [mid])
    assert " ".join(decoded.split()) == f"[{longest}] x y"   # the 33-character one is not, even when listed: dropped


@pytest.mark.asyncio
@pytest.mark.parametrize("vocabulary", [
    pytest.param(all_tags, id="halloween_vocabulary"),
    pytest.param(no_tags, id="empty_vocabulary"),
    pytest.param(lambda: frozenset({DIRECTION[1:-1]}), id="vocabulary_listing_it"),
])
async def test_a_direction_too_long_to_be_a_tag_is_dropped_not_spoken(vocabulary):
    # G13: the vocabulary is the only way onto the air, and a group this long can never be in it
    seen = []
    reply = f"{DIRECTION} Welcome, mortal. {DIRECTION}"   # one at each end: the last has no character after it
    mid = await collect(encode_tags(vocabulary, on_tag=seen.append), [reply])
    assert " ".join(mid.split()) == "Welcome, mortal."
    assert not has_pua(mid) and seen == []


@pytest.mark.asyncio
async def test_a_bracketed_group_is_dropped_up_to_120_characters_and_left_as_text_beyond():
    dropped, kept = "d" * 118, "k" * 119   # `[` + 118 + `]` is 120
    assert " ".join((await collect(encode_tags(all_tags), [f"[{dropped}] x"])).split()) == "x"
    assert await collect(encode_tags(all_tags), [f"[{kept}] x"]) == f"[{kept}] x"


@pytest.mark.asyncio
@pytest.mark.parametrize("vocabulary", [all_tags, no_tags])
async def test_a_markdown_link_with_long_text_still_passes(vocabulary):
    text = f"see {LONG_LINK} now"
    assert await collect(encode_tags(vocabulary), [text]) == text


@pytest.mark.asyncio
async def test_a_stray_open_bracket_at_the_end_swallows_at_most_33_characters():
    swallowed, kept = "x" * 33, "y" * 34   # `[` + 33 is 34 characters: the longest cut-off tag that is dropped
    assert await collect(encode_tags(all_tags), [f"so sorry [{swallowed}"]) == "so sorry "
    assert await collect(encode_tags(all_tags), [f"so sorry [{kept}"]) == f"so sorry [{kept}"


@pytest.mark.asyncio
async def test_the_spooky_tags_get_stable_placeholders_from_u_e000_up():
    mid = await collect(encode_tags(all_tags), [" ".join(f"[{tag}]" for tag in SPOOKY_TAGS)])
    assert mid == " ".join(chr(0xE000 + index) for index in range(len(SPOOKY_TAGS)))


@pytest.mark.asyncio
async def test_a_bracket_group_never_spans_a_line():
    # tags are one short phrase; this also keeps a stray `[` at the end of a line from holding the next one
    assert await collect(encode_tags(all_tags), ["[two\nlines] x"]) == "[two\nlines] x"


@pytest.mark.asyncio
async def test_a_tag_after_an_unfinished_bracket_is_still_found():
    mid = await collect(encode_tags(all_tags), ["nested [oops [whispers] fine"])
    assert has_pua(mid) and await collect(decode_tags, [mid]) == "nested [oops [whispers] fine"


@pytest.mark.asyncio
async def test_a_tag_outside_the_spooky_set_round_trips_when_the_vocabulary_allows_it():
    stage = encode_tags(lambda: frozenset({"growls"}))
    mid = await collect(stage, ["[growls] grr [whispers] hm"])
    assert await collect(decode_tags, [mid]) == "[growls] grr  hm"   # `whispers` is not in this vocabulary


@pytest.mark.asyncio
async def test_decode_leaves_ordinary_text_alone():
    assert await collect(decode_tags, ["plain ", "text [like] this"]) == "plain text [like] this"


@pytest.mark.asyncio
async def test_private_use_characters_in_the_text_cannot_forge_a_tag():
    # Only the vocabulary may put a tag on the air: a private-use character that arrives in the text
    # (the model, a tool result) is dropped, never decoded into `[whispers]` for a TTS that would read it out.
    forged = "\ue000\ue001 hi"
    for vocabulary in (no_tags, all_tags):
        mid = await collect(encode_tags(vocabulary), [forged])
        assert not has_pua(mid)
        assert await collect(decode_tags, [mid]) == " hi"


@pytest.mark.asyncio
async def test_vocabulary_is_read_once_per_reply():
    # A reply's TTS is chosen when it starts, so a voice-mode flip mid-reply must not change which tags
    # that reply may carry; the next reply reads the new vocabulary.
    current = {"vocabulary": frozenset(SPOOKY_TAGS)}
    stage = encode_tags(lambda: current["vocabulary"])

    async def flipping_reply():
        yield "[whispers] one "
        current["vocabulary"] = frozenset()
        yield "[sighs] two"

    mid = "".join([piece async for piece in stage(flipping_reply())])
    assert await collect(decode_tags, [mid]) == "[whispers] one [sighs] two"
    assert await collect(stage, ["[sighs] three"]) == " three"


# ------------------------------------------------------------------ on_tag (feeds ug.expressive_tags)

@pytest.mark.asyncio
async def test_on_tag_callback_counts_allowed_tags():
    seen = []
    await collect(encode_tags(all_tags, on_tag=seen.append), ["[whispers] a [sighs] b [explosion] c"])
    assert seen == ["whispers", "sighs"]   # allowed counted; unknown not


@pytest.mark.asyncio
async def test_no_tags_are_counted_when_the_vocabulary_is_empty():
    seen = []
    await collect(encode_tags(no_tags, on_tag=seen.append), ["[whispers] a [sighs] b"])
    assert seen == []


@pytest.mark.asyncio
async def test_failing_on_tag_callback_never_breaks_the_reply():
    def boom(name):
        raise RuntimeError("telemetry is down")

    mid = await collect(encode_tags(all_tags, on_tag=boom), ["[whispers] boo"])
    assert await collect(decode_tags, [mid]) == "[whispers] boo"


# ------------------------------------------------------------------ chunk boundaries

TEXTS = [
    "[whispers] boo",
    "plain text, no brackets at all",
    "a [sighs] b [explosion] c [inhales deeply] d",
    "see [the docs](http://x/y) then [laughs] done",
    "[sighs][laughs]back to back",
    "tail tag [whispers]",
    "cut off [whis",
    "nested [oops [whispers] fine",
    "empty [] and [two\nlines] and \ue000 forged",
    "[" + "z" * 40 + "] too long",
    "stray ] bracket and [ lone",
    pytest.param(f"{DIRECTION} Welcome, mortal. [sighs] Boo! {DIRECTION}", id="direction_too_long_to_be_a_tag"),
    pytest.param(f"see {LONG_LINK} now", id="markdown_link_with_long_text"),
    pytest.param("so sorry [" + "x" * 33, id="cut_off_bracket_dropped"),
    pytest.param("so sorry [" + "y" * 34, id="cut_off_bracket_kept"),
    pytest.param("[" + "d" * 118 + "] x", id="group_at_the_drop_limit"),
    pytest.param("[" + "k" * 119 + "] x", id="group_past_the_drop_limit"),
]


def chunkings(text):
    """Every fixed-size cut of `text`, and every two-way cut with an empty chunk in the middle."""
    for size in range(1, len(text) + 1):
        yield [text[i:i + size] for i in range(0, len(text), size)]
    for cut in range(len(text) + 1):
        yield [text[:cut], "", text[cut:]]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", TEXTS)
async def test_output_does_not_depend_on_where_the_stream_is_cut(text):
    encoded = await collect(encode_tags(all_tags), [text])
    stripped = strip_tags(text)
    for chunks in chunkings(text):
        assert await collect(encode_tags(all_tags), chunks) == encoded, chunks
        assert (await collect(strip_tags_stream, chunks)).strip() == stripped, chunks


# ------------------------------------------------------------------ the stock filters (LiveKit 1.8.3)

@pytest.mark.asyncio
async def test_placeholder_survives_both_stock_filters():
    from livekit.agents.voice.transcription.filters import filter_emoji, filter_markdown
    mid = await collect(encode_tags(all_tags), ["[whispers] boo"])
    out = await collect(lambda g: filter_emoji(filter_markdown(g)), [mid])
    assert has_pua(out)   # the placeholder is untouched by the markdown and emoji filters
    assert await collect(decode_tags, [out]) == "[whispers] boo"


@pytest.mark.asyncio
async def test_the_whole_private_use_block_passes_both_stock_filters_untouched():
    # whichever placeholder a vocabulary is given, it comes from this block
    from livekit.agents.voice.transcription.filters import filter_emoji, filter_markdown
    block = "".join(chr(code) for code in range(0xE000, 0xF8FF + 1))
    assert await collect(lambda g: filter_emoji(filter_markdown(g)), [block]) == block


@pytest.mark.asyncio
async def test_regression_our_filter_streams_bracket_text_early():
    """C7, the headline (spec §10): the stock filter_markdown HOLDS everything after a bare `[tag]` until the
    stream ends, which would stall streaming TTS. encode_tags hides the tag, so the same text streams."""
    from livekit.agents.voice.transcription.filters import filter_markdown
    chunks = ["[whispers] ", "come closer, ", "my dear."]
    everything = len(chunks)

    stock = await consumed_at_each_piece(filter_markdown, chunks)
    assert stock and {n for n, _ in stock} == {everything}   # premise: nothing leaves before the stream ends

    ours = encode_tags(all_tags)
    early = await consumed_at_each_piece(lambda text: filter_markdown(ours(text)), chunks)
    assert early[0][0] < everything   # text was already on its way before the last chunk was even read
    assert await collect(lambda text: decode_tags(filter_markdown(ours(text))), chunks) == (
        "[whispers] come closer, my dear.")


@pytest.mark.asyncio
async def test_full_chain_through_livekits_own_transform_folding():
    # the exact list the session installs; LiveKit folds plain callables beside the builtin filter names
    from livekit.agents.voice.transcription.text_transforms import _apply_text_transforms
    seen = []
    chain = [encode_tags(all_tags, on_tag=seen.append), "filter_markdown", "filter_emoji", decode_tags]

    async def reply():
        for chunk in ["[whispers] Oh **hello** there ", "\U0001F47B see [this](http://x) and ",
                      "[explosion]", "[laughs] bye!"]:
            yield chunk

    out = "".join([piece async for piece in _apply_text_transforms(reply(), chain)])
    assert " ".join(out.split()) == "[whispers] Oh hello there see this and [laughs] bye!"
    assert seen == ["whispers", "laughs"]


@pytest.mark.asyncio
async def test_standard_voice_chain_lets_no_tag_reach_the_tts():
    # G13: with an empty vocabulary (standard / fallback / OpenAI) nothing bracketed is ever spoken
    from livekit.agents.voice.transcription.text_transforms import _apply_text_transforms
    chain = [encode_tags(no_tags), "filter_markdown", "filter_emoji", decode_tags]

    async def reply():
        for chunk in ["[whispers] Oh hello", " there [sighs] ", "[explosion]", " bye!"]:
            yield chunk

    out = "".join([piece async for piece in _apply_text_transforms(reply(), chain)])
    assert " ".join(out.split()) == "Oh hello there bye!"
    assert "[" not in out and not has_pua(out)


@pytest.mark.asyncio
async def test_a_direction_too_long_to_be_a_tag_does_not_stall_the_reply():
    """The over-cap half of C7: left as text, a bracketed direction makes the stock filter hold the whole
    reply AND hands the TTS the direction to read out."""
    from livekit.agents.voice.transcription.filters import filter_markdown
    chunks = ["[whispers menacingly into the ", "microphone] Welcome, ", "mortal. Come ", "closer, my dear."]

    stock = await consumed_at_each_piece(filter_markdown, chunks)
    assert {n for n, _ in stock} == {len(chunks)}   # premise: nothing leaves before the stream ends

    ours = encode_tags(all_tags)
    early = await consumed_at_each_piece(lambda text: filter_markdown(ours(text)), chunks)
    assert early[0][0] < len(chunks)   # the reply streams as soon as the direction has closed
    assert " ".join("".join(piece for _, piece in early).split()) == "Welcome, mortal. Come closer, my dear."


@pytest.mark.asyncio
@pytest.mark.parametrize("vocabulary, expected", [
    pytest.param(all_tags, "Welcome, mortal. [sighs] Boo!", id="halloween_vocabulary"),
    pytest.param(no_tags, "Welcome, mortal. Boo!", id="empty_vocabulary"),
])
async def test_full_chain_never_speaks_a_direction_too_long_to_be_a_tag(vocabulary, expected):
    from livekit.agents.voice.transcription.text_transforms import _apply_text_transforms
    chain = [encode_tags(vocabulary), "filter_markdown", "filter_emoji", decode_tags]

    async def reply():
        for chunk in ["[whispers menacingly into the ", "microphone] Welcome, mortal. ", "[sighs] Boo!"]:
            yield chunk

    out = "".join([piece async for piece in _apply_text_transforms(reply(), chain)])
    assert " ".join(out.split()) == expected


# ------------------------------------------------------------------ strip_tags / strip_tags_stream

def test_strip_tags_removes_all_brackets():
    assert strip_tags("[whispers] your order [sighs] shipped") == "your order shipped"


@pytest.mark.parametrize("text, expected", [
    ("no tags here", "no tags here"),
    ("hello [laughs]", "hello"),
    ("[whispers]hello", "hello"),
    ("word[sighs] next", "word next"),                            # the space after a flush tag stays
    ("a [sighs] [laughs] b", "a b"),
    ("an [explosion] of joy", "an of joy"),                       # unknown tags go too, not only the vocabulary
    ("line one\n[whispers] line two", "line one\nline two"),       # never joins lines
    ("line one [whispers]\nline two", "line one \nline two"),
    ("see [the docs](http://x) [sighs] now", "see [the docs](http://x) now"),   # a link is not a tag
])
def test_strip_tags_tidies_the_gaps_it_leaves(text, expected):
    assert strip_tags(text) == expected


def test_strip_tags_removes_a_direction_too_long_to_be_a_tag():
    # otherwise the caller would read on screen what G13 keeps off the air
    assert strip_tags(f"{DIRECTION} Welcome, mortal. [sighs] Boo! {DIRECTION}") == "Welcome, mortal. Boo!"


def test_strip_tags_keeps_a_markdown_link_with_long_text():
    assert strip_tags(f"see {LONG_LINK} now") == f"see {LONG_LINK} now"


def test_strip_tags_swallows_at_most_33_characters_after_a_stray_open_bracket():
    assert strip_tags("so sorry [" + "x" * 33) == "so sorry"
    assert strip_tags("so sorry [" + "y" * 34) == "so sorry [" + "y" * 34


@pytest.mark.asyncio
async def test_strip_tags_stream_drops_tags_split_across_chunks():
    out = await collect(strip_tags_stream, ["[whis", "pers] your order [si", "ghs] shipped"])
    assert out == "your order shipped"


@pytest.mark.asyncio
async def test_strip_tags_stream_streams_text_before_the_stream_ends():
    early = await consumed_at_each_piece(strip_tags_stream, ["[whispers] come closer, ", "my dear."])
    assert early[0][0] == 1


# ------------------------------------------------------------------ structure

def test_module_imports_without_livekit():
    """Importing the module pulls in no LiveKit (the stock filters are only ever used by the tests)."""
    code = (
        "import sys, app.expressive; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'livekit'); "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parents[1])
