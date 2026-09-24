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

_APP_DIR = Path(__file__).parent
_REPO_ROOT = _APP_DIR.parent
for _p in (str(_APP_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env.local", override=False)

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.agents.telemetry import set_tracer_provider
from livekit.plugins import deepgram, openai

from src.agent_prompt import build_instructions
from src.services.db import create_pool
from src.services.session_bind import bind_session
from app.tools import SessionContext, build_tools
from app.tracing import build_tracer_provider, fill_ug_metadata

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


@server.rtc_session(agent_name=_AGENT_NAME)
async def entrypoint(ctx: agents.JobContext):
    host = os.getenv("DATABRICKS_HOST", "")
    token = os.getenv("DATABRICKS_TOKEN", "")
    if not host or not token:
        raise RuntimeError("DATABRICKS_HOST / DATABRICKS_TOKEN not set — check .env.local")

    # --- OTel tracing (fail-soft) ---
    trace_enrichment: dict[str, str] = {}
    trace_provider = build_tracer_provider(trace_enrichment)
    bind = None
    session_ctx = None
    if trace_provider is not None:
        set_tracer_provider(trace_provider, metadata={"livekit.agent_name": _AGENT_NAME})

        async def _flush_traces() -> None:
            fill_ug_metadata(trace_enrichment, bind, session_ctx)
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

    # --- Build the session on the ROUTED model (the tier's model). ---
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

    await session.start(
        room=ctx.room,
        agent=Agent(instructions=build_instructions(bind.system_prompt, bind.directives, bind.courtesy_name)),
    )

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
