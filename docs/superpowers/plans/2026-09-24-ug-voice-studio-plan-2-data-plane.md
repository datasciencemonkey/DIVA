# UG Voice Studio — Plan 2: Data Plane (Generator + Retrieval) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a small per-company dataset (docs + customers-across-tiers + records) into the `ug` Lakebase schema under a fresh `data_generation_id`, and retrieve from it with hybrid Lakebase Search (BM25 + ANN) + structured lookup — all scoped by `data_generation_id`.

**Architecture:** A `uv run` generator dogfoods Unity Gateway (chat for drafting, FMAPI for embeddings) to write synthetic rows into the generic schema, then builds the Lakebase Search indexes. A retrieval service exposes `semantic_search` (ANN), `keyword_search` (BM25), and `record_lookup` (SQL). Builds directly on Plan 1's `db.py` / schema / routing / loyalty modules (merged to `main`).

**Tech Stack:** Python 3.12, `uv`, `pytest`, `psycopg`, `databricks-sdk`, `requests`; Lakebase Search (`lakebase_vector` → `lakebase_ann`, `lakebase_text` → `lakebase_bm25`), pgvector `vector`; OpenAI-compatible Unity Gateway (`/ai-gateway/openai/v1`).

**Spec:** `docs/superpowers/specs/2026-09-24-unity-gateway-voice-studio-design.md`
**Contracts (verified, read these):** `docs/discovery/lakebase-search-contract.md` (Lakebase Search API + create_pool), `docs/discovery/model-routing-contract.md`, `docs/discovery/runtime-contracts.md`.

## Global Constraints

- Run everything with **`uv`**; profile **DEFAULT** (`--profile DEFAULT` / `DATABRICKS_CONFIG_PROFILE=DEFAULT`), never auto-selected.
- Commit as **`datasciencemonkey <datasciencemonkey@gmail.com>`**; end every commit message with `Co-authored-by: Isaac <no-reply@databricks.com>`.
- Python **3.12**. Loyalty tiers exactly `Standard`, `Premium`, `VIP`.
- **Lakebase target (verified):** endpoint `projects/your-project/branches/production/endpoints/primary`, DB `databricks_postgres`, schema `ug`. Env: `DATABRICKS_CONFIG_PROFILE=DEFAULT LAKEBASE_ENDPOINT=projects/your-project/branches/production/endpoints/primary LAKEBASE_DATABASE=databricks_postgres UG_SCHEMA=ug`.
- **Lakebase Search is ENABLED** on your-project. Retrieval = `lakebase_ann` (ANN, cosine `<=>`) + `lakebase_bm25` (BM25, `to_bm25query`); embeddings are **bring-your-own** via FMAPI. pgvector `hnsw` + native FTS/`pg_trgm` are the documented fallback.
- **Read-only distinction:** the *agent's runtime tools* are read-only (Plan 3). The **generator writes** datasets — that is content-prep/setup (spec §9), not an agent tool, and is the only writer in this plan.
- All generated data is **clearly labeled synthetic** (a `synthetic: true` marker in `datasets`/`documents.metadata` and in generated content framing).
- Generation + embeddings go through **Unity Gateway** (`{host}/ai-gateway/openai/v1`); every tier/gen model must be a served endpoint on DEFAULT.
- Everything scoped by **`data_generation_id`** — no query crosses datasets.

## Review Focus

- **Retrieval strictly scoped by `data_generation_id`** — a query for dataset A never returns dataset B's rows — owner: Task 5 (SQL `WHERE data_generation_id = …` on every retrieval path; multi-dataset isolation test).
- **Retrieval miss → clean empty result** (no error), so Plan 3's agent can abstain — owner: Task 5.
- **Generator produces customers across ALL three tiers** (`Standard`/`Premium`/`VIP` all present), or the routing demo can't show contrast — owner: Task 6.
- **Generated content is labeled synthetic** — owner: Task 6.
- **Embedding dimension matches the `documents.embedding` column** — a dim mismatch fails loudly at write, not silently — owner: Tasks 3/4/6.
- **Generator is transactional per `data_generation_id`** — a failed generation leaves no half-written dataset (row marked `failed` or rolled back), and re-running an id replaces cleanly — owner: Task 6.

---

### Task 1: Shared tiers module + empty-model guard (folds in Plan 1's deferred MEDIUMs)

**Files:**
- Create: `src/policy/tiers.py`
- Modify: `src/policy/routing.py`, `src/services/loyalty_context.py`
- Test: `tests/test_tiers.py`, and existing `tests/test_routing.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `src.policy.tiers.VALID_TIERS: tuple[str,...]` and `THOROUGHNESS: dict[str,str]`; `route_for` now **raises `RuntimeError`** when no model resolves.

- [ ] **Step 1: Write the failing tests**

`tests/test_tiers.py`:
```python
from src.policy import tiers


def test_single_source_of_truth_for_tiers():
    assert tiers.VALID_TIERS == ("Standard", "Premium", "VIP")
    # every valid tier has a thoroughness mapping (guards the _THOROUGHNESS KeyError trap)
    assert set(tiers.THOROUGHNESS) == set(tiers.VALID_TIERS)


def test_routing_and_loyalty_share_the_same_constant():
    from src.policy import routing
    from src.services import loyalty_context
    assert routing.VALID_TIERS is tiers.VALID_TIERS
    assert loyalty_context.VALID_TIERS is tiers.VALID_TIERS
```

Add to `tests/test_routing.py`:
```python
def test_route_for_raises_when_no_model_resolves(monkeypatch):
    for v in ("UG_MODEL_STANDARD", "UG_MODEL_PREMIUM", "UG_MODEL_VIP", "UG_MODEL_FALLBACK"):
        monkeypatch.delenv(v, raising=False)
    import pytest
    with pytest.raises(RuntimeError):
        route_for("Standard")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_tiers.py tests/test_routing.py::test_route_for_raises_when_no_model_resolves -q`
Expected: FAIL — `ModuleNotFoundError: src.policy.tiers` / `route_for` returns `""` instead of raising.

- [ ] **Step 3: Create `src/policy/tiers.py`**

```python
"""Canonical loyalty-tier constants — the single source of truth (spec §11)."""
from __future__ import annotations

VALID_TIERS: tuple[str, ...] = ("Standard", "Premium", "VIP")

THOROUGHNESS: dict[str, str] = {
    "Standard": "concise",
    "Premium": "balanced",
    "VIP": "thorough",
}
```

- [ ] **Step 4: Refactor `routing.py` and `loyalty_context.py` to import the shared constant**

In `src/policy/routing.py`: replace the local `VALID_TIERS = (...)` and `_THOROUGHNESS = {...}` with `from src.policy.tiers import VALID_TIERS, THOROUGHNESS as _THOROUGHNESS`. Then change `route_for`'s last line to guard empty models:
```python
def route_for(loyalty_tier: str | None) -> RoutingDecision:
    tier = loyalty_tier if loyalty_tier in VALID_TIERS else "Standard"
    model = os.getenv(_ENV_BY_TIER[tier], "") or os.getenv("UG_MODEL_FALLBACK", "")
    if not model:
        raise RuntimeError(
            f"No model configured for tier {tier!r} and no UG_MODEL_FALLBACK set — "
            "set UG_MODEL_STANDARD/PREMIUM/VIP + UG_MODEL_FALLBACK (see model-routing-contract.md)."
        )
    return RoutingDecision(tier=tier, model=model, directives=_directives_for(tier))
```
In `src/services/loyalty_context.py`: replace its local `VALID_TIERS = (...)` with `from src.policy.tiers import VALID_TIERS`.

- [ ] **Step 5: Run to verify green (whole suite — the refactor touches shared modules)**

Run: `uv run pytest -q`
Expected: PASS — all prior tests (routing/loyalty) still green + the 3 new tests. (The autouse `_models` fixture in `test_routing.py` sets the env, so existing routing tests don't hit the new guard.)

- [ ] **Step 6: Commit**

```bash
git add src/policy/tiers.py src/policy/routing.py src/services/loyalty_context.py tests/test_tiers.py tests/test_routing.py
git commit -m "refactor: shared tiers module + route_for empty-model guard

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 2: Discovery — embedding endpoint + exact Lakebase Search index/query DDL

**Files:**
- Create: `docs/discovery/embeddings-and-index-contract.md`

**Interfaces:**
- Consumes: the live Lakebase (ug schema, Search enabled) + DEFAULT gateway.
- Produces (verified facts consumed by Tasks 3–5): `EMBED_MODEL`, `EMBED_DIM`; the exact `CREATE INDEX … USING lakebase_ann (…)` opclass; the exact `CREATE INDEX … USING lakebase_bm25 (…)` + the BM25 query SQL (`to_bm25query` usage, match operator, ranking expression); and the ANN query SQL (`<=>`).

- [ ] **Step 1: Verify a served FMAPI embedding model + its dimension**

List embedding endpoints: `databricks serving-endpoints list --profile DEFAULT -o json` → filter `task == "llm/v1/embeddings"`. Candidates to expect: `databricks-gte-large-en`, `databricks-bge-large-en` (both 1024-dim). Then confirm live via the gateway:
```bash
uv run python - <<'PY'
import requests
from databricks.sdk import WorkspaceClient
w = WorkspaceClient(profile="DEFAULT")
url = f"{w.config.host.rstrip('/')}/ai-gateway/openai/v1/embeddings"
for m in ("databricks-gte-large-en", "databricks-bge-large-en"):
    h = w.config.authenticate(); h["Content-Type"] = "application/json"
    r = requests.post(url, headers=h, json={"model": m, "input": "hello"}, timeout=60)
    if r.status_code == 200:
        print(m, "dim =", len(r.json()["data"][0]["embedding"]))
    else:
        print(m, r.status_code, r.text[:120])
PY
```
Record the chosen `EMBED_MODEL` + `EMBED_DIM` in the contract, "verified by <command>".

- [ ] **Step 2: Nail the exact Lakebase Search index + query DDL live (scratch table in `ug`, then drop it)**

```bash
DATABRICKS_CONFIG_PROFILE=DEFAULT LAKEBASE_ENDPOINT=projects/your-project/branches/production/endpoints/primary \
LAKEBASE_DATABASE=databricks_postgres UG_SCHEMA=ug uv run python - <<'PY'
import asyncio
from src.services.db import create_pool
async def main():
    pool = await create_pool()
    async with pool.connection() as c:
        await c.set_autocommit(True)
        async with c.cursor() as cur:
            await cur.execute("DROP TABLE IF EXISTS ug._probe")
            await cur.execute("CREATE TABLE ug._probe (id int, body text, tsv tsvector, emb vector(8))")
            await cur.execute("INSERT INTO ug._probe VALUES (1,'hello world',to_tsvector('hello world'),'[1,0,0,0,0,0,0,0]')")
            # Try the ANN + BM25 index builds and queries; print what works verbatim.
            for label, sql in [
                ("ann", "CREATE INDEX ON ug._probe USING lakebase_ann (emb vector_cosine_ops)"),
                ("bm25", "CREATE INDEX ON ug._probe USING lakebase_bm25 (tsv)"),
            ]:
                try:
                    await cur.execute(sql); print("OK", label, "::", sql)
                except Exception as e:
                    print("ERR", label, "::", str(e)[:200])
            # queries
            try:
                await cur.execute("SELECT id, 1-(emb <=> '[1,0,0,0,0,0,0,0]'::vector) s FROM ug._probe ORDER BY emb <=> '[1,0,0,0,0,0,0,0]'::vector LIMIT 3")
                print("ann query rows:", await cur.fetchall())
            except Exception as e: print("ERR ann query:", str(e)[:200])
            try:
                await cur.execute("SELECT id FROM ug._probe WHERE tsv @@ to_bm25query(to_tsvector('hello'), 'ug._probe'::regclass)")
                print("bm25 query rows:", await cur.fetchall())
            except Exception as e: print("ERR bm25 query:", str(e)[:200])
            await cur.execute("DROP TABLE ug._probe")
    await pool.close()
asyncio.run(main())
PY
```
Adjust opclass / query operator per what succeeds, and **record the exact working DDL + query SQL verbatim** in the contract (this is what Tasks 4 & 5 copy). If an opclass name differs (e.g. a `lakebase_ann`-specific opclass), record the real one. Drop the scratch table.

- [ ] **Step 3: Write `docs/discovery/embeddings-and-index-contract.md`**

Record: `EMBED_MODEL`, `EMBED_DIM`, the verbatim `CREATE INDEX` statements for ANN + BM25, the ANN query (`ORDER BY emb <=> $q::vector`), and the BM25 query (`WHERE tsv @@ to_bm25query(...)` + ranking). Each fact stamped "verified by <command> 2026-09-24".

- [ ] **Step 4: Commit**

```bash
git add docs/discovery/embeddings-and-index-contract.md
git commit -m "docs: verified FMAPI embedding model + Lakebase Search index/query DDL

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 3: Gateway helper + FMAPI embeddings client

**Files:**
- Create: `src/services/gateway.py`, `src/services/embeddings.py`
- Modify: `pyproject.toml` (add `requests`)
- Test: `tests/test_embeddings.py`

**Interfaces:**
- Consumes: Task 2's `EMBED_MODEL`/`EMBED_DIM` (env `UG_EMBED_MODEL`, default from contract).
- Produces: `gateway.post(path: str, body: dict) -> dict` (Unity Gateway auth + base_url + `requests`); `embeddings.embed_texts(texts: list[str]) -> list[list[float]]`, `embeddings.EMBED_DIM: int`, `embeddings.to_pgvector(vec: list[float]) -> str` (formats `'[...]'` for `::vector`).

- [ ] **Step 1: Write the failing tests** (mock the gateway — no network in unit tests)

`tests/test_embeddings.py`:
```python
import src.services.embeddings as emb


def test_embed_texts_calls_gateway_and_returns_vectors(monkeypatch):
    captured = {}
    def fake_post(path, body):
        captured["path"], captured["body"] = path, body
        return {"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in body["input"]]}
    monkeypatch.setattr(emb, "_post", fake_post)
    out = emb.embed_texts(["a", "b"])
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert captured["path"].endswith("/embeddings")
    assert captured["body"]["input"] == ["a", "b"]


def test_to_pgvector_formats_bracketed_literal():
    assert emb.to_pgvector([0.5, -1.0, 2.0]) == "[0.5,-1.0,2.0]"


def test_embed_texts_empty_returns_empty(monkeypatch):
    monkeypatch.setattr(emb, "_post", lambda p, b: {"data": []})
    assert emb.embed_texts([]) == []
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_embeddings.py -q`
Expected: FAIL — `ModuleNotFoundError: src.services.embeddings`.

- [ ] **Step 3: Add `requests` to `pyproject.toml` dependencies, then implement**

Add `"requests==2.34.2"` to `[project].dependencies`; run `uv sync`.

`src/services/gateway.py`:
```python
"""Thin Unity Gateway (OpenAI-compatible) client — auth + base_url + POST.

Used off the hot path (generation, embeddings). Auth via WorkspaceClient so it
works with DATABRICKS_CONFIG_PROFILE locally and Apps-injected creds in prod.
"""
from __future__ import annotations

import requests

_BASE = "/ai-gateway/openai/v1"
_w = None


def _client():
    global _w
    if _w is None:
        from databricks.sdk import WorkspaceClient
        _w = WorkspaceClient()
    return _w


def post(path: str, body: dict, timeout: float = 60.0) -> dict:
    w = _client()
    url = f"{w.config.host.rstrip('/')}{_BASE}{path}"
    headers = w.config.authenticate()
    headers["Content-Type"] = "application/json"
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
```

`src/services/embeddings.py`:
```python
"""FMAPI embeddings over Unity Gateway (bring-your-own embeddings for lakebase_ann).

EMBED_MODEL / EMBED_DIM come from docs/discovery/embeddings-and-index-contract.md.
"""
from __future__ import annotations

import os

from src.services.gateway import post as _post

EMBED_MODEL = os.getenv("UG_EMBED_MODEL", "databricks-gte-large-en")
EMBED_DIM = int(os.getenv("UG_EMBED_DIM", "1024"))  # confirm vs Task 2 contract


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    resp = _post("/embeddings", {"model": EMBED_MODEL, "input": texts})
    return [d["embedding"] for d in resp["data"]]


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"
```
> Set `UG_EMBED_MODEL` / `UG_EMBED_DIM` from Task 2's contract if they differ from the defaults above.

- [ ] **Step 4: Run unit tests, then a live dim smoke**

Run: `uv run pytest tests/test_embeddings.py -q` → Expected: PASS.
Live smoke: `DATABRICKS_CONFIG_PROFILE=DEFAULT uv run python -c "from src.services.embeddings import embed_texts, EMBED_DIM; v=embed_texts(['hi'])[0]; print('dim', len(v), 'expected', EMBED_DIM); assert len(v)==EMBED_DIM"` → Expected: dim matches.

- [ ] **Step 5: Commit**

```bash
git add src/services/gateway.py src/services/embeddings.py tests/test_embeddings.py pyproject.toml uv.lock
git commit -m "feat: Unity Gateway helper + FMAPI embeddings client

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 4: Schema v2 — embedding + tsvector columns and Lakebase Search indexes

**Files:**
- Modify: `infra/lakebase_schema.sql`
- Create: `infra/lakebase_indexes.sql`
- Modify: `infra/apply_schema.py` (also apply extensions + indexes)
- Test: `tests/test_schema_sql.py` (extend)

**Interfaces:**
- Consumes: Task 2's `EMBED_DIM` + verbatim index DDL; Task 3's `EMBED_DIM`.
- Produces: `documents` with `embedding vector(<EMBED_DIM>)` + `content_tsv tsvector`; the `lakebase_ann` + `lakebase_bm25` indexes; extensions ensured.

- [ ] **Step 1: Write the failing test** (extend static SQL checks)

Add to `tests/test_schema_sql.py`:
```python
from pathlib import Path
IDX = Path("infra/lakebase_indexes.sql").read_text().lower()

def test_documents_has_embedding_and_tsv_columns():
    assert "embedding vector(" in SQL and "content_tsv tsvector" in SQL

def test_indexes_use_lakebase_search_access_methods():
    assert "using lakebase_ann" in IDX and "using lakebase_bm25" in IDX
    assert "create extension if not exists lakebase_vector" in IDX
    assert "create extension if not exists lakebase_text" in IDX
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_schema_sql.py -q`
Expected: FAIL — `infra/lakebase_indexes.sql` missing / embedding column absent.

- [ ] **Step 3: Add the columns to `infra/lakebase_schema.sql`**

In the `documents` table, add (dim from Task 2's contract):
```sql
    embedding          vector(1024),
    content_tsv        tsvector,
```
(place before the `PRIMARY KEY (data_generation_id, doc_id)` line.)

- [ ] **Step 4: Create `infra/lakebase_indexes.sql`** (extensions + indexes; copy the verbatim DDL Task 2 verified)

```sql
CREATE EXTENSION IF NOT EXISTS lakebase_tokenizer CASCADE;
CREATE EXTENSION IF NOT EXISTS lakebase_vector CASCADE;
CREATE EXTENSION IF NOT EXISTS lakebase_text CASCADE;

-- ANN (semantic) over document embeddings; cosine. Opclass per Task 2 contract.
CREATE INDEX IF NOT EXISTS documents_ann
    ON {schema}.documents USING lakebase_ann (embedding vector_cosine_ops);

-- BM25 (keyword) over the tsvector. Column/opclass per Task 2 contract.
CREATE INDEX IF NOT EXISTS documents_bm25
    ON {schema}.documents USING lakebase_bm25 (content_tsv);
```
> Replace the opclass / column spelling with exactly what Task 2's `embeddings-and-index-contract.md` recorded as working.

- [ ] **Step 5: Update `infra/apply_schema.py` to also apply the indexes**

After applying `lakebase_schema.sql`, also read + `{schema}`-substitute + execute `infra/lakebase_indexes.sql` (same connection). Add a line printing `[indexes] applied`.

- [ ] **Step 6: Run the static test, then apply live**

Run: `uv run pytest tests/test_schema_sql.py -q` → Expected: PASS.
Apply: `DATABRICKS_CONFIG_PROFILE=DEFAULT LAKEBASE_ENDPOINT=projects/your-project/branches/production/endpoints/primary LAKEBASE_DATABASE=databricks_postgres UG_SCHEMA=ug uv run python infra/apply_schema.py` → Expected: schema + indexes applied; verify with a `\d ug.documents`-style check that the columns + indexes exist.

- [ ] **Step 7: Commit**

```bash
git add infra/lakebase_schema.sql infra/lakebase_indexes.sql infra/apply_schema.py tests/test_schema_sql.py
git commit -m "feat: documents embedding+tsv columns and Lakebase Search indexes

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 5: Retrieval service (ANN + BM25 + record_lookup, scoped)

**Files:**
- Create: `src/services/retrieval.py`
- Test: `tests/test_retrieval.py`

**Interfaces:**
- Consumes: `db._run_query`, `embeddings.to_pgvector`, Task 2's ANN/BM25 query SQL.
- Produces: `async semantic_search(pool, data_generation_id, query_embedding: list[float], k=5) -> list[dict]`; `async keyword_search(pool, data_generation_id, query: str, k=5) -> list[dict]`; `async record_lookup(pool, data_generation_id, customer_id=None, kind=None) -> list[dict]`. Every function filters `WHERE data_generation_id = %(gid)s`. (Embedding the query is the caller's job — keeps retrieval pure-SQL.)

- [ ] **Step 1: Write the failing tests** (fake pool asserts scoping + SQL shape)

`tests/test_retrieval.py`:
```python
import src.services.retrieval as r


class _Pool:
    def __init__(self): self.calls = []
    # retrieval calls db._run_query(pool, sql, params); we monkeypatch that instead.


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
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_retrieval.py -q`
Expected: FAIL — `ModuleNotFoundError: src.services.retrieval`.

- [ ] **Step 3: Implement `src/services/retrieval.py`** (SQL per Task 2's verified DDL)

```python
"""Read-only retrieval over the ug schema, always scoped by data_generation_id
(spec §12, §13). Hybrid Lakebase Search: ANN (lakebase_ann, cosine <=>) + BM25
(lakebase_bm25, to_bm25query). record_lookup is plain SQL. Never returns the
loyalty tier. Query embedding is supplied by the caller (embeddings.embed_texts)."""
from __future__ import annotations

from src.services.db import SCHEMA, _run_query
from src.services.embeddings import to_pgvector


async def semantic_search(pool, data_generation_id, query_embedding, k: int = 5) -> list[dict]:
    sql = (
        f"SELECT doc_id, title, chunk_text, 1 - (embedding <=> %(q)s::vector) AS score "
        f"FROM {SCHEMA}.documents "
        "WHERE data_generation_id = %(gid)s AND embedding IS NOT NULL "
        "ORDER BY embedding <=> %(q)s::vector LIMIT %(k)s"
    )
    return await _run_query(pool, sql, {
        "q": to_pgvector(query_embedding), "gid": data_generation_id, "k": k})


async def keyword_search(pool, data_generation_id, query: str, k: int = 5) -> list[dict]:
    # BM25 via lakebase_bm25 over content_tsv; query per Task 2 contract.
    sql = (
        f"SELECT doc_id, title, chunk_text FROM {SCHEMA}.documents "
        "WHERE data_generation_id = %(gid)s "
        "AND content_tsv @@ to_bm25query(to_tsvector(%(q)s), "
        f"'{SCHEMA}.documents'::regclass) LIMIT %(k)s"
    )
    return await _run_query(pool, sql, {"q": query, "gid": data_generation_id, "k": k})


async def record_lookup(pool, data_generation_id, customer_id=None, kind=None) -> list[dict]:
    clauses = ["data_generation_id = %(gid)s"]
    params: dict = {"gid": data_generation_id}
    if customer_id is not None:
        clauses.append("customer_id = %(cid)s"); params["cid"] = customer_id
    if kind is not None:
        clauses.append("kind = %(kind)s"); params["kind"] = kind
    sql = (f"SELECT record_id, customer_id, kind, fields, status FROM {SCHEMA}.records "
           f"WHERE {' AND '.join(clauses)} LIMIT 25")
    return await _run_query(pool, sql, params)
```
> If Task 2 recorded a different BM25 query form (operator/ranking) or ANN opclass, adjust the two search SQL strings to match verbatim.

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/test_retrieval.py -q`
Expected: PASS (scoping + SQL-shape + empty-miss assertions).

- [ ] **Step 5: Commit**

```bash
git add src/services/retrieval.py tests/test_retrieval.py
git commit -m "feat: scoped hybrid retrieval (ANN + BM25 + record_lookup)

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 6: Unity Gateway generation client + dataset generator

**Files:**
- Create: `src/services/uaig_chat.py`, `generate.py`
- Test: `tests/test_generate.py`

**Interfaces:**
- Consumes: `gateway.post`, `embeddings.embed_texts`/`to_pgvector`, `db.create_pool`/`SCHEMA`/`_run_query`, `tiers.VALID_TIERS`.
- Produces: `uaig_chat.complete_json(system, user, model) -> dict`; `generate.build_rows(spec, llm_json) -> Rows` (pure: turns an LLM draft into `documents`/`customers`/`records` rows tagged with a `data_generation_id`, customers spanning all tiers); `generate.generate_dataset(company, role, system_prompt, *, model, pool) -> str` (writes rows in one transaction, embeds docs, refreshes indexes, registers the dataset, returns `data_generation_id`). CLI: `uv run python generate.py "<company>" --role "<role>" --prompt "<system prompt>"`.

- [ ] **Step 1: Write the failing tests** (pure row-building + tier coverage; mock LLM, no network)

`tests/test_generate.py`:
```python
import json
from src.generate import build_rows, GenSpec

_LLM = {
    "documents": [
        {"title": "Returns policy", "chunk_text": "Items can be returned within 30 days."},
        {"title": "Shipping", "chunk_text": "Standard shipping is 3-5 days."},
    ],
    "customers": [
        {"display_name": "Ada", "loyalty_tier": "Standard"},
        {"display_name": "Grace", "loyalty_tier": "Premium"},
        {"display_name": "Kat", "loyalty_tier": "VIP"},
    ],
    "records": [{"customer_index": 2, "kind": "order", "fields": {"item": "widget"}, "status": "shipped"}],
}


def test_build_rows_tags_everything_with_one_generation_id():
    spec = GenSpec(company="Acme", role="support", system_prompt="help", data_generation_id="G1")
    rows = build_rows(spec, _LLM)
    ids = {r["data_generation_id"] for r in rows.documents + rows.customers + rows.records}
    assert ids == {"G1"}


def test_build_rows_covers_all_three_tiers():
    spec = GenSpec("Acme", "support", "help", "G1")
    tiers = {c["loyalty_tier"] for c in build_rows(spec, _LLM).customers}
    assert tiers == {"Standard", "Premium", "VIP"}


def test_build_rows_marks_documents_synthetic():
    spec = GenSpec("Acme", "support", "help", "G1")
    docs = build_rows(spec, _LLM).documents
    assert all(json.loads(d["metadata"])["synthetic"] is True for d in docs)


def test_build_rows_rejects_missing_tier_coverage():
    import pytest
    bad = {**_LLM, "customers": [{"display_name": "X", "loyalty_tier": "Standard"}]}
    with pytest.raises(ValueError):
        build_rows(GenSpec("Acme", "support", "help", "G1"), bad)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_generate.py -q`
Expected: FAIL — `ModuleNotFoundError: src.generate` (module at repo root `generate.py`; test imports `from src.generate` — see Step 3 note).

- [ ] **Step 3: Implement `generate.py` + `src/services/uaig_chat.py`**

> Import note: to keep the tested pure logic importable as `src.generate`, put `build_rows`/`GenSpec` in `src/generate.py` and make the repo-root `generate.py` a thin CLI that imports from it (mirrors Plan 1's apply_schema `sys.path` handling). Alternatively keep everything in `src/generate.py` and a 3-line root `generate.py` runner. Pick one; the test imports `src.generate`.

`src/services/uaig_chat.py`:
```python
"""Unity Gateway chat completion returning parsed JSON — for the generator (off hot path)."""
from __future__ import annotations

import json

from src.services.gateway import post as _post


def complete_json(system: str, user: str, model: str) -> dict:
    resp = _post("/chat/completions", {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
    })
    return json.loads(resp["choices"][0]["message"]["content"])
```

`src/generate.py` (pure row-building + orchestration):
```python
"""Synthetic dataset generator (spec §9). Dogfoods Unity Gateway (chat) + FMAPI (embeddings).
The generator is the only writer in v1; the agent's tools stay read-only."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from src.policy.tiers import VALID_TIERS
from src.services.embeddings import embed_texts, to_pgvector
from src.services.uaig_chat import complete_json
from src.services.db import SCHEMA, _run_query

_SYNTH = json.dumps({"synthetic": True})


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
         "attributes": json.dumps({"synthetic": True})}
        for i, c in enumerate(llm["customers"])
    ]
    if {c["loyalty_tier"] for c in customers} != set(VALID_TIERS):
        raise ValueError(f"generated customers must cover all tiers {VALID_TIERS}")
    documents = [
        {"data_generation_id": gid, "doc_id": f"{gid}-D{i}",
         "title": d["title"], "chunk_text": d["chunk_text"], "metadata": _SYNTH}
        for i, d in enumerate(llm["documents"])
    ]
    records = [
        {"data_generation_id": gid, "record_id": f"{gid}-R{i}",
         "customer_id": customers[rec["customer_index"]]["customer_id"],
         "kind": rec.get("kind"), "fields": json.dumps(rec.get("fields", {})),
         "status": rec.get("status")}
        for i, rec in enumerate(llm.get("records", []))
    ]
    return Rows(documents=documents, customers=customers, records=records)
```
Also implement (below `build_rows`, exercised by the live smoke in Step 5, not the unit tests):
- a `_GEN_SYSTEM`/`_GEN_USER` prompt pair asking for `{"documents":[…], "customers":[… all three tiers …], "records":[…]}` JSON for `{company, role}`;
- `async generate_dataset(company, role, system_prompt, *, model, pool) -> str` that: mints `gid=uuid4().hex`; inserts a `datasets` row `status='pending'`; `complete_json` → `build_rows`; embeds `chunk_text` via `embed_texts` and sets each document's `embedding` (`to_pgvector`) + `content_tsv=to_tsvector(chunk_text)`; INSERTs documents/customers/records **in one transaction**; updates the `datasets` row to `status='ready'` with counts; on any exception sets `status='failed'` and re-raises. Re-running an existing `gid` is avoided (fresh uuid each run); a failed run leaves `status='failed'`, never partial "ready".
- a root `generate.py` CLI (argparse: company positional, `--role`, `--prompt`, `--model` default `UG_MODEL_PREMIUM`/contract) that opens `create_pool()` and calls `generate_dataset`, printing the `data_generation_id`.

- [ ] **Step 4: Run unit tests (pure row logic)**

Run: `uv run pytest tests/test_generate.py -q`
Expected: PASS (tagging, tier coverage, synthetic marker, missing-tier rejection).

- [ ] **Step 5: Live smoke — generate one dataset**

Run: `DATABRICKS_CONFIG_PROFILE=DEFAULT LAKEBASE_ENDPOINT=projects/your-project/branches/production/endpoints/primary LAKEBASE_DATABASE=databricks_postgres UG_SCHEMA=ug uv run python generate.py "Northwind Outfitters" --role "customer support" --prompt "Answer questions about orders, returns, and shipping."`
Expected: prints a `data_generation_id`; verify (SQL) that `datasets.status='ready'`, `documents` rows have non-null `embedding` + `content_tsv`, and `customers` span all three tiers. Keep the id for Task 7.

- [ ] **Step 6: Commit**

```bash
git add src/services/uaig_chat.py src/generate.py generate.py tests/test_generate.py
git commit -m "feat: Unity Gateway-powered synthetic dataset generator

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 7: End-to-end integration — generate then retrieve

**Files:**
- Test: `tests/test_integration_data_plane.py` (marked live; skipped without Lakebase env)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write a live integration test guarded by env**

`tests/test_integration_data_plane.py`:
```python
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
            model=os.environ["UG_MODEL_PREMIUM"], pool=pool)
        qvec = embed_texts(["how do returns work?"])[0]
        sem = await retrieval.semantic_search(pool, gid, qvec, k=3)
        assert sem and all("data_generation_id" not in row for row in sem)  # tier/gid not leaked in payload
        kw = await retrieval.keyword_search(pool, gid, "returns", k=3)
        # isolation: a different (nonexistent) gid returns nothing
        assert await retrieval.semantic_search(pool, "does-not-exist", qvec, k=3) == []
    finally:
        await pool.close()
```

- [ ] **Step 2: Run it live**

Run: `DATABRICKS_CONFIG_PROFILE=DEFAULT LAKEBASE_ENDPOINT=projects/your-project/branches/production/endpoints/primary LAKEBASE_DATABASE=databricks_postgres UG_SCHEMA=ug UG_MODEL_PREMIUM=databricks-gpt-5-5 uv run pytest tests/test_integration_data_plane.py -q -v`
Expected: PASS — semantic + keyword return on-topic rows; cross-`gid` query returns empty (isolation).

- [ ] **Step 3: Confirm the unit suite is still green without live env**

Run: `uv run pytest -q` (no Lakebase env) → Expected: PASS with the integration test **skipped**.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration_data_plane.py
git commit -m "test: end-to-end data-plane integration (generate -> retrieve, scoped)

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

## Self-Review

**1. Spec coverage:** generator → §9 (Task 6); generic-schema retrieval → §8/§13 (Tasks 4–5); Lakebase Search hybrid → §4/§7 + lakebase-search-contract (Tasks 2,4,5); embeddings via Unity Gateway → §9 (Task 3); tier fixes → §11 deferred (Task 1); scoping/no-fabrication → §12 (Tasks 5–7). Agent/tracing/UI/deploy remain Plans 3–4.

**2. Placeholder scan:** No "TODO/handle X." The three "per Task 2 contract" notes (ANN opclass, BM25 query form, EMBED_DIM) are explicit cross-task dependencies on a discovery task that records verbatim DDL — not vague placeholders; the unit-tested surfaces (row-building, SQL shape, scoping, embeddings mock) are complete.

**3. Type consistency:** `GenSpec`/`Rows`/`build_rows`/`generate_dataset` (Task 6) match the test's import (`src.generate`); `embed_texts`/`to_pgvector`/`EMBED_DIM` (Task 3) match Tasks 5–6 usage; `gateway.post` (Task 3) matches `uaig_chat`/`embeddings`; `retrieval.*` signatures match Task 7; `VALID_TIERS`/`THOROUGHNESS` (Task 1) match routing/loyalty imports; `documents.embedding`/`content_tsv` (Task 4) match `retrieval` SQL and generator writes.

**4. Review Focus:** scoping (Task 5 tests assert `data_generation_id = %(gid)s` on every path + Task 7 cross-gid isolation); retrieval miss → empty (Task 5 keyword test, Task 7); all-tier coverage (Task 6 tests); synthetic labeling (Task 6 test); embedding-dim match (Task 3 live smoke asserts `len==EMBED_DIM`; Task 4 column dim); transactional generator (Task 6 Step 3 one-transaction + `failed` status). Each has an owning task + test.

---

*Plan 2 delivers a demonstrable data plane — generate a company dataset and retrieve from it with real Lakebase Search hybrid search — and clears the two Plan 1 deferrals that bite here. Plan 3 (voice agent + routing wiring + tracing) consumes `retrieval.*`, `embeddings.*`, `route_for`, and `read_loyalty_context`.*
