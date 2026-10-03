"""UG Voice Studio agent worker.

Reuses ReferenceApp's boot/tracing/session pattern, REORDERED (spec §6): the loyalty tier —
and therefore the routed UAIG model — is bound BEFORE the AgentSession is built, so
each call runs on the tier's model. Generic read-only tools + a per-dataset,
governance-wrapped system prompt. The LLM never receives the raw tier.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

_APP_DIR = Path(__file__).parent
_REPO_ROOT = _APP_DIR.parent
for _p in (str(_APP_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env.local", override=False)

from livekit import agents
from livekit.agents import AgentServer, AgentSession
from livekit.agents.telemetry import set_tracer_provider
from livekit.plugins import deepgram, openai
from livekit.plugins import elevenlabs  # noqa: F401  — importing registers the plugin on the subprocess
# main thread (spec §7.1 / Task-10 gotcha); a lazy first-use import would raise off the main thread.

from src.agent_prompt import build_instructions
from src.services.ai_decide import DecideClient
from src.services.db import create_pool
from src.services.session_bind import bind_session
from app.expressive import decode_tags, encode_tags
from app.studio_agent import StudioAgent
from app.tools import SessionContext, build_tools
from app.tracing import build_tracer_provider, fill_ug_metadata
from app.voice_mode import VoiceModeController
from app.voice_profiles import build_tts, resolve_profiles

server = AgentServer()

_AGENT_NAME = os.environ.get("AGENT_NAME", "ug-agent")


def _read_meta(ctx):
    """(courtesy_name, data_generation_id, customer_id) from the first remote participant's
    LiveKit token. metadata is JSON {data_generation_id, customer_id} (web_server.mint_token).
    Safe defaults ("", "", None) when absent/unparseable. The name is courtesy only."""
    for p in ctx.room.remote_participants.values():
        name = (getattr(p, "name", "") or "").strip()
        gid, cid = "", None
        md = (getattr(p, "metadata", "") or "").strip()
        if md:
            try:
                d = json.loads(md)
                gid = d.get("data_generation_id", "") or ""
                cid = d.get("customer_id") or None
            except Exception:
                pass
        return name[:40], gid, cid
    return "", "", None


async def _create_pool_soft():
    """Warm Lakebase pool; degrade to None (spec §17) rather than crash the session."""
    try:
        return await create_pool()
    except Exception as exc:  # noqa: BLE001
        print(f"[ug] Lakebase pool unavailable, running degraded: {exc}", flush=True)
        return None


def _make_evidence_sink(room, session_ctx):
    """Publish privacy-clean evidence to the in-page panel over the LiveKit data channel
    (Choice/Control/Context for the operator UI — Plan 4), and stash the last retrieval on
    the session context so the trace can carry ug.retrieval. Fail-soft; never blocks a turn."""
    async def _sink(fragment: dict) -> None:
        try:
            if "retrieval" in fragment:
                session_ctx.last_retrieval = fragment["retrieval"]
            data = json.dumps({"type": "ug_evidence", **fragment}).encode("utf-8")
            await room.local_participant.publish_data(data, reliable=True, topic="ug_evidence")
        except Exception:
            pass  # evidence is cosmetic — never disrupt the voice pipeline
    return _sink


def _bridge_sink(evidence_sink_async):
    """Adapt the async `_make_evidence_sink` sink to the SYNCHRONOUS call the VoiceModeController makes
    (`self._evidence_sink({"voice_mode": …})`, no await). Schedule the coroutine on the running loop — the
    same idiom the usage path uses — so a voice_mode fragment still reaches the UI without blocking the turn.
    Without this bridge the controller would create a coroutine and never await it, and NO voice_mode evidence
    would be published."""
    return lambda fragment: asyncio.ensure_future(evidence_sink_async(fragment))


@server.rtc_session(agent_name=_AGENT_NAME)
async def entrypoint(ctx: agents.JobContext):
    host = os.getenv("DATABRICKS_HOST", "")
    token = os.getenv("DATABRICKS_TOKEN", "")
    if not host or not token:
        raise RuntimeError("DATABRICKS_HOST / DATABRICKS_TOKEN not set — check .env.local")

    # --- OTel tracing (fail-soft) ---
    trace_enrichment: dict[str, Any] = {}   # ug.voice_* adds ints/bools, not only strings (spec §7.11)
    trace_provider = build_tracer_provider(trace_enrichment)
    bind = None
    session_ctx = None
    controller = None   # the VoiceModeController, built after the governed bind; the flush reads its state
    if trace_provider is not None:
        set_tracer_provider(trace_provider, metadata={"livekit.agent_name": _AGENT_NAME})

        async def _flush_traces() -> None:
            fill_ug_metadata(trace_enrichment, bind, session_ctx, voice_mode=controller)
            trace_provider.force_flush()
            trace_provider.shutdown()

        ctx.add_shutdown_callback(_flush_traces)

    # --- Warm the Lakebase pool before session.start() ---
    pool = await _create_pool_soft()
    if pool is not None:
        async def _close_pool() -> None:
            await pool.close()
        ctx.add_shutdown_callback(_close_pool)

    # --- Connect + WAIT for the participant FIRST, so the token (data_generation_id +
    #     customer_id + name) is present BEFORE we bind the tier and pick the model (§6). ---
    await ctx.connect()
    try:
        await asyncio.wait_for(ctx.wait_for_participant(), timeout=30.0)
    except Exception:
        pass  # timeout -> reads below fall back to safe defaults

    name, gid, cid = _read_meta(ctx)

    # --- Governed bind: tier from read_loyalty_context -> route_for -> model + directives. ---
    bind = await bind_session(pool, gid, cid, courtesy_name=name or None)
    session_ctx = SessionContext(data_generation_id=gid, customer_id=cid)
    evidence_sink = _make_evidence_sink(ctx.room, session_ctx)

    # --- AI Decide + Halloween voice (spec §7.1 / §7.3 / §9). The decider sees ONLY the caller (G10); the
    #     bind is never handed to it. UG_AI_DECIDE=0 turns the whole subsystem off (enabled=False → standard
    #     voice only, zero classify calls). Env is parsed defensively: unset OR blank falls to the default. ---
    ai_decide_on = (os.environ.get("UG_AI_DECIDE") or "1") != "0"
    profiles = resolve_profiles(os.environ)   # reads the mapping it is given; build_tts reads os.environ
    decider = DecideClient(
        host, token,
        engine=os.environ.get("UG_DECIDE_ENGINE") or "ai_decide",
        model=os.environ.get("UG_DECIDE_MODEL") or "databricks-gpt-5-4-nano",
        timeout_s=float(os.environ.get("UG_DECIDE_TIMEOUT_S") or "3.0"),
    )
    if ai_decide_on:
        trace_enrichment["ug.decide_engine"] = decider.engine   # seed before the first decision publishes

    # The VoiceModeController calls its evidence sink SYNCHRONOUSLY; `_make_evidence_sink` is async → bridge it.
    controller_sink = _bridge_sink(evidence_sink)
    # The ug.ai_decide span rides the agent's own provider so the span-enrichment exporter types it CHAIN.
    decide_tracer = trace_provider.get_tracer("ug.voice_mode") if trace_provider is not None else None

    def _instructions_for(profile):
        # tuple(sorted(..)): a frozenset iterates nondeterministically; the rendered prompt must be stable.
        return build_instructions(
            bind.system_prompt, bind.directives, bind.courtesy_name,
            persona=profile.persona, expressive_tags=tuple(sorted(profile.tags)),
            voice_requests=ai_decide_on)

    studio_agent = None   # built below; the on_cue prewarm closes over it (invoked only during a live turn)

    def _prewarm_halloween() -> None:
        """On a cue, build + warm the Halloween profile TTS so the first spooky utterance is not slow."""
        try:
            if studio_agent is not None:
                tts_obj = studio_agent._tts_for(profiles["halloween"])
                prewarm = getattr(tts_obj, "prewarm", None)
                if callable(prewarm):
                    prewarm()
        except Exception:  # noqa: BLE001 - prewarm is best-effort; it must never break a turn
            pass

    controller = VoiceModeController(
        classifier=decider,
        profiles=profiles,
        instructions_for=_instructions_for,
        evidence_sink=controller_sink,
        tracer=decide_tracer,
        on_cue=_prewarm_halloween,
        enabled=ai_decide_on,
        cue_wait_s=float(os.environ.get("UG_DECIDE_CUE_WAIT_S") or "0.8"),
    )

    # --- Build the session on the ROUTED model (the tier's model). The TTS-branch transform chain hides the
    #     allowed [tags] from the stock filters and restores them for the voice (spec §7.8); the native
    #     `expressive` subsystem stays OFF — it does not cover ElevenLabs and would collide. ---
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="en-US"),
        llm=openai.responses.LLM(
            model=bind.model,
            api_key=token,
            base_url=f"{host}/ai-gateway/openai/v1",
            use_websocket=False,
            store=False,
        ),
        tts=deepgram.TTS(model="aura-2-andromeda-en"),
        tools=build_tools(pool, session_ctx, evidence_sink=evidence_sink),
        max_tool_steps=5,
        tts_text_transforms=[
            encode_tags(controller.vocabulary, controller.count_tag),
            "filter_markdown", "filter_emoji", decode_tags,
        ],
        expressive=False,
    )

    # The profile-owned Halloween voices live OUTSIDE the session's error counter (C10): a vendor hiccup must
    # never close the session. Build them through a wrapper that subscribes error/metrics for observability
    # only (never feeding the session's counter); they are cached + aclose()d in the shutdown lifecycle.
    def _build_tracked_tts(profile):
        tts_obj = build_tts(profile)
        try:
            tts_obj.on("error", lambda ev: print(
                f"[ug] halloween_tts error (profile={profile.key}): {getattr(ev, 'error', ev)!r}", flush=True))
            tts_obj.on("metrics_collected", lambda ev: None)
        except Exception:  # noqa: BLE001 - subscription is best-effort
            pass
        return tts_obj

    # Build the StudioAgent (NOT Agent): fallback_profile= (NOT profiles= — that is a TypeError), build_tts_fn=.
    studio_agent = StudioAgent(
        instructions=controller.instructions(),
        controller=controller,
        fallback_profile=profiles["halloween_fallback"],
        build_tts_fn=_build_tracked_tts,
    )

    if trace_provider is not None:
        fill_ug_metadata(trace_enrichment, bind, session_ctx)

        @session.on("conversation_item_added")
        def _on_item(ev) -> None:
            role = getattr(ev.item, "role", None)
            text = (getattr(ev.item, "text_content", None) or "").strip()
            if not text:
                return
            if role == "user" and "mlflow.spanInputs" not in trace_enrichment:
                trace_enrichment["mlflow.spanInputs"] = json.dumps({"user": text[:300]})
            elif role == "assistant":
                trace_enrichment["mlflow.spanOutputs"] = json.dumps({"assistant": text[:300]})

    # Reveal the routing decision to the in-page panel (Choice + Control): the operator UI
    # sees the tier/model; the LLM never does. (PII-free: no caller name here.)
    await evidence_sink({"bind": {"company": bind.company, "tier": bind.tier,
                                  "model": bind.model, "directives": bind.directives}})

    # Control pillar: publish the initial STANDARD voice_mode snapshot so the "Voice mode · AI Decide" row
    # renders on a standard call too (the UI already handles path:"none"/probabilities:null).
    await evidence_sink({"voice_mode": {
        "mode": controller.state.mode, "voice": controller.profile.label,
        "decided_by": decider.label, "engine": decider.engine,
        "path": "none", "confidence": 0.0, "probabilities": None,
        "latency_ms": None, "reason": "none", "degraded": False,
    }})

    # Costs pillar: publish cumulative LLM token usage (agent-reported, PII-free) after
    # each LLM call; the client diffs cumulative totals into per-turn bars.
    @session.on("session_usage_updated")
    def _on_usage(ev) -> None:
        try:
            llm = [u for u in ev.usage.model_usage if getattr(u, "type", "") == "llm_usage"]
            if not llm:
                return
            usage = {
                "input_tokens": sum(int(u.input_tokens) for u in llm),
                "output_tokens": sum(int(u.output_tokens) for u in llm),
            }
            asyncio.ensure_future(evidence_sink({"usage": usage}))
        except Exception:
            pass  # usage is cosmetic — never disrupt the voice pipeline

    # --- Voice-mode event hooks: feed the controller the final transcript (prefetch the background decision)
    #     and the agent's spoken line (the decider's agent_said, tags stripped inside note_agent_line). ---
    @session.on("user_input_transcribed")
    def _on_transcribed(ev) -> None:
        if getattr(ev, "is_final", False):
            controller.prefetch(getattr(ev, "transcript", "") or "")

    @session.on("conversation_item_added")
    def _on_agent_line(ev) -> None:
        if getattr(ev.item, "role", None) == "assistant":
            controller.note_agent_line(getattr(ev.item, "text_content", None) or "")

    # --- Shutdown: close everything the voice subsystem owns (the session owns only its own TTS). The
    #     controller drains its background classify/announce tasks; the decider closes its HTTP client; the
    #     cached profile TTS instances are aclose()d. All fail-soft. ---
    async def _close_voice() -> None:
        try:
            await controller.aclose()
        except Exception:  # noqa: BLE001
            pass
        try:
            await decider.aclose()
        except Exception:  # noqa: BLE001
            pass
        for tts_obj in list(studio_agent._tts_by_key.values()):
            try:
                await tts_obj.aclose()
            except Exception:  # noqa: BLE001
                pass
    ctx.add_shutdown_callback(_close_voice)

    await session.start(room=ctx.room, agent=studio_agent)

    # --- Governed greeting: courtesy name if we have it; warm ack ONLY when the directive says so. ---
    warm = bind.directives.get("recognition_tone") == "warm"
    if bind.courtesy_name and warm:
        greeting = (f"Greet {bind.courtesy_name} warmly for contacting {bind.company}. Give ONE brief, "
                    "warm acknowledgement that they are valued (never state any status, tier, or number), "
                    "then ask how you can help. Three short, warm sentences.")
    elif bind.courtesy_name:
        greeting = (f"Greet {bind.courtesy_name} for contacting {bind.company} and ask how you can help. "
                    "Two short, calm sentences; do not mention loyalty or status.")
    else:
        greeting = (f"Thank the caller for contacting {bind.company} and ask how you can help today. "
                    "Two short, calm sentences.")
    await session.generate_reply(instructions=greeting)


if __name__ == "__main__":
    agents.cli.run_app(server)
