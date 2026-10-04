"""Deterministic voice-mode decision policy (spec §7.2 / §7.5). Pure: no LiveKit, no network, no I/O.

Four small pieces, used in this order by the voice-mode controller:

  has_cue(text)            is this turn worth *waiting* for the decision engine's verdict?
  explicit_command(text)   high-precision regex verdict for clear commands (the engine-down safety net)
  resolve_verdict(m, r)    which verdict to act on: explicit exit rule > engine > explicit rule > none
  decide_mode(cur, v, ..)  the one deterministic transition (thresholds + entry cap)

Governance G12 (an exit is always honored) is realized here: an explicit exit rule beats the engine
(`resolve_verdict`) and an exit is never capped (`decide_mode`).

The explicit rule is the only mechanism while the engine is unavailable, and an exit rule beats the
engine, so it must only fire on *requests*: a Halloween topic ("can I switch to the halloween delivery
slot") is not one, and neither is a mere mention of the normal voice ("what does your normal voice sound
like").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STANDARD, HALLOWEEN = "standard", "halloween"

# Asymmetric on purpose: getting in takes a confident answer, getting out only a plausible one.
ENTER_THRESHOLD, EXIT_THRESHOLD, MAX_ENTRIES_PER_CALL = 0.7, 0.5, 3


@dataclass(frozen=True)
class IntentVerdict:
    intent: str        # "enter" | "exit" | "none"
    confidence: float  # 0..1 (AI Decide's confidence for the picked label; not calibrated)
    source: str        # "model" | "rule" | "timeout" | "error" | "skipped" | "disabled"
    probabilities: dict | None = None  # per-label, when the engine returns them (ai_decide)


@dataclass(frozen=True)
class ModeDecision:
    mode_before: str
    mode_after: str
    reason: str  # "enter" | "exit" | "below_threshold" | "already_in_mode" | "entry_cap" | "no_intent"

    @property
    def changed(self) -> bool:
        return self.mode_before != self.mode_after


# ---------------------------------------------------------------------------------------
# Lexicon. Spooky *adjectives* ("make it spooky") vs. words that are also ordinary topics
# ("halloween", "ghost"): the latter only count next to a strong voice noun ("halloween mode").
# ---------------------------------------------------------------------------------------
_ADJ = (r"(?:spook(?:y|ier|iest)|scar(?:y|ier|iest)|creep(?:y|ier|iest)|eeri(?:e|er|est)|ghostly|"
        r"haunt(?:ed|ing)|sinister|ominous|macabre|spine[- ]chilling)")
_TOPIC = r"(?:hallowe'?en|ghost)"
_ANY = rf"(?:{_ADJ}|{_TOPIC})"
_VOICE = r"(?:voices?|modes?|personas?|vibes?|accents?|tones?)"  # what the caller is asking to change
# beside halloween / ghost only these count: "halloween accent pillows" and "halloween vibes" are topics
_VOICE_STRONG = r"(?:voices?|personas?|modes?)"
_WORD = r"[a-z0-9'-]+"
# Glue between "make / be / sound" and the adjective: "make [it a little bit more] spooky".
_FILL = (r"(?:it|this|that|things|everything|yourself|me|us|your voice|the voice|a (?:(?:little|tiny) )?"
         r"(?:bit|little|lot)|more|so|very|really|pretty|quite|rather|slightly|somewhat|kind of|sort of|"
         r"kinda|sorta|all|just|super|extra|way|much|too|even|full)")
_OPENER = (r"(?:ok|okay|alright|all right|yes|yeah|yep|so|hey|well|um|uh|oh|right|cool|sure|then|also|"
           r"and|now|just|please|actually|maybe)")
_NEG = r"(?:don'?t|do\s+not|dont|never|not|no)"
# What may come before a request verb for it to be addressed to the assistant: the start of the clause
# (after openers, or negations, so that a negated request is still a cue and is cancelled later), or
# "can you / could we / would you mind / please / just / now / let's", or
# "you to / should / could / can". A statement or a question about something else
# ("will the ghost tour be scary", "do you sound scary") has none.
_LEAD = (rf"(?:^\s*(?:(?:{_OPENER}|{_NEG})\s+)*"
         r"|\b(?:(?:can|could|would|will|shall|should)\s+(?:you|we)(?:\s+mind)?|do\s+you\s+mind|"
         r"please|kindly|just|now|then|also|and|let'?s)\s+"
         r"|\byou\s+(?:to|should|could|can)\s+)")
_NORMAL = r"(?:normal|regular|usual|ordinary)"
_OWN = r"(?:own|real|natural|default|standard|original|old|normal|regular|usual|ordinary)"
_SELF = r"(?:voice|mode|persona|self|tone|accent)"
_NORMAL_VOICE = rf"(?:{_NORMAL}\s+{_SELF}|your\s+{_OWN}\s+{_SELF})"
# Verbs that make a mention of the normal voice a request for it
# ("go back to", "use", "I'd like ... back").
_ASK = (r"(?:go|going|come|coming|get|getting|switch|switching|change|changing|turn|revert|reverting|"
        r"return|returning|back|use|using|want|need|prefer|give|bring|put|try|"
        r"(?:i'?d|we'?d|would)\s+(?:like|love|prefer))")
# A word that may sit between that verb and the mention, unless it turns the request into something else:
# a contrast ("instead of your normal voice"), somebody else's voice ("my normal voice"), a question
# about it ("I want to know about your normal voice"), or leaving it: "from" followed, within three
# words and before any to / for / with, by the normal voice ("switch from this normal voice", "a break
# from your normal voice"). "from the spooky voice to the normal voice" is a move to it, so "from" is
# fine there.
_NOT_CONTRAST = (r"(?!(?:instead|rather|than|different|other|unlike|over|away|"
                 r"my|his|her|their|our|mine|"
                 r"know|about|ask|tell|learn|wonder|why|what|how|whether|if|when|where|who)\b"
                 rf"|from(?:\s+(?!(?:to|for|with|into)\b){_WORD}){{0,3}}?\s+{_NORMAL_VOICE}\b)")
# Verbs that move the caller from one voice to another: "switch from X to Y", "replace X with Y".
_MOVE = (r"(?:switch|switching|change|changing|go|going|move|moving|replace|replacing|"
         r"swap|swapping|trade|trading|exchange|exchanging)")
# A move has to be worded as a request, from the start of the clause: "switch from X to Y", "could you
# please swap ...", "I'd like to replace ...", "let's ...". Questions and refusals are not requests: "how
# do I switch ...", "why did you switch ...", "I'd rather you didn't switch ...", "stop switching ...".
_REQUEST = (r"(?:(?:can|could|would|will|shall|should)\s+(?:you|we)(?:\s+mind)?|do\s+you\s+mind|kindly|"
            r"let'?s|(?:i|we)(?:'d|\s+would)?\s+(?:want|need|like|love)\s+(?:you\s+)?to|i\s+wanna|"
            r"you\s+(?:should|could|can))")
_MOVE_LEAD = rf"^\s*(?:(?:{_OPENER}|{_NEG}|{_REQUEST})\s+)*"
# What is moved must be the voice (a spooky word, "voice", "persona"): "switch the phone from airplane
# mode to normal mode" moves something else.
_ABOUT_VOICE = rf"(?:{_ANY}|voices?|personas?)"
# Exit verbs. Not "cancel": in a support call that means an order ("cancel the scary stuff I ordered").
_STOP = (r"(?:stop|stopping|quit|quitting|cease|end|drop|dropping|cut|cutting|ditch|kill|skip|lose|"
         r"disable)")
_GLUE = (r"(?:the|that|this|your|all|with|being|doing|making|using|talking|speaking|sounding|acting|"
         r"in|it|like|a|an|please|just|now|already|right)")
# (not "things" or "business": "stop the scary things I ordered")
_NOUN = r"(?:act|stuff|routine|nonsense|talk)"
# The spooky "thing" a caller can stop, turn off, or have no more of. It needs a voice-ish noun:
# "stop the halloween voice" qualifies; "stop the halloween promotions" and
# "stop the scary movie" do not.
_THING = (rf"(?:{_ADJ}(?:\s+{_WORD})?\s+(?:{_VOICE}|{_NOUN})"
          rf"|{_TOPIC}(?:\s+{_WORD})?\s+(?:{_VOICE_STRONG}|act))")
_DET = r"(?:the|that|this|your)"


def _frames(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


# Phrases that ask for the spooky voice ... (frames with a lead-in mark where the command itself starts
# with the group "core", so that a negation before it still cancels it)
_ENTER = _frames(
    # "spooky voice", "scary halloween voice", "creepy vibes"
    # (filler words here exclude "-" so they cannot overlap the "[\s-]+" separators: linear, not cubic)
    rf"\b{_ADJ}\b(?:[\s-]+[a-z0-9']+){{0,2}}?[\s-]+{_VOICE}\b",
    # "halloween voice", "halloween-themed mode", "ghost persona"
    rf"\b{_TOPIC}\b(?:[\s-]+[a-z0-9']+){{0,2}}?[\s-]+{_VOICE_STRONG}\b",
    # "make it spooky", "can you be a bit scarier", "I want you to sound ghostly", "let's go spooky"
    rf"{_LEAD}(?P<core>(?:make|making|be|sound|sounding|act|acting|go)\b(?:\s+{_FILL})*\s+{_ADJ}\b)",
    # "talk like a ghost", "can you talk to me like a ghost"
    rf"{_LEAD}(?P<core>(?:talk|talking|speak|speaking|sound|sounding|act|acting|respond|answer|reply)\b"
    rf"(?:\s+{_WORD}){{0,2}}?\s+(?:like|as)\s+(?:an?\s+|the\s+)?{_ANY}\b)",
    # "switch to spooky", "change your voice to spooky", "switch to halloween mode / please / (end of
    # clause)"; not "switch to the halloween delivery slot"
    rf"{_LEAD}(?P<core>(?:switch|switching|change|changing)\b(?:\s+(?:your voice|the voice|yourself|over))*"
    rf"\s+(?:to|into)\s+(?:the\s+|an?\s+|your\s+)?"
    rf"(?:{_ADJ}\b|{_TOPIC}\b(?=\s+{_VOICE_STRONG}\b|,?\s+please\b|\s*$)))",
    # the terse "spooky please" / "a bit scarier, please" as a clause of its own
    rf"^\s*(?:(?:{_OPENER})\s+)*(?:{_FILL}\s+)*(?P<core>{_ADJ}),?\s+please\s*$",
)
# ... and phrases that ask to drop it. Mentioning the normal voice is not enough: it has to be requested.
_EXIT = _frames(
    # "go back to your normal voice", "I want your regular voice back", "use the normal voice": a request
    # verb at most 4 words before the mention, with no contrast ("instead of", "than", "from") in between
    rf"\b{_ASK}\b(?:\s+{_NOT_CONTRAST}{_WORD}){{0,4}}?\s+{_NORMAL_VOICE}\b",
    # "switch from the spooky voice to the normal voice", "replace the spooky voice with the normal
    # voice", "swap the ghost voice for your regular voice": the normal voice is where the caller is
    # being moved to, however much of the sentence is spent naming the voice being left
    rf"{_MOVE_LEAD}(?P<core>{_MOVE}\b(?:\s+{_NOT_CONTRAST}{_WORD}){{0,4}}?\s+{_ABOUT_VOICE}\b"
    rf"(?:\s+{_NOT_CONTRAST}{_WORD}){{0,4}}?\s+(?:to|for|with)\s+(?:(?:the|a|an)\s+)?{_NORMAL_VOICE}\b)",
    # "can you talk in your normal voice", "be your normal self" (these verbs also report, as in
    # "how do you sound in your normal voice", so they need the request lead-in)
    rf"{_LEAD}(?P<core>(?:be|being|sound|sounding|talk|talking|speak|speaking)\b"
    rf"(?:\s+{_NOT_CONTRAST}{_WORD}){{0,2}}?\s+{_NORMAL_VOICE}\b)",
    # ... or just the phrase as a clause of its own: "normal voice please", "the regular voice"
    rf"^\s*(?:(?:{_OPENER})\s+)*(?:(?:the|a|an)\s+)?{_NORMAL_VOICE}"
    rf"(?:\s+(?:please|now|again|back|thanks|thank you))*\s*$",
    # "stop the spooky voice", "drop the creepy act", "quit using that halloween persona"
    rf"\b{_STOP}\b(?:\s+{_GLUE}){{0,3}}\s+{_THING}\b",
    # "stop being spooky", "quit making it scary"
    rf"\b{_STOP}\b\s+(?:being|sounding|acting|going|getting|making|doing)\s+(?:{_FILL}\s+)*{_ADJ}\b",
    # "turn off the spooky voice", "turn halloween mode off"
    rf"\b(?:turn|turning|switch|shut)\s+off\b(?:\s+{_DET})*\s+{_THING}\b",
    rf"\b(?:turn|turning|switch|shut)\s+(?:{_DET}\s+)*{_THING}\s+off\b",
    # "no more scary voice", "enough with the creepy stuff"
    rf"\b(?:no\s+more|enough(?:\s+(?:of|with))?)\s+(?:{_DET}\s+)*{_THING}\b",
    # "talk normally", "can you sound normal"
    rf"{_LEAD}(?P<core>(?:talk|speak|sound|respond|answer|reply)\s+"
    r"(?:normal|normally|regular|regularly)\b)",
)

_ADJ_WORD = re.compile(rf"\b{_ADJ}\b")
_TOPIC_WORD = re.compile(rf"\b{_TOPIC}\b")
_VOICE_WORD = re.compile(rf"\b{_VOICE}\b")
_VOICE_STRONG_WORD = re.compile(rf"\b{_VOICE_STRONG}\b")

# A negation earlier in the same clause cancels an ENTER ("don't make it spooky", "no spooky voice").
# The stop verbs count too: when the exit rule has not already claimed the phrase ("stop pretending to
# have a spooky voice", "cancel the spooky voice"), it is still not an enter. So do the words that put
# the spooky voice on the losing side of a comparison or a swap ("better than the spooky voice",
# "switch from the spooky voice", "replace the spooky voice with a normal one"); "from now on" is not
# one of those. Precision over recall: a missed enter falls back to the engine.
_NEG_ENTER = re.compile(
    rf"\b(?:don'?t|do\s+not|dont|never|not|non|no|without|won'?t|wouldn'?t|shouldn'?t|instead\s+of|"
    rf"enough|cancel|than|from(?!\s+(?:now|here|then|today|tomorrow|this)\b)|"
    rf"replac(?:e|ing)|swap(?:ping)?|trad(?:e|ing)|exchang(?:e|ing)|{_STOP})\b")
# A clause that names the normal voice is not an enter either ("the normal voice and the spooky voice
# are both fine"), unless the normal voice is named only to be set aside ("a spooky voice instead of
# your normal voice").
_NORMAL_MENTION = re.compile(rf"\b{_NORMAL_VOICE}\b")
_SET_ASIDE = re.compile(rf"\b(?:instead\s+of|rather\s+than)\s+(?:(?:the|a|an)\s+)?{_NORMAL_VOICE}\b")
# A narrower set cancels an EXIT ("don't go back to your normal voice", "instead of going back to it"); a
# bare "no" must not, so that an unpunctuated "no stop the spooky voice" still exits.
_NEG_EXIT = re.compile(
    r"\b(?:don'?t|do\s+not|dont|never|not|without|won'?t|wouldn'?t|shouldn'?t|no\s+need|"
    r"instead\s+of|unlike|than|different\s+from)\b")
# Clause boundaries: a negation does not reach across them ("no, do a spooky voice"). A comma before
# "please" is not one, so that "spooky, please" stays together.
_CLAUSE_BREAK = re.compile(r"[.!?;]|,(?!\s*please\b)|\b(?:but|though|however)\b")
_APOSTROPHES = str.maketrans({chr(0x2018): "'", chr(0x2019): "'"})  # curly quotes -> "'"


def _clauses(text: str | None) -> list[str]:
    return _CLAUSE_BREAK.split(" ".join((text or "").lower().translate(_APOSTROPHES).split()))


def _command_start(m: re.Match[str]) -> int:
    return m.start("core") if "core" in m.re.groupindex else m.start()


def _is_live(clause: str, frames: tuple[re.Pattern[str], ...], negation: re.Pattern[str]) -> bool:
    """Some frame matches in this clause and no negation comes before the first such command."""
    starts = [_command_start(m) for frame in frames if (m := frame.search(clause))]
    if not starts:
        return False
    neg = negation.search(clause)
    return neg is None or min(starts) <= neg.start()


def _names_normal_voice(clause: str) -> bool:
    return _NORMAL_MENTION.search(_SET_ASIDE.sub(" ", clause)) is not None


def has_cue(text: str) -> bool:
    """Is this turn about the assistant's voice (either direction), so worth waiting for the verdict?

    Deliberately looser than `explicit_command`, which *acts*: a cue only buys a short wait, so a negated
    request ("don't make it spooky") is still a cue. Halloween as a mere topic ("are you open on
    halloween") is not: a spooky word only counts next to a request to change the voice.
    """
    for clause in _clauses(text):
        if any(frame.search(clause) for frame in (*_EXIT, *_ENTER)):
            return True
        # phrasing no frame knows ("change your voice to something creepy"): a spooky adjective and a
        # voice noun in the same clause; halloween / ghost need a strong voice noun ("halloween voice")
        if _ADJ_WORD.search(clause) and _VOICE_WORD.search(clause):
            return True
        if _TOPIC_WORD.search(clause) and _VOICE_STRONG_WORD.search(clause):
            return True
    return False


def explicit_command(text: str) -> IntentVerdict | None:
    """Safety net for when the engine fails: a verdict only for a clear, un-negated command, else None.

    Exit is checked first and wins (G12). A negated request is not a command either way: "don't make it
    spooky" is not an enter, and "don't go back to your normal voice" is not an exit. A sentence that
    also names the normal voice ("the normal voice is better than the spooky voice") is not an enter, so
    a caller who wants out is never read as wanting in.
    """
    clauses = _clauses(text)
    if any(_is_live(c, _EXIT, _NEG_EXIT) for c in clauses):
        return IntentVerdict("exit", 1.0, "rule")
    if any(_is_live(c, _ENTER, _NEG_ENTER) and not _names_normal_voice(c) for c in clauses):
        return IntentVerdict("enter", 1.0, "rule")
    return None


def resolve_verdict(model: IntentVerdict, rule: IntentVerdict | None) -> IntentVerdict:
    """Pick the verdict to act on: explicit exit rule > engine answer > explicit rule > none.

    The engine "answered" only when `model.source == "model"`; timeout, error, skipped and disabled did
    not. With nothing to act on the result is a fresh `none` verdict that keeps the engine's source, so
    that `timeout` / `error` stay observable (spec §8).
    """
    if rule is not None and rule.intent == "exit":
        return rule  # G12: an exit is always honored, whatever the engine said (or failed to say)
    if model.source == "model":
        return model
    if rule is not None:
        return rule
    return IntentVerdict("none", 0.0, model.source)


def decide_mode(current: str, verdict: IntentVerdict, *, entries_so_far: int = 0) -> ModeDecision:
    """The spec §7.5 table. Thresholds are written `conf >= T` so a NaN confidence fails closed."""
    if verdict.intent == "enter":
        target = HALLOWEEN
    elif verdict.intent == "exit":
        target = STANDARD
    else:
        return ModeDecision(current, current, "no_intent")

    if current == target:
        return ModeDecision(current, current, "already_in_mode")

    if verdict.intent == "enter":
        if not verdict.confidence >= ENTER_THRESHOLD:
            return ModeDecision(current, current, "below_threshold")
        if entries_so_far >= MAX_ENTRIES_PER_CALL:  # only a confident enter is ever blocked by the cap
            return ModeDecision(current, current, "entry_cap")
        return ModeDecision(current, HALLOWEEN, "enter")

    if not verdict.confidence >= EXIT_THRESHOLD:  # an exit is never capped
        return ModeDecision(current, current, "below_threshold")
    return ModeDecision(current, STANDARD, "exit")
