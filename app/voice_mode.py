"""The voice-mode controller (spec §7.2 / §7.3 / §7.4): per-turn decide → switch → degrade → evidence.

It threads the pure policy (`src.policy.voice_mode`), the decision engine (`src.services.ai_decide`), the
voice profiles (`app.voice_profiles`), the prompt pieces (`src.agent_prompt`) and tag stripping
(`app.expressive`) into the per-turn order of operations LiveKit's `on_user_turn_completed` needs. The
`StudioAgent` (Task 8) owns the LiveKit nodes and calls into this; the wiring (Task 10) builds ONE
`DecideClient` and passes it in as `classifier`.

Three load-bearing invariants:

  1. `on_turn` NEVER raises. A raise would drop the whole turn, so everything is wrapped and the decision
     wait is time-boxed to `cue_wait_s`. (livekit-agents 1.8.3 now also catches it, but we still never raise.)
  2. `_transition` is the SINGLE writer of `state.mode` (spec §7.4). It is reached from `on_turn`
     (same-turn) and from a late answer checked against `turn_seq` (announced); both run on the session's
     event loop, so no locking is needed. `mark_degraded` is the only other writer and it sets only
     `degraded`.
  3. Carry-forwards (review): `cue = has_cue(text) or mode == HALLOWEEN` (terse exits in Halloween still get
     the cue wait); in STANDARD an exit-RULE verdict is dropped before `resolve_verdict` (an exit is a no-op
     in STANDARD and could only shadow an engine enter via G12's exit priority); an explicit exit always
     wins in HALLOWEEN (G12).

Sequencing (spec §7.3): `prefetch` (on each final transcript) starts the background classify and accumulates
multi-segment turns. `on_turn` bumps `turn_seq`, resolves the explicit rule, waits for the engine within the
cue budget on a cue (else uses the answer only if it already arrived), and either switches SAME-TURN (patch
`turn_ctx` for this reply via the lazily-imported 1.8.3 `update_instructions` helper + `agent.update_
instructions` for later turns) or, if the answer wasn't in, defers to an ANNOUNCED switch when it lands —
unless a newer turn has started (then the answer is dropped). Evidence (§7.2) carries no transcript or PII.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.expressive import strip_tags
from app.voice_profiles import VoiceProfile
from src.agent_prompt import ANNOUNCE_OFF, ANNOUNCE_ON, BRIDGE_ON, OFF_NOTE, ON_NOTE
from src.policy.voice_mode import (
    HALLOWEEN,
    STANDARD,
    IntentVerdict,
    decide_mode,
    explicit_command,
    has_cue,
    resolve_verdict,
)

logger = logging.getLogger(__name__)

# Non-model verdicts the controller feeds to resolve_verdict when the engine has not answered. Their source
# is never "model", so resolve_verdict falls through to the explicit rule (or to no change).
_TIMEOUT = IntentVerdict("none", 0.0, "timeout")    # a cue turn whose wait expired
_SKIPPED = IntentVerdict("none", 0.0, "skipped")    # a non-cue turn whose answer was not yet in
_ERROR = IntentVerdict("none", 0.0, "error")        # the classify task was cancelled out from under us
_NONE = IntentVerdict("none", 0.0, "disabled")      # the base verdict for a degrade snapshot with no decision

# path values that appear in evidence (§7.2) and on the span (§7.11)
_SAME_TURN, _ANNOUNCED, _LATE_DROPPED, _NONE_PATH = "same_turn", "announced", "late_dropped", "none"


def _say(message: str) -> None:
    """One `[ug] voice_mode …` line per transition, late drop, or degrade (spec §7.11). Never raises."""
    try:
        print(f"[ug] voice_mode {message}", flush=True)
    except Exception:  # noqa: BLE001 - logging must not break the rule that a turn never raises
        pass


def _collapse(text: str | None) -> str:
    """Whitespace-collapsed text, as sent to the engine (case kept; the engine truncates and re-collapses)."""
    return " ".join((text or "").split())


def _normalize(text: str | None) -> str:
    """A case-insensitive key for deciding whether the committed turn text matches a prefetched one."""
    return _collapse(text).lower()


def _message_text(message: Any) -> str:
    """The caller's words from the turn's `llm.ChatMessage` (or anything with `text_content` / `content`)."""
    if message is None:
        return ""
    text = getattr(message, "text_content", None)
    if text is None:
        content = getattr(message, "content", None)
        if isinstance(content, (list, tuple)):
            text = " ".join(part for part in content if isinstance(part, str))
        elif isinstance(content, str):
            text = content
    return _collapse(text or "")


def _swallow_cancelled(task: asyncio.Task) -> None:
    """Retrieve a cancelled/stale task's outcome so asyncio does not warn about an unretrieved exception."""
    if not task.cancelled():
        try:
            task.exception()
        except Exception:  # noqa: BLE001
            pass


@dataclass
class VoiceModeState:
    """One per call (spec §7.4). Nothing here persists after the call: the mode is a per-call preference."""

    mode: str = STANDARD
    entries: int = 0            # Halloween entries so far (the MAX_ENTRIES_PER_CALL cap counts enters only)
    transitions: int = 0        # every applied switch, either direction
    degraded: bool = False      # a Halloween-TTS vendor failure: route to the fallback voice
    last_agent_line: str = ""   # the agent's previous line, tags stripped, for the decider's agent_said
    tags_spoken: int = 0        # expressive tags actually sent to the voice (ug.expressive_tags)
    turn_seq: int = 0           # monotonic turn counter; a late answer is dropped if it has advanced
    last: dict | None = None    # the last published evidence payload (reused for a degrade snapshot)


class VoiceModeController:
    """Decides the voice mode once per turn and applies it, same-turn or announced. See the module docstring.

    `classifier` is one `DecideClient` for the whole call (its `classify` never raises and never retries).
    `profiles` holds `standard` / `halloween` / `halloween_fallback`; `instructions_for(profile)` renders the
    steady instructions for a mode. `evidence_sink(payload)` publishes `{"voice_mode": …}` to the UI,
    `tracer` is the agent's OTel tracer (or None), `on_cue()` prewarms the Halloween TTS on a cue, `enabled`
    is the `UG_AI_DECIDE` kill switch, and `cue_wait_s` is the longest a turn waits for the engine.
    """

    def __init__(
        self,
        classifier,
        profiles: dict[str, VoiceProfile],
        instructions_for: Callable[[VoiceProfile], str],
        *,
        evidence_sink: Callable[[dict], None] | None = None,
        tracer=None,
        on_cue: Callable[[], None] | None = None,
        enabled: bool = True,
        cue_wait_s: float = 0.8,
    ) -> None:
        self._classifier = classifier
        self._profiles = profiles
        self._instructions_for = instructions_for
        self._evidence_sink = evidence_sink
        self._tracer = tracer
        self._on_cue = on_cue
        self._enabled = bool(enabled)
        self._cue_wait_s = float(cue_wait_s)
        self.state = VoiceModeState()

        # Background work. `_task` is the in-flight prefetch classify (one at a time); `_accum` is the turn's
        # accumulated transcript. `_late_tasks` keeps a reference to pending announced-switch applies so they
        # are not garbage-collected mid-flight; `_late_task` is the most recent, for observability/tests.
        self._task: asyncio.Task | None = None
        self._task_norm: str | None = None
        self._accum: str = ""
        self._inflight: set[asyncio.Task] = set()   # all live classify tasks, for a clean shutdown
        self._late_tasks: set[asyncio.Task] = set()
        self._late_task: asyncio.Task | None = None

    # ---------------------------------------------------------------- readers (tts_node, filter, UI, trace)

    @property
    def profile(self) -> VoiceProfile:
        """The active profile. In Halloween a degraded call routes to the fallback (dark voice, no tags)."""
        if self.state.mode == HALLOWEEN:
            if self.state.degraded:
                return self._profiles.get("halloween_fallback") or self._profiles["halloween"]
            return self._profiles["halloween"]
        return self._profiles["standard"]

    def vocabulary(self) -> frozenset[str]:
        """The active profile's performable tags, passed to `encode_tags` (empty => every cue is dropped)."""
        return self.profile.tags

    def instructions(self) -> str:
        """The steady instructions for the current mode (= `instructions_for(self.profile)`)."""
        return self._instructions_for(self.profile)

    # ---------------------------------------------------------------- event sinks (from StudioAgent / nodes)

    def prefetch(self, transcript: str) -> None:
        """From `user_input_transcribed` (is_final): start/refresh the background classify. Multi-segment
        turns accumulate, and the engine is re-asked for the growing text. Never raises."""
        if not self._enabled:
            return
        try:
            segment = _collapse(transcript)
            if not segment:
                return
            self._accum = f"{self._accum} {segment}".strip() if self._accum else segment
            self._ensure_task(self._accum)
        except Exception:  # noqa: BLE001 - an event callback must never break the pipeline
            logger.warning("voice_mode.prefetch failed", exc_info=True)

    def note_agent_line(self, text: str) -> None:
        """From `conversation_item_added` (assistant): store the agent's line (tags stripped) for the
        decider's `agent_said`. Never raises."""
        try:
            self.state.last_agent_line = strip_tags(text or "")
        except Exception:  # noqa: BLE001
            logger.warning("voice_mode.note_agent_line failed", exc_info=True)

    def count_tag(self, name: str) -> None:
        """The `on_tag` callback from `encode_tags`: a performed cue (ug.expressive_tags). Never raises."""
        try:
            self.state.tags_spoken += 1
        except Exception:  # noqa: BLE001
            logger.warning("voice_mode.count_tag failed", exc_info=True)

    def mark_degraded(self, reason: str) -> None:
        """From `tts_node` on a Halloween-TTS vendor failure. Sets ONLY `degraded` (§7.4) and publishes an
        evidence snapshot once so the UI shows the fallback voice (§7.10). Never raises."""
        try:
            if self.state.degraded:
                return
            self.state.degraded = True
            _say(f"degraded ({reason}); using the fallback voice for the rest of the call")
            base = dict(self.state.last) if self.state.last else self._evidence(_NONE_PATH, _NONE, None, "degraded")
            snapshot = {**base, "mode": self.state.mode, "voice": self.profile.label, "degraded": True}
            self._emit_evidence(snapshot)
        except Exception:  # noqa: BLE001
            logger.warning("voice_mode.mark_degraded failed", exc_info=True)

    # ---------------------------------------------------------------- the turn hook (never raises)

    async def on_turn(self, agent, turn_ctx, new_message) -> None:
        """From `StudioAgent.on_user_turn_completed`. Decides and applies the mode for this turn. The whole
        body is guarded: a raise would drop the turn, which must never happen."""
        if not self._enabled:
            return
        try:
            self.state.turn_seq += 1
            turn_no = self.state.turn_seq
            text = _message_text(new_message)

            # CARRY-FORWARD: in STANDARD, drop an exit-rule verdict before resolve_verdict. An exit is a
            # no-op in STANDARD; left in, resolve_verdict's G12 exit-priority would shadow an engine enter.
            rule = explicit_command(text)
            if self.state.mode == STANDARD and rule is not None and rule.intent == "exit":
                rule = None

            # CARRY-FORWARD: a cue is worth waiting for; in Halloween every turn is (terse exits included).
            cue = has_cue(text) or self.state.mode == HALLOWEEN
            task = self._claim_task(text)   # reuse the prefetch answer, or ask now; this turn owns the task

            if cue and self._on_cue is not None:
                try:
                    self._on_cue()          # prewarm the Halloween TTS while the decision is pending
                except Exception:  # noqa: BLE001
                    logger.warning("voice_mode.on_cue failed", exc_info=True)

            model, latency_ms, answered = await self._await_verdict(task, cue)
            verdict = resolve_verdict(model, rule)
            decision = decide_mode(self.state.mode, verdict, entries_so_far=self.state.entries)

            if decision.changed:
                await self._transition(decision, verdict, latency_ms, _SAME_TURN,
                                       agent=agent, turn_ctx=turn_ctx, cue=cue)
                return
            if not answered:
                # The reply goes out in the current mode; apply the engine's answer as an announced switch
                # when it lands (unless a newer turn has started by then).
                self._schedule_late(task, turn_no, rule, agent, cue)
        except Exception:  # noqa: BLE001 - the turn must survive any failure in here
            logger.warning("voice_mode.on_turn failed; the turn continues in the current mode", exc_info=True)

    async def aclose(self) -> None:
        """Cancel every in-flight classify and pending announced-switch apply, and drain them (for shutdown
        and tests). Does not close the classifier: the wiring owns it (one per call) and `aclose`es it."""
        pending = [t for t in (*self._inflight, *self._late_tasks) if not t.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except BaseException:  # noqa: BLE001 - draining cancellations on the way out
                pass
        self._task = None
        self._task_norm = None
        self._inflight.clear()
        self._late_tasks.clear()

    # ---------------------------------------------------------------- background classify management

    def _ensure_task(self, text: str) -> None:
        """Start the classify for `text`, cancelling any stale in-flight one. A duplicate (same text) is a
        no-op, so repeated final events for one segment do not re-ask."""
        norm = _normalize(text)
        if self._task is not None and self._task_norm == norm and not self._task.cancelled():
            return
        self._cancel_task()
        self._task_norm = norm
        self._task = asyncio.ensure_future(
            self._classifier.classify(_collapse(text), self.state.last_agent_line, self.state.mode))
        self._inflight.add(self._task)
        self._task.add_done_callback(self._inflight.discard)

    def _claim_task(self, text: str) -> asyncio.Task:
        """Return the task to await for this turn, reusing the prefetch's answer when its text matches the
        committed message, else asking again. The returned task is detached from `self._task` so the NEXT
        turn's prefetch starts fresh without cancelling an answer we are still waiting on (the turn_seq race).
        """
        if not (self._task is not None and self._task_norm == _normalize(text)):
            self._ensure_task(text)   # no prefetch, or the committed text differs -> re-ask for it now
        task = self._task
        assert task is not None
        self._task = None
        self._task_norm = None
        self._accum = ""              # this turn is consumed; the next turn accumulates from scratch
        return task

    def _cancel_task(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._task.add_done_callback(_swallow_cancelled)
        self._task = None
        self._task_norm = None

    async def _await_verdict(self, task: asyncio.Task, cue: bool) -> tuple[IntentVerdict, float | None, bool]:
        """(model_verdict, latency_ms, answered). On a cue, wait up to `cue_wait_s` (never cancelling the
        task — a slow answer still feeds the announced path); otherwise use the answer only if already in.
        A task that failed re-raises here so `on_turn` swallows it; the mode just stays put."""
        if cue:
            done, _ = await asyncio.wait({task}, timeout=self._cue_wait_s)
            if task not in done:
                return _TIMEOUT, None, False
        elif not task.done():
            return _SKIPPED, None, False
        if task.cancelled():
            return _ERROR, None, False
        exc = task.exception()
        if exc is not None:
            raise exc
        verdict, latency_ms = task.result()
        return verdict, latency_ms, True

    # ---------------------------------------------------------------- the announced (late) path

    def _schedule_late(self, task: asyncio.Task, turn_no: int, rule: IntentVerdict | None, agent, cue: bool) -> None:
        late = asyncio.ensure_future(self._apply_late(task, turn_no, rule, agent, cue))
        self._late_tasks.add(late)
        late.add_done_callback(self._late_tasks.discard)
        self._late_task = late

    async def _apply_late(self, task: asyncio.Task, turn_no: int, rule: IntentVerdict | None, agent, cue: bool) -> None:
        """Awaits the slow answer, then applies it as an announced switch — unless a newer turn has started
        (turn_seq advanced), in which case the answer is dropped and never applied."""
        try:
            model, latency_ms = await task
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - classify should not raise, but a late failure just means no switch
            logger.warning("voice_mode late classify failed; no announced switch", exc_info=True)
            return
        try:
            verdict = resolve_verdict(model, rule)
            if self.state.turn_seq != turn_no:
                _say(f"late answer dropped (turn {turn_no} < {self.state.turn_seq}); intent={verdict.intent}")
                self._publish(_LATE_DROPPED, self.state.mode, self.state.mode, verdict, latency_ms,
                              verdict.intent, cue)
                return
            decision = decide_mode(self.state.mode, verdict, entries_so_far=self.state.entries)
            if decision.changed:
                await self._transition(decision, verdict, latency_ms, _ANNOUNCED, agent=agent, cue=cue)
        except Exception:  # noqa: BLE001
            logger.warning("voice_mode late apply failed", exc_info=True)

    # ---------------------------------------------------------------- the single writer of state.mode

    async def _transition(self, decision, verdict: IntentVerdict, latency_ms: float | None, path: str, *,
                          agent, turn_ctx=None, cue: bool = False) -> None:
        """The ONE place `state.mode` changes (spec §7.4). Same-turn patches `turn_ctx` for THIS reply AND
        updates the steady instructions for later turns; announced updates steady then queues one
        `generate_reply`. Then publishes evidence and emits the span."""
        before, after = decision.mode_before, decision.mode_after

        # T11 verbal bridge: on a SAME-TURN enter, speak a short themed line in the CURRENT (outgoing) voice
        # to cover the incoming ElevenLabs voice's cold-start. Emitted BEFORE the mode flip (so it targets the
        # outgoing voice) and only here: the announced path's reply already carried the model's own
        # "One moment…" (VOICE_REQUESTS), and an exit's incoming standard voice has no cold-start.
        if path == _SAME_TURN and after == HALLOWEEN:
            self._emit_bridge(agent)

        self.state.mode = after
        self.state.transitions += 1
        if after == HALLOWEEN:
            self.state.entries += 1   # entries count enters only (the cap never blocks an exit — G12)
        steady = self._instructions_for(self.profile)   # self.profile now reflects the new mode

        if path == _SAME_TURN:
            note = ON_NOTE if after == HALLOWEEN else OFF_NOTE
            self._patch_turn_ctx(turn_ctx, f"{steady}\n\n{note}")  # LiveKit drops the stale preemptive reply
            await agent.update_instructions(steady)                # steady, without the one-reply note (C4)
        else:  # announced
            await agent.update_instructions(steady)
            announce = ANNOUNCE_ON if after == HALLOWEEN else ANNOUNCE_OFF
            agent.session.generate_reply(instructions=announce)    # queued after the current speech (C13)

        _say(f"{before} -> {after} path={path} reason={decision.reason} "
             f"source={verdict.source} conf={verdict.confidence:.2f}")
        self._publish(path, before, after, verdict, latency_ms, decision.reason, cue)

    def _patch_turn_ctx(self, turn_ctx, instructions: str) -> None:
        """Patch THIS reply's instructions on the per-reply `turn_ctx` via the 1.8.3 helper, imported lazily
        so the testable parts of this module stay importable without wiring up the whole voice stack."""
        if turn_ctx is None:
            return
        from livekit.agents.voice.generation import update_instructions
        update_instructions(turn_ctx, instructions=instructions, add_if_missing=True)

    def _emit_bridge(self, agent) -> None:
        """Speak the fixed T11 bridge line (`BRIDGE_ON`) through the session so the incoming voice's
        cold-start is covered by the OUTGOING voice (this runs before the mode flips). Fire-and-forget and
        fully guarded: a session without `say`, or a `say` that raises, must never break the switch or the
        turn (the turn-never-raises invariant). The line is cosmetic, so it never joins the chat context."""
        try:
            session = getattr(agent, "session", None)
            say = getattr(session, "say", None)
            if not callable(say):
                return
            say(BRIDGE_ON, add_to_chat_ctx=False)
            _say(f"bridge spoken ({self.state.mode} voice) to cover the cold-start")
        except Exception:  # noqa: BLE001 - the bridge is best-effort; the switch continues regardless
            logger.warning("voice_mode bridge failed; the switch continues", exc_info=True)

    # ---------------------------------------------------------------- evidence (§7.2) + span (§7.11)

    def _evidence(self, path: str, verdict: IntentVerdict, latency_ms: float | None, reason: str) -> dict:
        """The `voice_mode` evidence body (§7.2): NO transcript text, NO PII — the decider's input never
        appears here, only labels, the decision, and timing."""
        return {
            "mode": self.state.mode,
            "voice": self.profile.label,
            "decided_by": getattr(self._classifier, "label", None),
            "engine": getattr(self._classifier, "engine", None),
            "path": path,
            "confidence": verdict.confidence,
            "probabilities": verdict.probabilities,
            "latency_ms": latency_ms,
            "reason": reason,
            "degraded": self.state.degraded,
        }

    def _publish(self, path: str, before: str, after: str, verdict: IntentVerdict,
                 latency_ms: float | None, reason: str, cue: bool) -> None:
        self._emit_evidence(self._evidence(path, verdict, latency_ms, reason))
        self._emit_span(path, before, after, verdict, latency_ms, reason, cue)

    def _emit_evidence(self, voice_mode: dict) -> None:
        self.state.last = voice_mode
        if self._evidence_sink is None:
            return
        try:
            self._evidence_sink({"voice_mode": voice_mode})
        except Exception:  # noqa: BLE001 - publishing evidence must not break the turn
            logger.warning("voice_mode evidence sink failed", exc_info=True)

    def _emit_span(self, path: str, before: str, after: str, verdict: IntentVerdict,
                   latency_ms: float | None, reason: str, cue: bool) -> None:
        """One `ug.ai_decide` span per mode transition (`same_turn` / `announced`) and per dropped late answer
        (`late_dropped`); a turn that changes nothing gets none, and a degrade has no span of its own (§7.11).
        A no-op when tracing is off. The utterance is never copied — LiveKit's own user-turn span already
        holds the transcript."""
        if self._tracer is None:
            return
        try:
            with self._tracer.start_as_current_span("ug.ai_decide") as span:
                if span is None:
                    return
                span.set_attribute("ug.decide.engine", str(getattr(self._classifier, "engine", "") or ""))
                span.set_attribute("ug.decide.cue", bool(cue))
                span.set_attribute("ug.decide.source", verdict.source)
                span.set_attribute("ug.decide.intent", verdict.intent)
                span.set_attribute("ug.decide.confidence", float(verdict.confidence))
                if verdict.probabilities is not None:
                    span.set_attribute("ug.decide.probabilities", json.dumps(verdict.probabilities))
                if latency_ms is not None:
                    span.set_attribute("ug.decide.latency_ms", float(latency_ms))
                span.set_attribute("ug.decide.path", path)
                span.set_attribute("ug.decide.reason", reason)
                span.set_attribute("ug.voice_mode.before", before)
                span.set_attribute("ug.voice_mode.after", after)
        except Exception:  # noqa: BLE001 - the span is best-effort; it must never break the turn
            logger.warning("voice_mode span emit failed", exc_info=True)
