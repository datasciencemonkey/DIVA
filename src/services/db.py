"""Lakebase (Postgres) connection pool + query runner.

Pool auth mirrors ReferenceApp src/services/recovery.py: WorkspaceClient postgres
endpoint + a generated credential + current user, sslmode=require. The live
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
    result (or None) down; see spec §17 degraded mode.

    NOTE: the exact WorkspaceClient().postgres.* surface is pinned by Task 5's
    lakebase-search-contract.md and exercised live in Task 6.
    """
    from databricks.sdk import WorkspaceClient
    from psycopg_pool import AsyncConnectionPool

    w = WorkspaceClient()
    endpoint = os.environ["LAKEBASE_ENDPOINT"]
    database = os.getenv("LAKEBASE_DATABASE", "databricks_postgres")
    ep = w.postgres.get_endpoint(endpoint)
    cred = w.postgres.generate_database_credential(
        request_id=os.urandom(8).hex(), instance_names=[endpoint]
    )
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
