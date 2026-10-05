"""Offline tests for tools/expressive_bakeoff.py: the pure logic only (no network, no credentials)."""
import io
import re
import wave

import pytest

from tools import expressive_bakeoff as bake


def _trial(**kw):
    base = dict(transcript="somebody left the back door open again tonight",
                seconds=3.0,
                base_transcript="somebody left the back door open again tonight",
                base_seconds=3.0, noise_s=0.0)
    base.update(kw)
    return bake.Trial(**base)


def test_wav_round_trip():
    pcm = b"\x01\x00" * 2400
    wav = bake.pcm_to_wav(pcm, sample_rate=24000)
    assert bake.wav_seconds(wav) == pytest.approx(0.1)
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)


def test_tag_words_drop_short_words_and_punctuation():
    assert bake.tag_words("building tension") == ["building", "tension"]
    assert bake.tag_words("sighs") == ["sighs"]
    assert bake.tag_words("a b") == []


def test_a_tag_read_aloud_is_spoken_aloud():
    heard = _trial(transcript="building tension somebody left the back door open again tonight")
    assert bake.spoken_aloud("building tension", heard)


def test_a_word_the_plain_carrier_already_has_is_not_counted_as_read_aloud():
    both = _trial(transcript="the door is soft somebody left", base_transcript="the door is soft")
    assert not bake.spoken_aloud("soft", both)


def test_verdict_prefers_spoken_aloud_over_everything():
    heard = _trial(transcript="deep breaths somebody left the back door open again tonight", seconds=9.0)
    assert bake.verdict("deep breaths", [heard]) == "spoken-aloud"


def test_verdict_performed_when_the_audio_changes_without_the_words():
    longer = _trial(seconds=4.2)
    assert bake.verdict("sighs", [longer]) == "performed"
    laugh = _trial(transcript="ha ha somebody left the back door open again tonight")
    assert bake.verdict("laughs", [laugh]) == "performed"


def test_verdict_no_audible_effect_when_nothing_changes():
    assert bake.verdict("sighs", [_trial()]) == "no-audible-effect"


def test_run_to_run_noise_raises_the_bar_for_a_duration_change():
    noisy = _trial(seconds=3.2, noise_s=0.2)          # 0.2 s apart but the plain carrier itself varies by 0.2 s
    assert bake.verdict("sighs", [noisy]) == "no-audible-effect"


def test_one_spoken_trial_condemns_the_tag():
    good, bad = _trial(seconds=4.5), _trial(transcript="pause somebody left the back door open again tonight")
    assert bake.verdict("pause", [good, bad]) == "spoken-aloud"


def test_leaked_tags_finds_tags_read_aloud_in_a_passage():
    plain = "They said she disappeared on a Tuesday. No note."
    heard = "laughing they said she disappeared on a tuesday no note"
    assert bake.leaked_tags(["laughing", "building tension"], heard, plain) == ["laughing"]


def test_candidates_are_well_formed_and_unique():
    flat = [t for tags in bake.CANDIDATES.values() for t in tags]
    assert len(flat) == len(set(flat))
    assert set(bake.CANDIDATES) <= {"breath", "volume", "emotion", "pacing"}
    for tag in flat:
        assert re.fullmatch(r"[a-z]+(?: [a-z]+)?", tag) and len(tag) <= 32, tag


def test_the_reference_example_tags_are_all_candidates():
    flat = {t for tags in bake.CANDIDATES.values() for t in tags}
    assert {"laughing", "building tension", "soft", "dismissive", "deep breaths", "whisper"} <= flat


def test_carriers_do_not_contain_any_candidate_word():
    words = {w for tags in bake.CANDIDATES.values() for t in tags for w in bake.tag_words(t)}
    for carrier in bake.CARRIERS:
        assert not words & set(bake.words(carrier)), carrier


def test_plan_counts_syntheses_and_stays_in_budget_by_default():
    tags = [t for tags in bake.CANDIDATES.values() for t in tags]
    jobs = bake.plan_jobs(tags, bake.CARRIERS)
    assert len(jobs) == len(tags) * len(bake.CARRIERS) + 2 * len(bake.CARRIERS)
    assert len(jobs) <= bake.DEFAULT_BUDGET


def test_a_job_puts_the_tag_in_front_of_the_carrier():
    jobs = bake.plan_jobs(["sighs"], bake.CARRIERS[:1])
    assert [j.text for j in jobs if j.kind == "tag"] == [f"[sighs] {bake.CARRIERS[0]}"]
    assert [j.text for j in jobs if j.kind == "base"] == [bake.CARRIERS[0]] * 2


def test_dry_run_touches_nothing_and_reports_the_count(capsys):
    assert bake.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "syntheses" in out and str(bake.DEFAULT_BUDGET) in out


def test_a_typo_in_only_is_refused_before_any_call(capsys):
    assert bake.main(["--dry-run", "--only", "[sighs]"]) == 2
    assert "not a valid tag" in capsys.readouterr().out
