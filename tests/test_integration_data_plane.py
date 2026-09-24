import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("LAKEBASE_ENDPOINT"), reason="live Lakebase env not set")


async def test_generate_then_retrieve_scoped():
    from src.services.db import create_pool
    from src.services.embeddings import embed_texts
    from src.services import retrieval
    from src.generate import generate_dataset

    pool = await create_pool()
    try:
        gid = await generate_dataset(
            "Northwind Outfitters", "customer support",
            "Answer questions about orders, returns, and shipping.",
            model=os.getenv("UG_GEN_MODEL", "databricks-gpt-5-4"), pool=pool)

        qvec = embed_texts(["how do returns work?"])[0]
        sem = await retrieval.semantic_search(pool, gid, qvec, k=3)
        # semantic search returns rows and never leaks the scoping/tier fields in the payload
        assert sem and all("data_generation_id" not in row for row in sem)

        kw = await retrieval.keyword_search(pool, gid, "returns", k=3)
        assert isinstance(kw, list)

        # isolation: a different (nonexistent) data_generation_id returns nothing
        assert await retrieval.semantic_search(pool, "does-not-exist", qvec, k=3) == []
    finally:
        await pool.close()
