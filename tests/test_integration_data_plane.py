import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("LAKEBASE_ENDPOINT"), reason="live Lakebase env not set")

_GEN_MODEL = os.getenv("UG_GEN_MODEL", "databricks-gpt-5-4")


async def test_generate_two_datasets_and_retrieve_isolated():
    """Spec §18 property test: two real datasets; every retrieval path returns only
    its own data_generation_id's rows; a keyword miss returns []."""
    from src.services.db import create_pool
    from src.services.embeddings import embed_texts
    from src.services import retrieval
    from src.generate import generate_dataset

    pool = await create_pool()
    try:
        gid_a = await generate_dataset(
            "Northwind Outfitters", "customer support",
            "Answer questions about orders, returns, and shipping.", model=_GEN_MODEL, pool=pool)
        gid_b = await generate_dataset(
            "Cascade Airlines", "customer support",
            "Answer questions about flights, baggage, and refunds.", model=_GEN_MODEL, pool=pool)
        assert gid_a != gid_b

        qvec = embed_texts(["how do returns work?"])[0]

        # semantic: each dataset returns only its own docs (doc_id is prefixed with the gid)
        sem_a = await retrieval.semantic_search(pool, gid_a, qvec, k=3)
        assert sem_a and all(row["doc_id"].startswith(gid_a) for row in sem_a)
        assert all("data_generation_id" not in row for row in sem_a)  # scoping field not leaked
        sem_b = await retrieval.semantic_search(pool, gid_b, qvec, k=3)
        assert all(row["doc_id"].startswith(gid_b) for row in sem_b)

        # keyword: scoped to its own gid; a nonsense token returns [] (no fabrication padding)
        kw_a = await retrieval.keyword_search(pool, gid_a, "returns", k=3)
        assert all(row["doc_id"].startswith(gid_a) for row in kw_a)
        assert await retrieval.keyword_search(pool, gid_a, "zxqwv-nonsense-token", k=3) == []

        # record_lookup: scoped to its own gid (record_id is prefixed with the gid)
        rec_a = await retrieval.record_lookup(pool, gid_a)
        assert all(row["record_id"].startswith(gid_a) for row in rec_a)
        rec_b = await retrieval.record_lookup(pool, gid_b)
        assert all(row["record_id"].startswith(gid_b) for row in rec_b)

        # nonexistent gid returns nothing on every path
        assert await retrieval.semantic_search(pool, "does-not-exist", qvec, k=3) == []
        assert await retrieval.record_lookup(pool, "does-not-exist") == []
    finally:
        await pool.close()
