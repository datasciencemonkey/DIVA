"""The expressive-cue palette (src/expressive_palette.py): data invariants, helpers, and parity with the
contract docs/discovery/expressive-tags-contract.md that verified it."""
import re
from pathlib import Path

from src.expressive_palette import GROUP_HINTS, OTHER, PALETTE, flatten, grouped

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = ("breath", "volume", "emotion", "pacing")


def test_categories_are_known_non_empty_and_in_display_order():
    assert PALETTE, "the palette is empty"
    names = tuple(PALETTE)
    assert set(names) <= set(CATEGORIES)
    assert names == tuple(c for c in CATEGORIES if c in PALETTE)
    assert all(PALETTE[c] for c in PALETTE)


def test_every_tag_is_a_short_lowercase_name_the_bracket_scan_can_carry():
    for tag in flatten():
        assert re.fullmatch(r"[a-z]+(?: [a-z]+)?", tag), tag   # one or two words, letters and single spaces only
        assert len(tag) <= 32, tag                              # MAX_TAG_LEN - 2


def test_no_tag_appears_twice():
    tags = flatten()
    assert len(tags) == len(set(tags))


def test_every_category_has_a_hint():
    assert set(GROUP_HINTS) >= set(PALETTE)
    assert all(GROUP_HINTS[c].strip() for c in PALETTE)


def test_flatten_keeps_palette_order():
    assert flatten() == tuple(t for members in PALETTE.values() for t in members)
    assert flatten({"a": ("x", "y"), "b": ("z",)}) == ("x", "y", "z")


def test_grouped_keeps_only_the_requested_tags_in_palette_order():
    some = (flatten()[-1], flatten()[0])                        # given out of order
    out = grouped(some)
    assert [t for _, members in out for t in members] == [t for t in flatten() if t in some]


def test_grouped_drops_empty_categories():
    first = next(iter(PALETTE))
    assert [c for c, _ in grouped((PALETTE[first][0],))] == [first]


def test_grouped_collects_tags_the_palette_does_not_know_last_and_sorted():
    out = grouped(("zzz", flatten()[0], "aaa"))
    assert out[-1] == (OTHER, ("aaa", "zzz"))
    assert out[0][1] == (flatten()[0],)


def test_grouped_of_nothing_is_empty():
    assert grouped(()) == []


def test_palette_matches_the_verified_contract():
    text = (ROOT / "docs" / "discovery" / "expressive-tags-contract.md").read_text(encoding="utf-8")
    block = re.search(r"```palette\n(.*?)```", text, re.S)
    assert block, "the contract has no ```palette block"
    expected = {}
    for line in block.group(1).strip().splitlines():
        category, tags = line.split(":", 1)
        expected[category.strip()] = tuple(t.strip() for t in tags.split("|") if t.strip())
    assert PALETTE == expected
