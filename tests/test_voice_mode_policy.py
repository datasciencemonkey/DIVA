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


@pytest.mark.parametrize("source", ["timeout", "error", "skipped", "disabled"])
def test_resolve_verdict_with_nothing_to_act_on_passes_the_engine_source_through(source):
    miss = IntentVerdict("none", 0.0, source)
    nothing = resolve_verdict(miss, None)
    assert nothing == IntentVerdict("none", 0.0, source)  # spec §8: timeout / error stay observable
    assert nothing is not miss                            # a fresh verdict, not the caller's object
    assert decide_mode(HALLOWEEN, nothing).reason == "no_intent"


def test_resolve_verdict_never_acts_on_a_non_model_intent():
    stray = IntentVerdict("enter", 0.95, "timeout")       # an "enter" the engine did not actually answer
    assert resolve_verdict(stray, None) == IntentVerdict("none", 0.0, "timeout")


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
    # requests addressed to the assistant, in the shapes people actually use
    "can you be spooky",
    "I want you to be a little bit scarier",
    "let's go spooky",
    "okay make it spooky",
    "can we make it spooky",
    "would you mind making it spooky",
    "could you talk like a ghost",
    "please change to spooky",
    "switch to halloween",
    "can you switch to halloween please",
    "give me spooky vibes",
    "from now on do a spooky voice",                           # "from" only blocks an enter as a source
]

# Exit requests that name BOTH voices: the spooky one is what is being left, the normal one is where to.
# With the engine down these are the only thing that gets the caller out of Halloween (G12).
EXITS_NAMING_BOTH_VOICES = [
    "switch from the spooky voice to the normal voice",
    "change from the scary voice to your regular voice",
    "go from spooky to your normal voice",
    "go from the spooky voice to the normal voice",
    "replace the spooky voice with the normal voice",
    "swap the spooky voice for your normal voice",
    "trade the ghost voice for the normal voice",
    "exchange the spooky voice for the usual voice",
    "change the spooky voice into your normal voice",
    "move from the spooky voice to your normal voice",
    "come from the spooky voice back to the normal voice",
    "I prefer the normal voice to the spooky voice",
    "I prefer the normal voice over the spooky voice",
    "use the normal voice instead of the spooky voice",
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
    # requests to get the normal voice back, in the shapes people actually use
    "switch back to your regular voice",
    "I want your normal voice back",
    "can you talk in your normal voice",
    "revert to your original voice",
    "give me the normal voice",
    "the regular voice please",
    "I'd like the normal voice back",
    "can we go back to your normal voice",
    *EXITS_NAMING_BOTH_VOICES,
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
    "cancel the spooky voice",                     # "cancel" means an order in support: not an exit, but
                                                   # still never read as an enter
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


# --------------------------------------------------------------------------------------
# Topic look-alikes and mentions of the normal voice. The explicit rule is the only mechanism while the
# engine is unavailable, and an exit rule beats the engine, so a wrong rule is either a surprise flip
# into Halloween or a false exit that nothing can correct.
# --------------------------------------------------------------------------------------

# Halloween / spooky TOPICS worded with the same verbs and nouns as a voice request.
TOPIC_LOOKALIKES = [
    "can I switch to the halloween delivery slot",
    "I would like to change to the halloween package",
    "will the ghost tour be scary",
    "is the haunted house going to be scary for my kids",
    "do you sell halloween accent pillows",
    "can we switch to the halloween delivery slot",
    "please change to the ghost tour tickets",
    "let's switch to halloween candy",
    "can you switch to the halloween menu",
    "do you sell halloween vibe candles",
    "I love the halloween vibes in your store",
    "it will be scary for the kids",
    "the tour might sound spooky",
    "that haunted house will sound scary to my kids",
    "does the haunted house tour make it scary",
    "could you make the ghost costume bigger",
    "I want to make it a spooky party",
    "do you sound scary at night",                  # a question about the voice is not a request
    "how do you make it spooky",
    "do you have something spooky please",          # "spooky please" only counts as a clause of its own
    "is the ghost tour scary please tell me",
    "the tour guide will talk like a ghost",
    "I want to switch to the spooky season box",
]


@pytest.mark.parametrize("text", TOPIC_LOOKALIKES)
def test_topic_lookalikes_never_produce_a_rule_or_a_cue(text):
    assert explicit_command(text) is None
    assert not has_cue(text)


# Mentions of the normal voice (or stop-verbs next to a topic) that are not a request to drop the spooky
# voice.
NOT_EXIT_REQUESTS = [
    "your normal voice is boring, can you do a spooky one",
    "can you do a spooky voice instead of your normal voice",
    "I love your natural voice but can you do a spooky one",
    "is that your real voice",
    "what does your normal voice sound like",
    "do you have a regular voice option",
    "I like your usual voice",
    "tell me about your normal voice",
    "why is your normal voice so quiet",
    "I want it spookier than your normal voice",
    "why did you switch from your normal voice",
    "use a spooky voice rather than your normal voice",
    "instead of going back to your normal voice, keep the spooky one",   # contrast before the verb
    "unlike your normal voice this one is fun",
    "I use my normal voice for voicemail",                               # somebody else's voice
    "I prefer my regular voice when I call",
    "how do you sound in your normal voice",                             # a question, not a request
    "I want to know about your normal voice",
    "swap my voice plan for a regular voice plan",                      # the move frame skips "my"
    "replace the spooky voice with my normal voice",
    "does this sound normal",
    "cancel the halloween things I ordered",
    "cancel the scary stuff I ordered",
    "stop the scary things I ordered",
    "turn off the halloween accent lights",                              # topic words need a strong noun
    "stop the scary movie",
]


@pytest.mark.parametrize("text", NOT_EXIT_REQUESTS)
def test_a_mention_of_the_normal_voice_is_not_an_exit(text):
    verdict = explicit_command(text)
    assert verdict is None or verdict.intent != "exit"


@pytest.mark.parametrize("text", [
    "can you do a spooky voice instead of your normal voice",
    "use a spooky voice rather than your normal voice",
    "make it spooky instead of the normal voice",
])
def test_a_request_that_sets_the_normal_voice_aside_still_enters(text):
    assert explicit_command(text).intent == "enter"


@pytest.mark.parametrize("text", [
    "your normal voice is boring, can you do a spooky one",
    "can you do a spooky voice instead of your normal voice",
    "I love your natural voice but can you do a spooky one",
])
def test_an_engine_enter_is_not_lost_to_a_mention_of_the_normal_voice(text):
    verdict = resolve_verdict(_v("enter", 0.95), explicit_command(text))
    assert decide_mode(STANDARD, verdict).mode_after == HALLOWEEN


@pytest.mark.parametrize("text", [
    "is that your real voice",
    "what does your normal voice sound like",
    "cancel the halloween things I ordered",
])
def test_no_false_exit_can_be_forced_in_halloween_mode(text):
    verdict = resolve_verdict(_v("none", 0.9), explicit_command(text))
    assert decide_mode(HALLOWEEN, verdict).mode_after == HALLOWEEN


# Wanting the normal voice must never read as an enter, however the sentence names the spooky one.
# Checking only "not an exit" would let a reversed ENTER through (the caller is stranded in Halloween).
PREFERS_THE_NORMAL_VOICE = [
    "I'd rather have the normal voice than the spooky voice",
    "the normal voice is better than the spooky voice",
    "I like the normal voice more than the spooky voice",
    "the normal voice sounds nicer than the halloween voice",
    "the normal voice and the spooky voice are both fine",     # names both voices; no verb or marker
    "your regular voice and your usual halloween voice are both fine",
    "replace the spooky voice with a normal one",              # "a normal one" names no voice: verb guards
    "swap the spooky voice for a regular one",
    "trade the scary voice for a usual one",
    "exchange the spooky voice for a normal one",
    "switch from the spooky voice to a normal one",
    "a regular one is better than the spooky voice",
    *EXITS_NAMING_BOTH_VOICES,
]


@pytest.mark.parametrize("text", PREFERS_THE_NORMAL_VOICE)
def test_wanting_the_normal_voice_is_never_an_enter(text):
    verdict = explicit_command(text)
    assert verdict is None or verdict.intent != "enter"


@pytest.mark.parametrize("text", PREFERS_THE_NORMAL_VOICE)
def test_wanting_the_normal_voice_never_flips_a_standard_call_into_halloween(text):
    rule = explicit_command(text)
    verdict = resolve_verdict(IntentVerdict("none", 0.0, "error"), rule)   # engine down: only the rule
    assert decide_mode(STANDARD, verdict).mode_after == STANDARD


@pytest.mark.parametrize("text", EXITS_NAMING_BOTH_VOICES)
def test_an_exit_naming_both_voices_gets_the_caller_out_when_the_engine_is_down(text):
    verdict = resolve_verdict(IntentVerdict("none", 0.0, "error"), explicit_command(text))
    assert decide_mode(HALLOWEEN, verdict).mode_after == STANDARD


def test_voice_mode_module_is_pure():
    """No LiveKit (and no network client) is imported by the policy module."""
    code = (
        "import sys, src.policy.voice_mode; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('livekit', 'httpx', 'requests')); "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parents[1])
