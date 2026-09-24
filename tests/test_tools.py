import app.tools as tools


async def test_semantic_handler_embeds_query_scopes_and_maps_snippet(monkeypatch):
    seen = {}
    monkeypatch.setattr(tools, "embed_texts", lambda xs: [[0.1, 0.2] for _ in xs])
    async def fake_sem(pool, gid, vec, k=5):
        seen["gid"], seen["vec"] = gid, vec
        return [{"doc_id": "d1", "title": "Returns", "chunk_text": "30 days.", "score": 0.9}]
    monkeypatch.setattr(tools.retrieval, "semantic_search", fake_sem)
    out = await tools._do_semantic_search(object(), "G1", "how do returns work")
    assert seen["gid"] == "G1" and seen["vec"] == [0.1, 0.2]
    assert out["results"] == [{"title": "Returns", "snippet": "30 days."}]  # snippet mapped; no tier/doc_id


async def test_record_lookup_handler_scoped_to_bound_customer(monkeypatch):
    seen = {}
    async def fake_rec(pool, gid, customer_id=None, kind=None):
        seen["gid"], seen["cid"] = gid, customer_id
        return [{"record_id": "r1", "kind": "order", "fields": {"item": "x"}, "status": "shipped"}]
    monkeypatch.setattr(tools.retrieval, "record_lookup", fake_rec)
    out = await tools._do_record_lookup(object(), "G1", "C1")
    assert seen["gid"] == "G1" and seen["cid"] == "C1"
    assert out["records"][0]["status"] == "shipped"


async def test_semantic_miss_returns_empty_results(monkeypatch):
    monkeypatch.setattr(tools, "embed_texts", lambda xs: [[0.0] for _ in xs])
    async def fake_sem(pool, gid, vec, k=5): return []
    monkeypatch.setattr(tools.retrieval, "semantic_search", fake_sem)
    out = await tools._do_semantic_search(object(), "G1", "nonsense")
    assert out == {"results": []}


async def test_record_lookup_abstains_for_unknown_caller(monkeypatch):
    # An unidentified caller (no customer_id) must NEVER receive another customer's
    # records: the tool abstains WITHOUT touching the unscoped query path (governance §12).
    called = False
    async def fake_rec(pool, gid, customer_id=None, kind=None):
        nonlocal called
        called = True
        return [{"record_id": "r1", "kind": "order", "fields": {"x": 1}, "status": "shipped"}]
    monkeypatch.setattr(tools.retrieval, "record_lookup", fake_rec)
    out = await tools._do_record_lookup(object(), "G1", None)
    assert out == {"records": []}
    assert called is False  # never reached the (unscoped) DB lookup


async def test_handlers_abstain_when_pool_is_none(monkeypatch):
    # Degraded mode (spec §17): Lakebase unreachable -> pool is None. Tools must abstain
    # (empty result), never raise into the voice loop, and never even embed.
    def boom_embed(xs):
        raise AssertionError("must not embed when pool is None")
    async def boom(*a, **k):
        raise AssertionError("must not query when pool is None")
    monkeypatch.setattr(tools, "embed_texts", boom_embed)
    monkeypatch.setattr(tools.retrieval, "semantic_search", boom)
    monkeypatch.setattr(tools.retrieval, "record_lookup", boom)
    assert await tools._do_semantic_search(None, "G1", "q") == {"results": []}
    assert await tools._do_record_lookup(None, "G1", "C1") == {"records": []}
