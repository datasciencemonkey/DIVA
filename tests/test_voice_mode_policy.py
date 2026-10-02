import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from src.policy import voice_mode as vm
from src.policy.voice_mode import (
    STANDARD, HALLOWEEN, IntentVerdict, has_cue, explicit_command,
    resolve_verdict, decide_mode,
)

def _v(intent, conf, source="model"): return IntentVerdict(intent, conf, source)

def test_has_cue_true_on_voice_and_halloween_wording():
    assert has_cue("can you do a spooky halloween voice")
    assert has_cue("make this creepy for me")
    assert has_cue("go back to your normal voice")

def test_has_cue_false_on_halloween_topic_not_about_voice():
    assert not has_cue("are you open on halloween")
    assert not has_cue("where is my order")

def test_explicit_command_detects_enter_exit_and_negation():
    assert explicit_command("do a spooky voice").intent == "enter"
    assert explicit_command("stop the spooky voice, go normal").intent == "exit"
    assert explicit_command("please don't make it spooky") is None  # negation != enter

def test_resolve_verdict_exit_rule_always_wins():
    model = _v("enter", 0.99); rule = IntentVerdict("exit", 1.0, "rule")
    assert resolve_verdict(model, rule).intent == "exit"

def test_resolve_verdict_prefers_model_then_rule_then_none():
    assert resolve_verdict(_v("enter", 0.8), None).source == "model"
    miss = IntentVerdict("none", 0.0, "timeout")
    assert resolve_verdict(miss, IntentVerdict("enter", 1.0, "rule")).source == "rule"
    assert resolve_verdict(miss, None).intent == "none"

def test_decide_mode_table():
    assert decide_mode(STANDARD, _v("enter", 0.71)).mode_after == HALLOWEEN
    assert decide_mode(STANDARD, _v("enter", 0.69)).reason == "below_threshold"
    assert decide_mode(STANDARD, _v("enter", 0.9), entries_so_far=3).reason == "entry_cap"
    assert decide_mode(HALLOWEEN, _v("exit", 0.51)).mode_after == STANDARD
    assert decide_mode(HALLOWEEN, _v("exit", 0.49)).reason == "below_threshold"
    assert decide_mode(HALLOWEEN, _v("enter", 0.99)).reason == "already_in_mode"
    assert decide_mode(STANDARD, _v("none", 0.0)).reason == "no_intent"
    assert decide_mode(HALLOWEEN, _v("exit", 0.9), entries_so_far=99).mode_after == STANDARD  # exit uncapped


# --------------------------------------------------------------------------------------
# Supplementary tests: pin the parts of spec §7.2/§7.5 the cases above don't exercise.
# --------------------------------------------------------------------------------------

def _rule(intent): return IntentVerdict(intent, 1.0, "rule")


def test_policy_constants_are_pinned():
    assert (vm.STANDARD, vm.HALLOWEEN) == ("standard", "halloween")
    assert (vm.ENTER_THRESHOLD, vm.EXIT_THRESHOLD, vm.MAX_ENTRIES_PER_CALL) == (0.7, 0.5, 3)


def test_verdict_and_decision_are_frozen_with_optional_probabilities():
    v = IntentVerdict("enter", 0.9, "model")
    assert v.probabilities is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.intent = "exit"
    d = decide_mode(STANDARD, v)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.mode_after = STANDARD
    probs = {"enter": 0.9, "exit": 0.05, "none": 0.05}
    assert IntentVerdict("enter", 0.9, "model", probs).probabilities == probs


def test_decide_mode_thresholds_are_inclusive_and_asymmetric():
    assert decide_mode(STANDARD, _v("enter", 0.7)).reason == "enter"            # 0.7 enters
    assert decide_mode(STANDARD, _v("enter", 0.699)).reason == "below_threshold"
    assert decide_mode(HALLOWEEN, _v("exit", 0.5)).reason == "exit"             # 0.5 exits
    assert decide_mode(HALLOWEEN, _v("exit", 0.499)).reason == "below_threshold"
    # same plausible-but-unsure confidence: too weak to get in, enough to get out
    assert decide_mode(STANDARD, _v("enter", 0.6)).changed is False
    assert decide_mode(HALLOWEEN, _v("exit", 0.6)).changed is True


def test_decide_mode_entry_cap_boundary_and_precedence():
    third_entry = decide_mode(STANDARD, _v("enter", 0.9), entries_so_far=2)
    assert third_entry.mode_after == HALLOWEEN
    capped = decide_mode(STANDARD, _v("enter", 0.9), entries_so_far=3)
    assert (capped.mode_before, capped.mode_after, capped.reason) == (STANDARD, STANDARD, "entry_cap")
    # a verdict that is not confident enough is below_threshold, even when the cap is also reached
    assert decide_mode(STANDARD, _v("enter", 0.3), entries_so_far=3).reason == "below_threshold"


def test_decide_mode_already_in_mode_ignores_confidence():
    assert decide_mode(STANDARD, _v("exit", 0.99)).reason == "already_in_mode"
    assert decide_mode(STANDARD, _v("exit", 0.0)).reason == "already_in_mode"
    assert decide_mode(HALLOWEEN, _v("enter", 0.1)).reason == "already_in_mode"


def test_decide_mode_no_intent_in_either_mode_and_changed_flag():
    for mode in (STANDARD, HALLOWEEN):
        d = decide_mode(mode, _v("none", 0.95))
        assert (d.mode_before, d.mode_after, d.reason, d.changed) == (mode, mode, "no_intent", False)
    entered = decide_mode(STANDARD, _v("enter", 0.95))
    assert (entered.mode_before, entered.mode_after, entered.reason, entered.changed) == (
        STANDARD, HALLOWEEN, "enter", True)
    exited = decide_mode(HALLOWEEN, _v("exit", 0.95))
    assert (exited.mode_before, exited.mode_after, exited.reason, exited.changed) == (
        HALLOWEEN, STANDARD, "exit", True)


def test_decide_mode_fails_closed_on_nan_confidence():
    nan = float("nan")
    assert decide_mode(STANDARD, _v("enter", nan)).changed is False
    assert decide_mode(HALLOWEEN, _v("exit", nan)).changed is False


def test_exit_is_honored_at_every_entry_count():
    for entries in (0, 1, 2, 3, 4, 99):
        assert decide_mode(HALLOWEEN, _v("exit", 0.5), entries_so_far=entries).mode_after == STANDARD


def test_resolve_verdict_exit_rule_beats_engine_whatever_it_said():
    for model in (_v("enter", 0.99), _v("none", 0.99), _v("exit", 0.6),
                  IntentVerdict("none", 0.0, "timeout"), IntentVerdict("none", 0.0, "error")):
        assert resolve_verdict(model, _rule("exit")) == _rule("exit")


def test_resolve_verdict_engine_answer_beats_a_non_exit_rule():
    # the engine answered "none" for an utterance the rule read as enter: trust the engine
    engine_none = _v("none", 0.9)
    assert resolve_verdict(engine_none, _rule("enter")) is engine_none
    engine_enter = _v("enter", 0.8)
    assert resolve_verdict(engine_enter, _rule("enter")) is engine_enter


@pytest.mark.parametrize("source", ["timeout", "error", "skipped", "disabled"])
def test_resolve_verdict_falls_back_to_the_rule_when_the_engine_did_not_answer(source):
    rule = _rule("enter")
    assert resolve_verdict(IntentVerdict("none", 0.0, source), rule) is rule


def test_resolve_verdict_with_nothing_to_act_on_is_a_none_verdict():
    nothing = resolve_verdict(IntentVerdict("none", 0.0, "skipped"), None)
    assert nothing == IntentVerdict("none", 0.0, "none")
    assert decide_mode(HALLOWEEN, nothing).reason == "no_intent"


ENTER_REQUESTS = [
    "do a spooky voice",
    "Can you do a spooky Halloween voice? Also, where's my order?",
    "make it spooky",
    "make this creepy for me",
    "make your voice a little more scary",
    "sound scary please",
    "be more spooky",
    "talk to me like a ghost",
    "switch to halloween mode",
    "use your haunted voice",
    "give me that spine-chilling voice",
    "spooky please",
    "I'm VIP, spooky please",
    "no, do a spooky voice",                                   # negation does not leak across a comma
    "I don't want the normal voice, I want a spooky voice",    # ...nor does a negated exit phrase
]

EXIT_REQUESTS = [
    "stop the spooky voice, go normal",
    "go back to your normal voice",
    "Normal voice please.",
    "use your regular voice",
    "stop the halloween voice",
    "stop being spooky",
    "stop making it scary",
    "turn off the spooky voice",
    "turn halloween mode off",
    "no more scary voice",
    "enough with the creepy stuff",
    "drop the spooky act",
    "please talk normally",
    "go back to your normal voice, not the spooky voice",
    "do a spooky voice, actually no, stop the spooky voice",  # exit wins over enter in one utterance
    "no stop the spooky voice",                               # a bare "no" does not cancel an exit
]

NOT_COMMANDS = [
    "please don't make it spooky",
    "do not do a spooky voice",
    "I don't want a spooky voice",
    "no spooky voice",
    "never mind the spooky voice",
    "don't go back to your normal voice",         # a negated exit is not an exit either
    "I'd rather not have the halloween voice",
    "stop pretending to have a spooky voice",      # an unparsed stop request is never an enter
    "are you open on halloween",
    "what time does the halloween sale end",
    "where is my order",
    "please stop",                                 # a bare "stop" is the engine's call
    "",
    "   ",
]


@pytest.mark.parametrize("text", ENTER_REQUESTS)
def test_explicit_command_enter(text):
    v = explicit_command(text)
    assert v is not None and v.intent == "enter"
    assert (v.confidence, v.source, v.probabilities) == (1.0, "rule", None)


@pytest.mark.parametrize("text", EXIT_REQUESTS)
def test_explicit_command_exit(text):
    v = explicit_command(text)
    assert v is not None and v.intent == "exit"
    assert (v.confidence, v.source, v.probabilities) == (1.0, "rule", None)


@pytest.mark.parametrize("text", NOT_COMMANDS)
def test_explicit_command_none_on_negation_or_no_match(text):
    assert explicit_command(text) is None


HALLOWEEN_TOPICS_AND_ORDINARY_CALLS = [
    "are you open on halloween",
    "what time does the halloween sale end",
    "do you have any halloween decorations in stock",
    "will my halloween costume arrive before friday",
    "can you tell me if the store will be open on halloween",
    "could you make sure my halloween order shipped",
    "can you change my order to halloween colors",
    "that sounds good, is the halloween sale still on",
    "I love your voice, are you open on halloween",
    "where is my order",
    "please stop my subscription",
    "can we go back to my order",
    "it was back to normal after the outage",
    "",
    "   ",
]


@pytest.mark.parametrize("text", HALLOWEEN_TOPICS_AND_ORDINARY_CALLS)
def test_has_cue_false_for_halloween_topics_and_ordinary_calls(text):
    assert not has_cue(text)


@pytest.mark.parametrize("text", [
    *ENTER_REQUESTS,
    *EXIT_REQUESTS,
    "please don't make it spooky",                 # still about the voice: worth waiting for the verdict
    "change your voice to something creepy",       # phrasing no rule claims; the engine decides
    "a spooky voice would be fun",
])
def test_has_cue_true_for_voice_requests_in_either_direction(text):
    assert has_cue(text)


def test_every_explicit_command_is_also_a_cue():
    for text in (*ENTER_REQUESTS, *EXIT_REQUESTS):
        assert explicit_command(text) is not None and has_cue(text), text


def test_voice_mode_module_is_pure():
    """No LiveKit (and no network client) is imported by the policy module."""
    code = (
        "import sys, src.policy.voice_mode; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('livekit', 'httpx', 'requests')); "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parents[1])
