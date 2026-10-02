"""Deterministic voice-mode decision policy (spec §7.2 / §7.5). Pure: no LiveKit, no network, no I/O.

Four small pieces, used in this order by the voice-mode controller:

  has_cue(text)            is this turn worth *waiting* for the decision engine's verdict?
  explicit_command(text)   high-precision regex verdict for clear commands (the engine-down safety net)
  resolve_verdict(m, r)    which verdict to act on: explicit exit rule > engine > explicit rule > none
  decide_mode(cur, v, ..)  the one deterministic transition (thresholds + entry cap)

Governance G12 (an exit is always honored) is realized here: an explicit exit rule beats the engine
(`resolve_verdict`) and an exit is never capped (`decide_mode`).
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
    # "model" | "rule" | "timeout" | "error" | "skipped" | "disabled"
    # (resolve_verdict's "nothing to act on" verdict uses "none")
    source: str
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
# ("halloween", "ghost"): the latter only count next to a voice noun ("halloween voice").
# ---------------------------------------------------------------------------------------
_ADJ = (r"(?:spook(?:y|ier|iest)|scar(?:y|ier|iest)|creep(?:y|ier|iest)|eeri(?:e|er|est)|ghostly|"
        r"haunt(?:ed|ing)|sinister|ominous|macabre|spine[- ]chilling)")
_ANY = rf"(?:{_ADJ}|hallowe'?en|ghost)"
_VOICE = r"(?:voices?|modes?|personas?|vibes?|accents?|tones?)"  # what the caller is asking to change
_WORD = r"[a-z0-9'-]+"
# Glue between "make / be / sound" and the adjective: "make [it a little bit more] spooky".
_FILL = (r"(?:it|this|that|things|everything|yourself|me|us|your voice|the voice|a (?:(?:little|tiny) )?"
         r"(?:bit|little|lot)|more|so|very|really|pretty|quite|rather|slightly|somewhat|kind of|sort of|"
         r"kinda|sorta|all|just|super|extra|way|much|too|even|full)")
_NORMAL = r"(?:normal|regular|usual|ordinary)"
_OWN = r"(?:own|real|natural|default|standard|original|old|normal|regular|usual|ordinary)"
_SELF = r"(?:voice|mode|persona|self|tone|accent)"
_STOP = (r"(?:stop|stopping|quit|quitting|cease|end|drop|dropping|cut|cutting|ditch|kill|skip|lose|"
         r"disable|cancel)")
_GLUE = (r"(?:the|that|this|your|all|with|being|doing|making|using|talking|speaking|sounding|acting|"
         r"in|it|like|a|an|please|just|now|already|right)")
_NOUN = r"(?:act|stuff|things?|routine|nonsense|business|talk)"
# The spooky "thing" a caller can stop, turn off, or have no more of. A bare topic word does not qualify:
# "stop the halloween voice" does, "stop the halloween promotions" does not.
_THING = rf"(?:{_ADJ}(?:\s+(?:{_VOICE}|{_NOUN}))?|{_ANY}(?:\s+{_WORD})?\s+(?:{_VOICE}|{_NOUN}))"
_DET = r"(?:the|that|this|your)"


def _frames(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


# Phrases that ask for the spooky voice ...
_ENTER = _frames(
    # "spooky voice", "scary halloween voice", "halloween-themed voice", "halloween mode", "ghost persona"
    # (filler words here exclude "-" so they cannot overlap the "[\s-]+" separators: linear, not cubic)
    rf"\b{_ANY}\b(?:[\s-]+[a-z0-9']+){{0,2}}?[\s-]+{_VOICE}\b",
    # "make it spooky", "make this creepy for me", "sound a bit scarier", "be more creepy", "go spooky"
    rf"\b(?:make|making|be|sound|sounding|act|acting|go)\b(?:\s+{_FILL})*\s+{_ADJ}\b",
    # "talk like a ghost", "talk to me like a ghost", "act as a haunted house"
    rf"\b(?:talk|talking|speak|speaking|sound|sounding|act|acting|respond|answer|reply)\b"
    rf"(?:\s+{_WORD}){{0,2}}?\s+(?:like|as)\s+(?:an?\s+|the\s+)?{_ANY}\b",
    # "switch to halloween", "change your voice to spooky"
    rf"\b(?:switch|switching|change|changing)\b(?:\s+(?:your voice|the voice|yourself|over))*"
    rf"\s+(?:to|into)\s+(?:the\s+|an?\s+|your\s+)?{_ANY}\b",
    # the terse "spooky please" / "spooky, please"
    rf"\b{_ADJ},?\s+please\b",
)
# ... and phrases that ask to drop it.
_EXIT = _frames(
    # "normal voice", "your regular voice", "your old self"
    rf"\b{_NORMAL}\s+{_SELF}\b",
    rf"\byour\s+{_OWN}\s+{_SELF}\b",
    # "stop the spooky voice", "quit being scary", "drop the creepy act", "stop making it spooky"
    rf"\b{_STOP}\b(?:\s+{_GLUE}){{0,3}}\s+{_THING}\b",
    # "turn off the spooky voice", "turn halloween mode off"
    rf"\b(?:turn|turning|switch|shut)\s+off\b(?:\s+{_DET})*\s+{_THING}\b",
    rf"\b(?:turn|turning|switch|shut)\s+(?:{_DET}\s+)*{_THING}\s+off\b",
    # "no more scary voice", "enough with the creepy stuff"
    rf"\b(?:no\s+more|enough(?:\s+(?:of|with))?)\s+(?:{_DET}\s+)*{_THING}\b",
    # "talk normally", "sound normal"
    r"\b(?:talk|speak|sound|respond|answer|reply)\s+(?:normal|normally|regular|regularly)\b",
)

_SPOOKY_WORD = re.compile(rf"\b{_ANY}\b")
_VOICE_WORD = re.compile(rf"\b{_VOICE}\b")

# A negation earlier in the same clause cancels an ENTER ("don't make it spooky", "no spooky voice").
# The stop verbs count too: when the exit rule has not already claimed the phrase ("stop pretending to
# have a spooky voice"), it is still not an enter. Precision over recall: a missed enter falls back to
# the engine.
_NEG_ENTER = re.compile(
    rf"\b(?:don'?t|do\s+not|dont|never|not|non|no|without|won'?t|wouldn'?t|shouldn'?t|instead\s+of|"
    rf"enough|{_STOP})\b")
# A narrower set cancels an EXIT ("don't go back to your normal voice"); a bare "no" must not, so that
# an unpunctuated "no stop the spooky voice" still exits.
_NEG_EXIT = re.compile(
    r"\b(?:don'?t|do\s+not|dont|never|not|without|won'?t|wouldn'?t|shouldn'?t|no\s+need)\b")
# Clause boundaries: a negation does not reach across them ("no, do a spooky voice"). A comma before
# "please" is not one, so that "spooky, please" stays together.
_CLAUSE_BREAK = re.compile(r"[.!?;]|,(?!\s*please\b)|\b(?:but|though|however)\b")
_APOSTROPHES = str.maketrans({chr(0x2018): "'", chr(0x2019): "'"})  # curly quotes -> "'"


def _clauses(text: str | None) -> list[str]:
    return _CLAUSE_BREAK.split(" ".join((text or "").lower().translate(_APOSTROPHES).split()))


def _is_live(clause: str, frames: tuple[re.Pattern[str], ...], negation: re.Pattern[str]) -> bool:
    """Some frame matches in this clause and no negation comes before the first such match."""
    starts = [m.start() for frame in frames if (m := frame.search(clause))]
    if not starts:
        return False
    neg = negation.search(clause)
    return neg is None or min(starts) <= neg.start()


def has_cue(text: str) -> bool:
    """Is this turn about the assistant's voice (either direction), so worth waiting for the verdict?

    Deliberately looser than `explicit_command`, which *acts*: a cue only buys a short wait, so a negated
    request ("don't make it spooky") is still a cue. Halloween as a mere topic ("are you open on
    halloween") is not: a spooky word only counts next to a request to change the voice.
    """
    for clause in _clauses(text):
        if any(frame.search(clause) for frame in (*_EXIT, *_ENTER)):
            return True
        # phrasing no frame knows ("change your voice to something creepy"): a spooky word and a voice
        # noun in the same clause
        if _SPOOKY_WORD.search(clause) and _VOICE_WORD.search(clause):
            return True
    return False


def explicit_command(text: str) -> IntentVerdict | None:
    """Safety net for when the engine fails: a verdict only for a clear, un-negated command, else None.

    Exit is checked first and wins (G12). A negated request is not a command either way: "don't make it
    spooky" is not an enter, and "don't go back to your normal voice" is not an exit.
    """
    clauses = _clauses(text)
    if any(_is_live(c, _EXIT, _NEG_EXIT) for c in clauses):
        return IntentVerdict("exit", 1.0, "rule")
    if any(_is_live(c, _ENTER, _NEG_ENTER) for c in clauses):
        return IntentVerdict("enter", 1.0, "rule")
    return None


def resolve_verdict(model: IntentVerdict, rule: IntentVerdict | None) -> IntentVerdict:
    """Pick the verdict to act on: explicit exit rule > engine answer > explicit rule > none.

    The engine "answered" only when `model.source == "model"`; timeout, error, skipped and disabled
    did not.
    """
    if rule is not None and rule.intent == "exit":
        return rule  # G12: an exit is always honored, whatever the engine said (or failed to say)
    if model.source == "model":
        return model
    if rule is not None:
        return rule
    return IntentVerdict("none", 0.0, "none")


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
