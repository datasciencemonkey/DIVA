"""Generation job-store transitions (Plan 4 review fixes).

The 'ready' transition must become terminal only once the customer list is
attached — otherwise a status poll can strand the fresh-world caller picker
empty (~10% of generations). And a world that committed status='ready' must
never be reported as failed just because the post-commit customers read hiccups.
"""
import app.web_server as ws


def test_generate_ready_progress_stays_nonterminal_until_store_ready():
    ws._new_job("jrace")
    # generate_dataset fires progress("ready", ...) BEFORE the web tier attaches customers.
    ws._store_progress("jrace", "ready",
                       {"data_generation_id": "G", "doc_count": 7, "customer_count": 14})
    mid = ws._status_payload("jrace")
    assert mid["done"] is False          # a poll landing here must NOT be treated as terminal
    assert mid["stage"] != "ready"       # only _store_ready yields terminal "ready"
    assert mid["detail"].get("customer_count") == 14   # counts preserved for the summary
    # Only the customers-attached _store_ready produces the real terminal ready.
    ws._store_ready("jrace", "G",
                    [{"customer_id": "G-C1", "display_name": "X", "loyalty_tier": "VIP"}])
    fin = ws._status_payload("jrace")
    assert fin["done"] is True and fin["stage"] == "ready" and len(fin["customers"]) == 1


async def test_committed_world_reports_ready_even_if_customers_query_fails(monkeypatch):
    class _FakePool:
        async def close(self):
            pass

    async def fake_create_pool():
        return _FakePool()

    async def fake_generate(*a, **k):
        return "GID"

    async def boom_query(*a, **k):
        raise RuntimeError("transient read")

    monkeypatch.setattr(ws, "create_pool", fake_create_pool)
    monkeypatch.setattr(ws, "generate_dataset", fake_generate)
    monkeypatch.setattr(ws, "_run_query", boom_query)

    await ws._generate_job_async("jm1", "Acme", "support", "prompt")
    fin = ws._status_payload("jm1")
    assert fin["done"] is True
    assert fin["stage"] == "ready"        # world exists -> ready, not failed
    assert fin["error"] is None
    assert fin["customers"] == []         # degrade to manual caller entry
    assert fin["data_generation_id"] == "GID"
