# UG Voice Studio — Plan 1: Foundations & Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the repo, verify every unproven external dependency against the chosen workspace (writing contract docs), and build the two pure-logic foundations — loyalty→model routing and the governed loyalty reader — that the rest of the studio consumes.

**Architecture:** A Databricks-App voice-studio, generalizing the ReferenceApp governed skeleton. This plan builds only the discovery contracts + the pure/groundable core (routing policy, DB helpers, loyalty reader, Lakebase schema). No voice, no UI, no generator yet — those are Plans 2–4, written once this plan's discovery lands.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `psycopg[binary]` + `psycopg-pool`, `databricks-sdk`, Databricks CLI, Lakebase (Postgres), Unity AI Gateway.

**Spec:** `docs/superpowers/specs/2026-09-24-unity-gateway-voice-studio-design.md` (read it alongside this plan; the plan argues from it).

## Global Constraints

- Run everything with **`uv`** (`uv run …`, `uv pip …`, `uv venv`).
- Databricks **profile is user-chosen; never auto-selected** — pass `--profile <name>` on every CLI call.
- Route Databricks CLI work through the skills: load **`databricks-core`** first, then the matching product skill (`databricks-lakebase`, `databricks-model-serving`, `databricks-python-sdk`).
- Commit as **`datasciencemonkey <datasciencemonkey@gmail.com>`**; end every commit message with `Co-authored-by: Isaac <no-reply@databricks.com>`.
- Python **3.12**.
- **Loyalty tiers are exactly:** `Standard`, `Premium`, `VIP` (safe default `Standard`).
- **Everything the agent reads on the hot path lives in Lakebase**; OTel spans land in Unity Catalog.
- **Read-only v1** — no write tools/actions anywhere.
- All generated data is **clearly labeled synthetic** (later plans).
- **Governance invariants (spec §12):** the routing decision is deterministic and outside the LLM; the LLM never sees the raw tier; the caller name is courtesy-only; the tier comes from a governed lookup, never conversation; stale/missing → safe default, never fabricate; every data query is scoped by `data_generation_id`.
- LLM path (later plans) is UAIG **Responses API** (`{host}/ai-gateway/openai/v1`, `use_websocket=False`, `store=False`) — never Chat Completions.

## Review Focus

Spec-implied behaviors this plan's code must pin (each has a test in its owning task; cross-plan ones are noted for later plans):

- **Unknown / unmatched caller → `Standard` + safe default** — owner: Task 4 (`read_loyalty_context`) and Task 2 (`route_for`).
- **Stale / missing / DB-error loyalty → `Standard`, `stale=True`, never raises** — owner: Task 4.
- **Invalid tier value stored in the DB → coerced to `Standard`** — owner: Tasks 2 and 4.
- **Multi-tenant isolation: every customer read is scoped by `data_generation_id`** — owner: Task 4 (query shape) and Task 6 (schema PKs).
- **Routing directives never leak the raw tier / loyalty signal** — owner: Task 2.
- *(Cross-plan, noted)* Stated status must not change treatment (agent prompt) → Plan 3; retrieval miss → abstain → Plan 2; degraded no-Lakebase mode → Plans 2–3.

---

### Task 1: Bootstrap the repo

**Files:**
- Create: `pyproject.toml`, `.python-version`, `conftest.py`, `src/__init__.py`, `tests/__init__.py`, `README.md`
- Exists: `.gitignore` (from the spec commit)

**Interfaces:**
- Consumes: nothing.
- Produces: a `uv`-managed env where `uv run pytest` executes; the `src/` package root.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "ug-voice-studio"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "psycopg[binary]==3.3.4",
    "psycopg-pool==3.3.1",
    "databricks-sdk==0.133.0",
    "python-dotenv==1.2.2",
]

[dependency-groups]
dev = ["pytest==8.3.4", "pytest-asyncio==0.25.2"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create `.python-version`, package markers, and `conftest.py`**

`.python-version`:
```
3.12
```

`src/__init__.py` and `tests/__init__.py`: empty files.

`conftest.py` (repo root — makes `src` importable in tests):
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
```

- [ ] **Step 3: Create the env and a smoke test**

`tests/test_smoke.py`:
```python
def test_python_and_imports():
    import psycopg  # noqa: F401
    import databricks.sdk  # noqa: F401
    assert True
```

- [ ] **Step 4: Run it**

Run: `uv sync && uv run pytest tests/test_smoke.py -v`
Expected: PASS (deps resolve, imports succeed).

- [ ] **Step 5: Confirm the target Databricks profile WITH the user (never auto-select)**

Ask the user which profile to build against (spec §16 requires a **Lakebase-capable** workspace). Record it in `README.md` under a `## Build target` heading: profile name + "Lakebase-capable: to be confirmed in Task 5." Do **not** run any workspace call yet.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .python-version conftest.py src tests README.md uv.lock
git commit -m "chore: bootstrap ug-voice-studio repo and test harness

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 2: Routing policy (pure, deterministic)

**Files:**
- Create: `src/policy/__init__.py`, `src/policy/routing.py`
- Test: `tests/test_routing.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RoutingDecision(tier: str, model: str, directives: dict)` and `route_for(loyalty_tier: str | None) -> RoutingDecision`. Model ids read from env `UG_MODEL_STANDARD|PREMIUM|VIP` + `UG_MODEL_FALLBACK` (set from Task 5's contract at deploy; read at call time so tests monkeypatch them). `VALID_TIERS = ("Standard", "Premium", "VIP")`.

- [ ] **Step 1: Write the failing tests**

`tests/test_routing.py`:
```python
import pytest
from src.policy.routing import RoutingDecision, route_for, VALID_TIERS


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setenv("UG_MODEL_STANDARD", "model-standard")
    monkeypatch.setenv("UG_MODEL_PREMIUM", "model-premium")
    monkeypatch.setenv("UG_MODEL_VIP", "model-vip")
    monkeypatch.setenv("UG_MODEL_FALLBACK", "model-fallback")


@pytest.mark.parametrize("tier,model", [
    ("Standard", "model-standard"),
    ("Premium", "model-premium"),
    ("VIP", "model-vip"),
])
def test_each_tier_routes_to_its_model(tier, model):
    d = route_for(tier)
    assert isinstance(d, RoutingDecision)
    assert d.tier == tier and d.model == model


@pytest.mark.parametrize("bad", [None, "", "Gold", "vip", "UNKNOWN"])
def test_unknown_tier_falls_back_to_standard(bad):
    d = route_for(bad)
    assert d.tier == "Standard" and d.model == "model-standard"


def test_missing_model_env_uses_fallback(monkeypatch):
    monkeypatch.delenv("UG_MODEL_VIP", raising=False)
    assert route_for("VIP").model == "model-fallback"


def test_directives_never_leak_the_raw_tier():
    d = route_for("VIP")
    blob = repr(d.directives).lower()
    assert "vip" not in blob and "tier" not in blob and "loyalty" not in blob


def test_warm_recognition_only_for_premium_and_vip():
    assert route_for("Standard").directives["recognition_tone"] == "neutral"
    assert route_for("Premium").directives["recognition_tone"] == "warm"
    assert route_for("VIP").directives["recognition_tone"] == "warm"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routing.py -v`
Expected: FAIL with `ModuleNotFoundError: src.policy.routing`.

- [ ] **Step 3: Implement `src/policy/routing.py`**

```python
"""Deterministic loyalty->model routing (spec §11). Pure, no I/O, outside the LLM.

v1 is the StaticTierStrategy: tier -> model from env, plus LLM-safe behavior
directives. A future served "decision model" (spec Future extensions) drops in
behind route_for() with the same RoutingDecision contract.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

VALID_TIERS = ("Standard", "Premium", "VIP")

_ENV_BY_TIER = {
    "Standard": "UG_MODEL_STANDARD",
    "Premium": "UG_MODEL_PREMIUM",
    "VIP": "UG_MODEL_VIP",
}

_THOROUGHNESS = {"Standard": "concise", "Premium": "balanced", "VIP": "thorough"}


@dataclass(frozen=True)
class RoutingDecision:
    tier: str          # governance signal — used to build directives, never sent to the LLM
    model: str         # UAIG-served conversational model for this session
    directives: dict   # LLM-safe behavior flags only (no tier/loyalty/spend)


def _directives_for(tier: str) -> dict:
    warm = tier in ("Premium", "VIP")
    return {
        "recognition_tone": "warm" if warm else "neutral",
        "be_proactive": tier == "VIP",
        "thoroughness": _THOROUGHNESS[tier],
        "offer_human_escalation": tier == "VIP",
    }


def route_for(loyalty_tier: str | None) -> RoutingDecision:
    tier = loyalty_tier if loyalty_tier in VALID_TIERS else "Standard"
    model = os.getenv(_ENV_BY_TIER[tier], "") or os.getenv("UG_MODEL_FALLBACK", "")
    return RoutingDecision(tier=tier, model=model, directives=_directives_for(tier))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_routing.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add src/policy tests/test_routing.py
git commit -m "feat: deterministic loyalty->model routing policy

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 3: Lakebase DB helpers (pool + query runner)

**Files:**
- Create: `src/services/__init__.py`, `src/services/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing (unit tests use a fake pool).
- Produces: `async create_pool() -> AsyncConnectionPool` (Lakebase auth via `WorkspaceClient`; live path exercised in Task 6 / Plan 2) and `async _run_query(pool, sql, params=None) -> list[dict]` (3s timeout + 1 retry, raises `QueryError` on failure). `SCHEMA` module constant (default from `UG_SCHEMA` env, else `"ug"`).

- [ ] **Step 1: Write the failing tests** (fake pool — no live Lakebase needed)

`tests/test_db.py`:
```python
import pytest
from src.services.db import _run_query, QueryError


class _FakeCursor:
    def __init__(self, rows, cols, boom=False):
        self._rows, self._cols, self._boom = rows, cols, boom
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def execute(self, sql, params=None):
        if self._boom:
            raise RuntimeError("connection reset")
    @property
    def description(self):
        return [(c,) for c in self._cols]
    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, cursor): self._cursor = cursor
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def cursor(self): return self._cursor


class _FakePool:
    def __init__(self, cursor): self._cursor = cursor
    def connection(self): return _FakeConn(self._cursor)


async def test_run_query_maps_rows_to_dicts():
    pool = _FakePool(_FakeCursor([(1, "VIP")], ["customer_id", "loyalty_tier"]))
    rows = await _run_query(pool, "SELECT 1")
    assert rows == [{"customer_id": 1, "loyalty_tier": "VIP"}]


async def test_run_query_raises_queryerror_after_retry():
    pool = _FakePool(_FakeCursor([], [], boom=True))
    with pytest.raises(QueryError):
        await _run_query(pool, "SELECT 1")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: src.services.db`.

- [ ] **Step 3: Implement `src/services/db.py`** (pool/auth pattern adapted from ReferenceApp `src/services/recovery.py::create_recovery_pool`, `_run_query`)

```python
"""Lakebase (Postgres) connection pool + query runner.

Pool auth mirrors ReferenceApp src/services/recovery.py: WorkspaceClient postgres
endpoint + a generated 1h credential + current user, sslmode=require. The live
pool is exercised in Task 6 / Plan 2; _run_query is unit-tested with a fake pool.
"""
from __future__ import annotations

import asyncio
import os

SCHEMA = os.getenv("UG_SCHEMA", "ug")

_QUERY_TIMEOUT_S = 3.0
_RETRIES = 1


class QueryError(RuntimeError):
    """Raised when a Lakebase query fails after its retry."""


async def create_pool():
    """Warm AsyncConnectionPool over the UG schema. Fail-soft callers pass the
    result (or None) down; see spec §17 degraded mode."""
    from databricks.sdk import WorkspaceClient
    from psycopg_pool import AsyncConnectionPool

    w = WorkspaceClient()
    endpoint = os.environ["LAKEBASE_ENDPOINT"]
    database = os.getenv("LAKEBASE_DATABASE", "ug")
    ep = w.postgres.get_endpoint(endpoint)
    cred = w.postgres.generate_database_credential(request_id=os.urandom(8).hex(), instance_names=[endpoint])
    user = w.current_user.me().user_name
    conninfo = (
        f"host={ep.read_write_dns} port=5432 dbname={database} "
        f"user={user} password={cred.token} sslmode=require"
    )
    pool = AsyncConnectionPool(conninfo, min_size=1, max_size=4, open=False)
    await pool.open(timeout=15)
    return pool


async def _run_query(pool, sql: str, params: dict | None = None) -> list[dict]:
    last: Exception | None = None
    for _ in range(_RETRIES + 1):
        try:
            async def _go():
                async with pool.connection() as conn:
                    cur = conn.cursor()
                    async with cur:
                        await cur.execute(sql, params)
                        cols = [d[0] for d in cur.description]
                        rows = await cur.fetchall()
                        return [dict(zip(cols, r)) for r in rows]
            return await asyncio.wait_for(_go(), timeout=_QUERY_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise QueryError(str(last))
```

> **Note for the implementer:** `create_pool()`'s exact `WorkspaceClient().postgres.*` calls are pinned by Task 5's `lakebase-search-contract.md`; if the discovered SDK surface differs, correct the three auth lines here and re-run Plan 2's integration test. `_run_query` (the unit-tested part) is unaffected.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/__init__.py src/services/db.py tests/test_db.py
git commit -m "feat: Lakebase pool + query runner

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 4: Governed loyalty context reader

**Files:**
- Create: `src/services/loyalty_context.py`
- Test: `tests/test_loyalty_context.py`

**Interfaces:**
- Consumes: `src.services.db.SCHEMA`, and a `_run_query`-shaped callable (monkeypatched in tests).
- Produces: `LoyaltyContext(customer_id, data_generation_id, loyalty_tier, display_name, stale)` and `async read_loyalty_context(pool, customer_id, data_generation_id) -> LoyaltyContext` — **never raises**; scoped by `data_generation_id`; safe default `Standard`/`stale=True`.

- [ ] **Step 1: Write the failing tests**

`tests/test_loyalty_context.py`:
```python
import pytest
import src.services.loyalty_context as lc
from src.services.loyalty_context import read_loyalty_context, LoyaltyContext


class _Pool:  # truthy sentinel so the None-guard doesn't short-circuit
    pass


async def test_pool_none_returns_standard_default():
    ctx = await read_loyalty_context(None, "C1", "G1")
    assert ctx.loyalty_tier == "Standard" and ctx.stale is True


async def test_missing_customer_returns_default(monkeypatch):
    async def fake(pool, sql, params=None): return []
    monkeypatch.setattr(lc, "_run_query", fake)
    ctx = await read_loyalty_context(_Pool(), "NOPE", "G1")
    assert ctx.loyalty_tier == "Standard" and ctx.stale is True


async def test_valid_row_returns_tier_and_name(monkeypatch):
    async def fake(pool, sql, params=None):
        return [{"loyalty_tier": "VIP", "display_name": "Sam"}]
    monkeypatch.setattr(lc, "_run_query", fake)
    ctx = await read_loyalty_context(_Pool(), "C1", "G1")
    assert ctx == LoyaltyContext("C1", "G1", "VIP", "Sam", False)


async def test_invalid_tier_value_coerced_to_standard(monkeypatch):
    async def fake(pool, sql, params=None):
        return [{"loyalty_tier": "Platinum", "display_name": None}]
    monkeypatch.setattr(lc, "_run_query", fake)
    ctx = await read_loyalty_context(_Pool(), "C1", "G1")
    assert ctx.loyalty_tier == "Standard" and ctx.stale is False


async def test_never_raises_on_query_error(monkeypatch):
    async def boom(pool, sql, params=None): raise RuntimeError("db down")
    monkeypatch.setattr(lc, "_run_query", boom)
    ctx = await read_loyalty_context(_Pool(), "C1", "G1")
    assert ctx.loyalty_tier == "Standard" and ctx.stale is True


async def test_query_is_scoped_by_data_generation_id(monkeypatch):
    seen = {}
    async def fake(pool, sql, params=None):
        seen["sql"], seen["params"] = sql, params
        return [{"loyalty_tier": "Premium", "display_name": "A"}]
    monkeypatch.setattr(lc, "_run_query", fake)
    await read_loyalty_context(_Pool(), "C1", "G1")
    assert "data_generation_id" in seen["sql"]
    assert seen["params"]["gid"] == "G1" and seen["params"]["cid"] == "C1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_loyalty_context.py -v`
Expected: FAIL with `ModuleNotFoundError: src.services.loyalty_context`.

- [ ] **Step 3: Implement `src/services/loyalty_context.py`** (contract mirrors ReferenceApp `src/services/value_context.py`: never raises, safe default)

```python
"""Governed loyalty lookup, bound once at session start (spec §10, §12).

NEVER raises: missing / unknown / stale / error -> Standard, stale=True.
Always scoped by data_generation_id (multi-tenant isolation). The tier is a
governance signal used to route + build directives, never surfaced to the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.services.db import SCHEMA, _run_query

VALID_TIERS = ("Standard", "Premium", "VIP")


@dataclass(frozen=True)
class LoyaltyContext:
    customer_id: str | None
    data_generation_id: str | None
    loyalty_tier: str
    display_name: str | None
    stale: bool


def _default(customer_id, data_generation_id) -> LoyaltyContext:
    return LoyaltyContext(customer_id, data_generation_id, "Standard", None, True)


async def read_loyalty_context(pool, customer_id, data_generation_id) -> LoyaltyContext:
    if pool is None or not customer_id or not data_generation_id:
        return _default(customer_id, data_generation_id)
    sql = (
        f"SELECT loyalty_tier, display_name FROM {SCHEMA}.customers "
        "WHERE customer_id = %(cid)s AND data_generation_id = %(gid)s"
    )
    try:
        rows = await _run_query(pool, sql, {"cid": customer_id, "gid": data_generation_id})
    except Exception:  # noqa: BLE001 — never take the session down on a lookup
        return _default(customer_id, data_generation_id)
    if not rows:
        return _default(customer_id, data_generation_id)
    tier = rows[0].get("loyalty_tier")
    tier = tier if tier in VALID_TIERS else "Standard"
    return LoyaltyContext(customer_id, data_generation_id, tier, rows[0].get("display_name"), False)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_loyalty_context.py -v`
Expected: PASS (all cases, including the scoping + never-raises checks).

- [ ] **Step 5: Commit**

```bash
git add src/services/loyalty_context.py tests/test_loyalty_context.py
git commit -m "feat: governed loyalty context reader (safe default, scoped)

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 5: Discovery contracts (resolve R1–R7)

**Files:**
- Create: `docs/discovery/lakebase-search-contract.md`, `docs/discovery/model-routing-contract.md`, `docs/discovery/runtime-contracts.md`
- Modify: `README.md` (Build target: mark Lakebase-capable yes/no)

**Interfaces:**
- Consumes: the confirmed profile (Task 1).
- Produces: verified facts consumed by Plans 2–4 — the exact Lakebase Search API, the three tier model ids + fallback, and the OTel/usage/ordering answers. Each fact is stamped "verified by `<command>`" (ReferenceApp methodology, notes §9).

- [ ] **Step 1: Load the Databricks skills**

Invoke `databricks-core`, then `databricks-lakebase`, `databricks-model-serving`, `databricks-python-sdk`. Use `--profile <name>` from Task 1 on every call.

- [ ] **Step 2: R6 — confirm a Lakebase-capable workspace**

Verify the profile can provision/reach Lakebase (ReferenceApp's `your-workspace` could not → degraded). Record the endpoint + database in `runtime-contracts.md`; update `README.md` Build target. If **not** capable, record that Plans 2–3 must run the degraded/simulated path (spec §17) and stop escalating this as a blocker.

- [ ] **Step 3: R1 — verify Lakebase Search** → `lakebase-search-contract.md`

Confirm Lakebase Search (BM25 + semantic) availability and the **exact** API: index creation DDL/SQL, whether embeddings are computed internally or need FMAPI, and the query call shape. Record a minimal working `semantic_search`-style example, each line "verified by `<command>`". If unavailable, record the fallback contract (pgvector + `tsvector` hybrid) instead. Also confirm the `WorkspaceClient().postgres.*` auth calls used in `src/services/db.py::create_pool` and note any correction.

- [ ] **Step 4: R2 — verify tier models** → `model-routing-contract.md`

List models UAIG serves on this workspace; for each candidate, verify **Responses-API passthrough with function calling** (notes §7: `system.ai.gpt-5-5` worked, `databricks-claude-sonnet-4-5` did not). Pick `UG_MODEL_STANDARD` / `PREMIUM` / `VIP` + `UG_MODEL_FALLBACK` from the verified-compatible set; record a per-model price (illustrative) for the Costs pillar. Resolve ReferenceApp's §7 model-config inconsistency for our own config.

- [ ] **Step 5: R3/R4/R7 — runtime contracts** → `runtime-contracts.md`

Record: (R3) whether `livekit-plugins-openai` surfaces per-turn Responses usage (tokens) or we estimate; (R4) that `AgentSession` can be constructed after `ctx.connect()` + `wait_for_participant()` in `livekit-agents==1.5.6` (cite a minimal check); (R7) the OTel ingest path to use (ReferenceApp's OTLP `/api/2.0/otel/v1/traces` endpoint vs zerobus).

- [ ] **Step 6: Commit**

```bash
git add docs/discovery README.md
git commit -m "docs: discovery contracts (Lakebase Search, tier models, runtime)

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

### Task 6: Generic Lakebase schema

**Files:**
- Create: `infra/lakebase_schema.sql`, `infra/apply_schema.py`
- Test: `tests/test_schema_sql.py`

**Interfaces:**
- Consumes: the Lakebase endpoint/db from Task 5; `src.services.db.SCHEMA`.
- Produces: the generic tables partitioned by `data_generation_id` (spec §8) — `datasets`, `documents`, `customers`, `records` — that Plans 2–4 read/write.

- [ ] **Step 1: Write the failing test** (static SQL invariants — no live DB)

`tests/test_schema_sql.py`:
```python
from pathlib import Path

SQL = Path("infra/lakebase_schema.sql").read_text().lower()


def test_all_tenant_tables_carry_data_generation_id():
    for table in ("documents", "customers", "records"):
        idx = SQL.find(f"create table if not exists {{schema}}.{table}")
        assert idx != -1, f"missing table {table}"
        body = SQL[idx: SQL.find(");", idx)]
        assert "data_generation_id" in body, f"{table} not scoped by data_generation_id"


def test_customers_has_loyalty_tier():
    assert "loyalty_tier" in SQL
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_schema_sql.py -v`
Expected: FAIL (`infra/lakebase_schema.sql` missing).

- [ ] **Step 3: Write `infra/lakebase_schema.sql`** (`{schema}` substituted by the apply script; spec §8)

```sql
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.datasets (
    data_generation_id TEXT PRIMARY KEY,
    company_name       TEXT NOT NULL,
    assistant_role     TEXT NOT NULL,
    system_prompt      TEXT NOT NULL,
    generator_model    TEXT,
    status             TEXT NOT NULL DEFAULT 'pending',
    doc_count          INT  NOT NULL DEFAULT 0,
    customer_count     INT  NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {schema}.documents (
    data_generation_id TEXT NOT NULL,
    doc_id             TEXT NOT NULL,
    title              TEXT,
    chunk_text         TEXT NOT NULL,
    metadata           JSONB,
    PRIMARY KEY (data_generation_id, doc_id)
);

CREATE TABLE IF NOT EXISTS {schema}.customers (
    data_generation_id TEXT NOT NULL,
    customer_id        TEXT NOT NULL,
    display_name       TEXT,
    loyalty_tier       TEXT NOT NULL DEFAULT 'Standard',
    attributes         JSONB,
    scored_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (data_generation_id, customer_id)
);

CREATE TABLE IF NOT EXISTS {schema}.records (
    data_generation_id TEXT NOT NULL,
    record_id          TEXT NOT NULL,
    customer_id        TEXT,
    kind               TEXT,
    fields             JSONB,
    status             TEXT,
    PRIMARY KEY (data_generation_id, record_id)
);
```
> The `documents` embedding column + Lakebase Search index are added in Plan 2 per Task 5's `lakebase-search-contract.md` (their exact type/DDL depend on that contract).

- [ ] **Step 4: Write `infra/apply_schema.py`**

```python
"""Apply infra/lakebase_schema.sql to Lakebase. Run: uv run python infra/apply_schema.py"""
import asyncio
from pathlib import Path

from src.services.db import SCHEMA, create_pool


async def main() -> None:
    ddl = Path("infra/lakebase_schema.sql").read_text().replace("{schema}", SCHEMA)
    pool = await create_pool()
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(ddl)
        print(f"[schema] applied to {SCHEMA}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run the SQL test, then apply live (if Lakebase-capable)**

Run: `uv run pytest tests/test_schema_sql.py -v` → Expected: PASS.
If Task 5 confirmed Lakebase: `uv run python infra/apply_schema.py --profile <name>` and verify the four tables exist (`\dt {schema}.*` via the Databricks SQL/psql path). If degraded, record that apply is deferred to a Lakebase-capable run.

- [ ] **Step 6: Commit**

```bash
git add infra tests/test_schema_sql.py
git commit -m "feat: generic Lakebase schema (data_generation_id-scoped)

Co-authored-by: Isaac <no-reply@databricks.com>"
```

---

## Self-Review

**1. Spec coverage (this plan's slice):** routing policy → §11 (Task 2); loyalty reader → §10/§12 (Task 4); generic schema → §8 (Task 6); Lakebase pool → §5/§16 (Task 3); discovery R1–R7 → §19 (Task 5); bootstrap/profile/`uv` → §16 (Task 1). Generator/retrieval/agent/UI/tracing/deploy are explicitly **out of this plan** (Plans 2–4). No in-scope gap.

**2. Placeholder scan:** No "TBD/TODO/handle edge cases." The two forward-notes (db.py auth lines, documents index) are explicit **cross-task dependencies on Task 5's contract**, not vague placeholders — the unit-tested surfaces are complete.

**3. Type consistency:** `RoutingDecision`/`route_for` (Task 2), `create_pool`/`_run_query`/`SCHEMA`/`QueryError` (Task 3), `LoyaltyContext`/`read_loyalty_context` (Task 4) are used consistently; `VALID_TIERS = ("Standard","Premium","VIP")` matches the schema default and the routing map. Schema table/column names (`customers.loyalty_tier`, `customers.data_generation_id`) match the loyalty query in Task 4.

**4. Review Focus:** unknown caller → Standard (Tasks 2, 4 ✓); stale/error → safe default, never raises (Task 4 ✓); invalid tier coerced (Tasks 2, 4 ✓); `data_generation_id` scoping (Task 4 query test ✓, Task 6 PKs ✓); directives don't leak tier (Task 2 ✓). Cross-plan items (stated-status, retrieval-miss, degraded mode) are noted for their owning plans.

---

*Plan 1 delivers a tested foundation + the verified contracts that let Plans 2–4 be written as concrete, no-placeholder code rather than guesses.*
