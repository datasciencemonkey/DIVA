"""Read-only retrieval over the ug schema, always scoped by data_generation_id
(spec §12, §13). Hybrid Lakebase Search: ANN (lakebase_ann, cosine <=>) + BM25
(lakebase_bm25, <@> to_bm25query). record_lookup is plain SQL. Never returns the
loyalty tier. The query embedding is supplied by the caller (embeddings.embed_texts).

Query forms verified in docs/discovery/embeddings-and-index-contract.md.
"""
from __future__ import annotations

from src.services.db import SCHEMA, _run_query
from src.services.embeddings import to_pgvector


async def semantic_search(pool, data_generation_id, query_embedding, k: int = 5) -> list[dict]:
    sql = (
        "SELECT doc_id, title, chunk_text, 1 - (embedding <=> %(q)s::vector) AS score "
        f"FROM {SCHEMA}.documents "
        "WHERE data_generation_id = %(gid)s AND embedding IS NOT NULL "
        "ORDER BY embedding <=> %(q)s::vector LIMIT %(k)s"
    )
    return await _run_query(pool, sql, {
        "q": to_pgvector(query_embedding), "gid": data_generation_id, "k": k})


async def keyword_search(pool, data_generation_id, query: str, k: int = 5) -> list[dict]:
    # BM25 via lakebase_bm25: <@> is a SCORING operator (more negative = more
    # relevant); the 2nd arg of to_bm25query is the BM25 INDEX regclass. ORDER BY ASC.
    bm25 = (f"content_tsv <@> to_bm25query(to_tsvector('english', %(q)s), "
            f"'{SCHEMA}.documents_bm25'::regclass)")
    sql = (
        f"SELECT doc_id, title, chunk_text, {bm25} AS score "
        f"FROM {SCHEMA}.documents "
        "WHERE data_generation_id = %(gid)s "
        "ORDER BY score ASC LIMIT %(k)s"
    )
    return await _run_query(pool, sql, {"q": query, "gid": data_generation_id, "k": k})


async def record_lookup(pool, data_generation_id, customer_id=None, kind=None) -> list[dict]:
    clauses = ["data_generation_id = %(gid)s"]
    params: dict = {"gid": data_generation_id}
    if customer_id is not None:
        clauses.append("customer_id = %(cid)s")
        params["cid"] = customer_id
    if kind is not None:
        clauses.append("kind = %(kind)s")
        params["kind"] = kind
    sql = (f"SELECT record_id, customer_id, kind, fields, status FROM {SCHEMA}.records "
           f"WHERE {' AND '.join(clauses)} LIMIT 25")
    return await _run_query(pool, sql, params)
