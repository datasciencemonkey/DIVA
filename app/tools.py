"""Generic read-only voice-agent tools (spec §13), scoped to the session's
data_generation_id. Handlers are pure of livekit so they unit-test without it;
build_tools wraps them as function_tools. Tools NEVER return the loyalty tier.

NOTE: no `from __future__ import annotations` here on purpose — build_tools' function_tool
signatures annotate `context: RunContext` (imported inside build_tools), and LiveKit resolves
those via typing.get_type_hints() at session start. Deferred (string) annotations can't see the
build_tools-local RunContext and raise NameError on every LLM turn. Eager annotations bind the
real class object, so get_type_hints succeeds. The module still imports no livekit at top level."""

import asyncio
from dataclasses import dataclass

from src.services import retrieval
from src.services.embeddings import embed_texts


@dataclass
class SessionContext:
    data_generation_id: str
    customer_id: str | None = None
    last_retrieval: dict | None = None


async def _do_semantic_search(pool, data_generation_id, query, *, evidence_sink=None) -> dict:
    if pool is None:  # degraded (spec §17): abstain — never embed or query, never raise
        return {"results": []}
    qvec = (await asyncio.to_thread(embed_texts, [query]))[0]
    hits = await retrieval.semantic_search(pool, data_generation_id, qvec, k=5)
    result = {"results": [{"title": h.get("title"), "snippet": h["chunk_text"]} for h in hits]}
    if evidence_sink is not None:
        try:
            await evidence_sink({"retrieval": {"kind": "semantic", "query": query, "hits": len(hits)}})
        except Exception:
            pass
    return result


async def _do_record_lookup(pool, data_generation_id, customer_id, query=None, *, evidence_sink=None) -> dict:
    # Abstain when degraded (pool None, §17) OR the caller is unidentified. An unknown
    # caller must NEVER receive another customer's records: retrieval.record_lookup is
    # unscoped when customer_id is None, so the tool — not the LLM — is the boundary (§12).
    if pool is None or not customer_id:
        return {"records": []}
    recs = await retrieval.record_lookup(pool, data_generation_id, customer_id=customer_id)
    result = {"records": [{"kind": r.get("kind"), "fields": r.get("fields"), "status": r.get("status")}
                          for r in recs]}
    if evidence_sink is not None:
        try:
            await evidence_sink({"retrieval": {"kind": "record", "hits": len(recs)}})
        except Exception:
            pass
    return result


def build_tools(pool, session_ctx: SessionContext, *, evidence_sink=None) -> list:
    from livekit.agents import RunContext, function_tool

    async def semantic_search(context: RunContext, query: str) -> dict:
        """Search the company's documents/policies/FAQ for information to answer the caller."""
        return await _do_semantic_search(pool, session_ctx.data_generation_id, query,
                                         evidence_sink=evidence_sink)

    async def record_lookup(context: RunContext, query: str = "") -> dict:
        """Look up the caller's own records (orders/cases). Returns only their records."""
        return await _do_record_lookup(pool, session_ctx.data_generation_id, session_ctx.customer_id,
                                       query=query, evidence_sink=evidence_sink)

    return [
        function_tool(semantic_search, name="semantic_search",
                      description="Search the company's documents, policies, and FAQ. Call this to "
                                  "answer questions about the company or its processes. If it returns "
                                  "no results, tell the caller you don't have that information."),
        function_tool(record_lookup, name="record_lookup",
                      description="Look up the caller's own records (e.g. their orders or cases). "
                                  "Returns only what exists; if empty, say you couldn't find it."),
    ]
