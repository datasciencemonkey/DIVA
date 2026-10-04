"""Synthetic dataset generator (spec §9). Dogfoods UAIG (chat) + FMAPI (embeddings).

The generator is the only writer in v1; the agent's runtime tools stay read-only.
`build_rows` is pure (unit-tested); `generate_dataset` does the live I/O.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from src.policy.tiers import VALID_TIERS
from src.services.db import SCHEMA, _run_query
from src.services.embeddings import embed_texts, to_pgvector
from src.services.uaig_chat import complete_json

_SYNTH = json.dumps({"synthetic": True})

_GEN_SYSTEM = (
    "You generate SMALL, clearly SYNTHETIC customer-support datasets for a demo. "
    "Return ONLY a JSON object with keys: "
    "documents (5-8 items, each {title, chunk_text} — short policy/FAQ/process snippets); "
    "customers (12-18 items, each {display_name, loyalty_tier}, loyalty_tier one of "
    "Standard/Premium/VIP, and ALL THREE tiers MUST appear); "
    "records (6-12 items, each {customer_index (int index into customers), kind, "
    "fields (object), status}). Concise, realistic for the company; all fictional."
)


@dataclass
class GenSpec:
    company: str
    role: str
    system_prompt: str
    data_generation_id: str


@dataclass
class Rows:
    documents: list[dict] = field(default_factory=list)
    customers: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)


def build_rows(spec: GenSpec, llm: dict) -> Rows:
    gid = spec.data_generation_id
    customers = [
        {"data_generation_id": gid, "customer_id": f"{gid}-C{i}",
         "display_name": c["display_name"], "loyalty_tier": c["loyalty_tier"],
         "attributes": _SYNTH}
        for i, c in enumerate(llm["customers"])
    ]
    if {c["loyalty_tier"] for c in customers} != set(VALID_TIERS):
        raise ValueError(f"generated customers must cover all tiers {VALID_TIERS}")
    documents = [
        {"data_generation_id": gid, "doc_id": f"{gid}-D{i}",
         "title": d["title"], "chunk_text": d["chunk_text"], "metadata": _SYNTH}
        for i, d in enumerate(llm["documents"])
    ]
    records = []
    for i, rec in enumerate(llm.get("records", [])):
        ci = rec.get("customer_index", 0)
        if not isinstance(ci, int) or not (0 <= ci < len(customers)):
            ci = 0
        records.append(
            {"data_generation_id": gid, "record_id": f"{gid}-R{i}",
             "customer_id": customers[ci]["customer_id"], "kind": rec.get("kind"),
             "fields": json.dumps(rec.get("fields", {})), "status": rec.get("status")})
    return Rows(documents=documents, customers=customers, records=records)


async def generate_dataset(
    company: str,
    role: str,
    system_prompt: str,
    *,
    model: str,
    pool,
    progress: Callable[[str, dict], None] | None = None,
) -> str:
    """Draft (UAIG) -> build rows -> embed (FMAPI) -> write in one transaction ->
    register. Transactional: a failure leaves datasets.status='failed', never partial.

    ``progress`` is an optional narration hook invoked with (stage, detail) as the
    run advances (stages: drafting, drafted, embedding, saving, ready, failed). It is
    advisory only: every call is wrapped so a callback exception never breaks generation.
    Default ``None`` keeps the function backward compatible (no narration)."""

    def emit(stage: str, detail: dict) -> None:
        if progress is None:
            return
        try:
            progress(stage, detail)
        except Exception:  # noqa: BLE001 — narration must never break generation
            pass

    gid = uuid.uuid4().hex
    await _run_query(pool,
        f"INSERT INTO {SCHEMA}.datasets "
        "(data_generation_id, company_name, assistant_role, system_prompt, generator_model, status) "
        "VALUES (%(gid)s, %(c)s, %(r)s, %(sp)s, %(m)s, 'pending')",
        {"gid": gid, "c": company, "r": role, "sp": system_prompt, "m": model})
    try:
        emit("drafting", {})
        llm = await asyncio.to_thread(complete_json, _GEN_SYSTEM,
                                      f"Company: {company}\nAssistant role: {role}\nGenerate the dataset now.",
                                      model)
        rows = build_rows(GenSpec(company, role, system_prompt, gid), llm)
        emit("drafted", {"documents": len(rows.documents),
                         "customers": len(rows.customers),
                         "records": len(rows.records)})
        emit("embedding", {"count": len(rows.documents)})
        vecs = await asyncio.to_thread(embed_texts, [d["chunk_text"] for d in rows.documents])
        emit("saving", {})
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                for d, v in zip(rows.documents, vecs):
                    await cur.execute(
                        f"INSERT INTO {SCHEMA}.documents "
                        "(data_generation_id, doc_id, title, chunk_text, metadata, embedding, content_tsv) "
                        "VALUES (%(gid)s, %(id)s, %(t)s, %(ct)s, %(md)s::jsonb, %(emb)s::vector, "
                        "to_tsvector('english', %(ct)s))",
                        {"gid": gid, "id": d["doc_id"], "t": d["title"], "ct": d["chunk_text"],
                         "md": d["metadata"], "emb": to_pgvector(v)})
                for c in rows.customers:
                    await cur.execute(
                        f"INSERT INTO {SCHEMA}.customers "
                        "(data_generation_id, customer_id, display_name, loyalty_tier, attributes) "
                        "VALUES (%(gid)s, %(id)s, %(n)s, %(lt)s, %(a)s::jsonb)",
                        {"gid": gid, "id": c["customer_id"], "n": c["display_name"],
                         "lt": c["loyalty_tier"], "a": c["attributes"]})
                for rec in rows.records:
                    await cur.execute(
                        f"INSERT INTO {SCHEMA}.records "
                        "(data_generation_id, record_id, customer_id, kind, fields, status) "
                        "VALUES (%(gid)s, %(id)s, %(cid)s, %(k)s, %(f)s::jsonb, %(s)s)",
                        {"gid": gid, "id": rec["record_id"], "cid": rec["customer_id"],
                         "k": rec["kind"], "f": rec["fields"], "s": rec["status"]})
        await _run_query(pool,
            f"UPDATE {SCHEMA}.datasets SET status='ready', doc_count=%(dc)s, customer_count=%(cc)s "
            "WHERE data_generation_id=%(gid)s",
            {"dc": len(rows.documents), "cc": len(rows.customers), "gid": gid})
        emit("ready", {"data_generation_id": gid,
                       "doc_count": len(rows.documents),
                       "customer_count": len(rows.customers)})
        return gid
    except Exception as exc:
        emit("failed", {"error": str(exc)})
        await _run_query(pool,
            f"UPDATE {SCHEMA}.datasets SET status='failed' WHERE data_generation_id=%(gid)s",
            {"gid": gid})
        raise
