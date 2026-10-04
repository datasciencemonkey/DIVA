import base64
import datetime as _dt
import http.client
import json
import os
import threading
from functools import partial
from http.server import ThreadingHTTPServer


def _load(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://x")
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")
    import importlib
    import app.web_server as ws
    return importlib.reload(ws)


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def test_clean_name_strips_non_name_chars(monkeypatch):
    ws = _load(monkeypatch)
    assert ws.clean_name("Sam <script>3") == "Sam script"  # letters/space/-'. kept, digits/<> dropped


def test_token_carries_dataset_and_customer_in_metadata(monkeypatch):
    ws = _load(monkeypatch)
    tok = ws.mint_token(name="Sam", data_generation_id="G1", customer_id="C1")
    claims = _claims(tok["token"])
    assert claims["name"] == "Sam"
    md = json.loads(claims["metadata"])
    assert md == {"data_generation_id": "G1", "customer_id": "C1"}
    assert claims["roomConfig"]["agents"][0]["agentName"]  # agent dispatch embedded
    assert claims["video"]["room"] == tok["roomName"]        # unique room per visit


# --- generation job store + /api/generate + /api/datasets (Plan 4 backend) ---


def test_new_job_seeds_queued_entry(monkeypatch):
    ws = _load(monkeypatch)
    ws._new_job("j1")
    assert ws._JOBS["j1"] == {"stage": "queued", "detail": {}, "done": False, "error": None}


def test_run_generate_job_stores_ready_with_customers(monkeypatch):
    ws = _load(monkeypatch)

    class _Pool:
        closed = False

        async def close(self):
            self.closed = True

    pool = _Pool()

    async def fake_create_pool():
        return pool

    async def fake_generate(company, role, system_prompt, *, model, pool, progress=None):
        assert model  # UG_GEN_MODEL default flows through
        progress("drafting", {})
        progress("drafted", {"documents": 5, "customers": 3, "records": 2})
        progress("embedding", {"count": 5})
        progress("saving", {})
        progress("ready", {"data_generation_id": "GID9", "doc_count": 5, "customer_count": 3})
        return "GID9"

    async def fake_run_query(p, sql, params=None):
        assert p is pool
        assert "customers" in sql.lower()
        assert params == {"g": "GID9"}
        return [
            {"customer_id": "GID9-C0", "display_name": "Ada", "loyalty_tier": "Standard"},
            {"customer_id": "GID9-C1", "display_name": "Kat", "loyalty_tier": "VIP"},
        ]

    monkeypatch.setattr(ws, "create_pool", fake_create_pool)
    monkeypatch.setattr(ws, "generate_dataset", fake_generate)
    monkeypatch.setattr(ws, "_run_query", fake_run_query)

    ws._new_job("j2")
    ws._run_generate_job("j2", "Acme", "support", "help")

    job = ws._JOBS["j2"]
    assert job["stage"] == "ready"
    assert job["done"] is True
    assert job["error"] is None
    assert job["data_generation_id"] == "GID9"
    assert [c["customer_id"] for c in job["customers"]] == ["GID9-C0", "GID9-C1"]
    assert job["detail"] == {"data_generation_id": "GID9", "doc_count": 5, "customer_count": 3}
    assert pool.closed is True  # pool always closed in finally


def test_run_generate_job_records_failure_and_closes_pool(monkeypatch):
    ws = _load(monkeypatch)

    class _Pool:
        closed = False

        async def close(self):
            self.closed = True

    pool = _Pool()

    async def fake_create_pool():
        return pool

    async def fake_generate(*a, progress=None, **k):
        progress("drafting", {})
        raise RuntimeError("llm down")

    monkeypatch.setattr(ws, "create_pool", fake_create_pool)
    monkeypatch.setattr(ws, "generate_dataset", fake_generate)

    ws._new_job("j3")
    ws._run_generate_job("j3", "Acme", "support", "help")

    job = ws._JOBS["j3"]
    assert job["done"] is True
    assert job["stage"] == "failed"
    assert job["error"] == "llm down"
    assert pool.closed is True


def test_status_payload_returns_copy_and_none_for_unknown(monkeypatch):
    ws = _load(monkeypatch)
    ws._new_job("j4")
    payload = ws._status_payload("j4")
    assert payload == {"stage": "queued", "detail": {}, "done": False, "error": None}
    # a returned copy must not alias the live store
    payload["stage"] = "mutated"
    assert ws._JOBS["j4"]["stage"] == "queued"
    assert ws._status_payload("does-not-exist") is None


def test_datasets_payload_fail_soft_when_pool_errors(monkeypatch):
    ws = _load(monkeypatch)

    async def boom():
        raise RuntimeError("no lakebase")

    monkeypatch.setattr(ws, "create_pool", boom)
    assert ws._datasets_payload() == {"datasets": []}


def test_datasets_payload_isoformats_and_shapes_rows(monkeypatch):
    ws = _load(monkeypatch)

    class _Pool:
        async def close(self):
            pass

    async def fake_create_pool():
        return _Pool()

    async def fake_run_query(pool, sql, params=None):
        low = sql.lower()
        assert "status='ready'" in low
        assert "order by created_at desc" in low
        return [
            {"data_generation_id": "G2", "company_name": "Beta", "status": "ready",
             "doc_count": 6, "customer_count": 4,
             "created_at": _dt.datetime(2026, 9, 24, 12, 0, 0)},
        ]

    monkeypatch.setattr(ws, "create_pool", fake_create_pool)
    monkeypatch.setattr(ws, "_run_query", fake_run_query)

    out = ws._datasets_payload()
    assert list(out.keys()) == ["datasets"]
    row = out["datasets"][0]
    assert row["created_at"] == "2026-09-24T12:00:00"
    assert row["company_name"] == "Beta" and row["data_generation_id"] == "G2"
    assert row["doc_count"] == 6 and row["customer_count"] == 4


def _serve(ws):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(ws.Handler, directory=str(ws.PUBLIC)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_http_routes_dispatch_and_status_codes(monkeypatch):
    ws = _load(monkeypatch)

    # The POST handler spawns a worker thread; stub it so no real DB is touched.
    monkeypatch.setattr(ws, "_run_generate_job", lambda *a, **k: None)

    # /api/datasets must stay fail-soft when the pool cannot be created.
    async def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(ws, "create_pool", boom)

    server = _serve(ws)
    host, port = server.server_address
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        body = json.dumps({"company": "Acme", "role": "support", "system_prompt": "help"})
        conn.request("POST", "/api/generate", body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 202
        job_id = json.loads(resp.read())["job_id"]
        assert job_id
        assert ws._JOBS[job_id]["stage"] == "queued"  # seeded synchronously before 202

        conn.request("GET", f"/api/generate/status?job_id={job_id}")
        resp = conn.getresponse()
        assert resp.status == 200
        status = json.loads(resp.read())
        assert status["stage"] == "queued" and status["done"] is False

        conn.request("GET", "/api/generate/status?job_id=missing")
        resp = conn.getresponse()
        assert resp.status == 404
        assert json.loads(resp.read()) == {"error": "unknown job"}

        conn.request("GET", "/api/datasets")
        resp = conn.getresponse()
        assert resp.status == 200
        assert json.loads(resp.read()) == {"datasets": []}
    finally:
        conn.close()
        server.shutdown()
