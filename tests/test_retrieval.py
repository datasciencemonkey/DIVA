import src.services.retrieval as r


class _Pool:  # sentinel; retrieval calls the module-level _run_query, which we patch
    pass


async def test_semantic_search_is_scoped_and_orders_by_distance(monkeypatch):
    seen = {}
    async def fake(pool, sql, params=None):
        seen["sql"], seen["params"] = sql.lower(), params
        return [{"doc_id": "d1", "title": "t", "chunk_text": "c", "score": 0.9}]
    monkeypatch.setattr(r, "_run_query", fake)
    out = await r.semantic_search(_Pool(), "G1", [0.1, 0.2], k=3)
    assert out and out[0]["doc_id"] == "d1"
    assert "data_generation_id = %(gid)s" in seen["sql"]
    assert "<=>" in seen["sql"] and "limit" in seen["sql"]
    assert seen["params"]["gid"] == "G1" and seen["params"]["k"] == 3


async def test_keyword_search_is_scoped_and_uses_bm25(monkeypatch):
    seen = {}
    async def fake(pool, sql, params=None):
        seen["sql"] = sql.lower(); seen["params"] = params
        return []
    monkeypatch.setattr(r, "_run_query", fake)
    out = await r.keyword_search(_Pool(), "G1", "delayed order", k=4)
    assert out == []                      # retrieval miss -> clean empty list
    assert "data_generation_id = %(gid)s" in seen["sql"]
    assert "to_bm25query" in seen["sql"]
    assert seen["params"]["gid"] == "G1"


async def test_keyword_search_drops_non_matches(monkeypatch):
    # BM25 scores matches negative, non-matches ~0. Only real matches must survive,
    # so a sparse-match query can't pad results with irrelevant docs (governance §12.7).
    async def fake(pool, sql, params=None):
        return [{"doc_id": "d1", "score": -1.2},
                {"doc_id": "d2", "score": 0.0},
                {"doc_id": "d3", "score": -0.0}]
    monkeypatch.setattr(r, "_run_query", fake)
    out = await r.keyword_search(_Pool(), "G1", "returns", k=5)
    assert [d["doc_id"] for d in out] == ["d1"]


async def test_record_lookup_scoped_by_gid_and_optional_customer(monkeypatch):
    seen = {}
    async def fake(pool, sql, params=None):
        seen["sql"] = sql.lower(); seen["params"] = params
        return [{"record_id": "r1"}]
    monkeypatch.setattr(r, "_run_query", fake)
    await r.record_lookup(_Pool(), "G1", customer_id="C1")
    assert "data_generation_id = %(gid)s" in seen["sql"]
    assert "customer_id = %(cid)s" in seen["sql"]
    assert seen["params"] == {"gid": "G1", "cid": "C1"}
