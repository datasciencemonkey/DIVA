"""Offline tests for tools/expressive_llm_check.py: the pure metrics and target checks (no network)."""
import pytest

from tools import expressive_llm_check as chk

VOCAB = frozenset({"whispers", "building tension", "sighs"})
ORDER = chk.SCENARIOS[2]            # the "order" scenario, with facts


def test_measure_counts_cues_words_and_distinct_cues():
    m = chk.measure("[whispers] The door opened. [building tension] Nobody was there.", VOCAB)
    assert (m.cues, m.words) == (2, 6)
    assert m.distinct == {"whispers", "building tension"}
    assert m.per_100 == pytest.approx(100 * 2 / 6)


def test_measure_separates_exact_spelling_from_canonicalised_spelling():
    m = chk.measure("[Whispers] a [ building   tension ] b [sighs] c", VOCAB)
    assert (m.cues, m.exact, m.canonical) == (3, 1, 3)


def test_measure_flags_a_cue_that_splits_a_fact():
    m = chk.measure("Order 48[sighs]213 shipped on October 7.", VOCAB, ("48213", "October 7"))
    assert m.cues_inside_facts == 1 and m.facts_verbatim


def test_measure_facts_verbatim_is_false_when_a_fact_is_paraphrased():
    m = chk.measure("It shipped on the seventh.", VOCAB, ("October 7",))
    assert not m.facts_verbatim and m.cues_inside_facts == 0


def test_measure_counts_stage_directions():
    m = chk.measure("[whispers menacingly into the microphone] boo [whispers] hi", VOCAB)
    assert m.directions == 1 and m.canonical == 1


def test_measure_counts_stacked_cues():
    assert chk.measure("[whispers] [sighs] hi", VOCAB).stacked == 1
    assert chk.measure("[whispers] hi [sighs] there", VOCAB).stacked == 0


def test_plain_text_has_no_cues():
    m = chk.measure("Just a plain sentence here.", VOCAB)
    assert (m.cues, m.per_100, m.distinct) == (0, 0.0, frozenset())


def _row(scenario, reply, facts=()):
    return scenario, chk.measure(reply, VOCAB, facts)


def test_evaluate_passes_a_rich_compliant_suite():
    story = chk.SCENARIOS[0]
    rows = [_row(story, "[whispers] One. [building tension] Two. [sighs] Three. [whispers] Four. [sighs] Five."),
            _row(ORDER, "[whispers] Order 48213 shipped on October 7.", ORDER.facts[:2])]
    assert chk.evaluate(rows, chk.Targets(distinct_cues=3)) == []


def test_evaluate_reports_every_kind_of_miss():
    story = chk.SCENARIOS[0]
    rows = [_row(story, "A story with no cues at all, just words going on and on."),
            _row(ORDER, "Order 48[sighs]213 shipped. [explodes] [whispers]", ("48213",))]
    problems = " | ".join(chk.evaluate(rows, chk.Targets(distinct_cues=3)))
    for needle in ("cues/100", "distinct", "inside a fact", "compliance", "stacked"):
        assert needle in problems, problems


def test_scenarios_are_well_formed():
    assert len({s.key for s in chk.SCENARIOS}) == len(chk.SCENARIOS)
    assert {s.kind for s in chk.SCENARIOS} == {"story", "factual", "short"}
    for s in chk.SCENARIOS:
        assert all(f in (s.tool_result or "") for f in s.facts), s.key     # facts come from the tool result


def test_dry_run_reports_calls_and_needs_no_credentials(capsys, monkeypatch):
    for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert chk.main(["--dry-run", "--samples", "2"]) == 0
    assert "calls" in capsys.readouterr().out


def test_median_words_is_taken_per_scenario_kind():
    story = chk.SCENARIOS[0]
    rows = [_row(story, "one two three"), _row(story, "one two three four five"), _row(ORDER, "one two")]
    assert chk.median_words(rows, "story") == 4
    assert chk.median_words(rows, "factual") == 2
    assert chk.median_words(rows, "short") == 0            # no replies of that kind


def test_failure_summary_names_the_class_and_status_but_never_the_message():
    class GatewayError(Exception):
        status_code = 403

    assert chk.failure_summary(GatewayError("MESSAGE-MUST-NOT-BE-PRINTED")) == "GatewayError (HTTP 403)"
    assert chk.failure_summary(TimeoutError("MESSAGE-MUST-NOT-BE-PRINTED")) == "TimeoutError"


def test_samples_below_one_is_refused_before_anything_runs(capsys):
    assert chk.main(["--dry-run", "--samples", "0"]) == 2
    assert "at least 1" in capsys.readouterr().out


def test_a_lookup_result_reaches_the_model_as_a_tool_output_not_as_caller_text():
    # The agent's tools answer through tool outputs. A result pasted into the caller's own message is read as the
    # caller's claim, and the governance says to answer only from the tools.
    items = chk.conversation(ORDER)
    assert items[0] == {"role": "user", "content": ORDER.user}
    call, result = items[1], items[2]
    assert (call["type"], result["type"]) == ("function_call", "function_call_output")
    assert call["call_id"] == result["call_id"] and call["name"] == ORDER.tool
    assert result["output"] == ORDER.tool_result
    assert chk.conversation(chk.SCENARIOS[-1]) == chk.SCENARIOS[-1].user     # no lookup: just the caller's words


def test_evaluate_flags_a_story_reply_that_is_a_refusal_once_a_minimum_is_set():
    rows = [_row(chk.SCENARIOS[0], "[whispers] I have no story. [sighs] Sorry.")]
    assert chk.evaluate(rows, chk.Targets(distinct_cues=1)) == []                       # no minimum unless asked for
    problems = chk.evaluate(rows, chk.Targets(distinct_cues=1, story_min_words=40))
    assert any("story" in p and "40 words" in p for p in problems), problems
