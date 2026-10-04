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

    Auth verified 2026-09-24 (docs/discovery/lakebase-search-contract.md):
    LAKEBASE_ENDPOINT is the endpoint RESOURCE PATH
    (projects/<p>/branches/<b>/endpoints/<e>); host lives at status.hosts.host;
    generate_database_credential takes that path positionally and returns .token.
    """
    import socket

    from databricks.sdk import WorkspaceClient
    from psycopg_pool import AsyncConnectionPool

    w = WorkspaceClient()  # respects DATABRICKS_CONFIG_PROFILE / Apps-injected auth
    ep_path = os.environ["LAKEBASE_ENDPOINT"]
    database = os.getenv("LAKEBASE_DATABASE", "databricks_postgres")
    host = w.postgres.get_endpoint(ep_path).as_dict()["status"]["hosts"]["host"]
    cred = w.postgres.generate_database_credential(ep_path)
    token = getattr(cred, "token", None) or cred.as_dict().get("token")
    user = w.current_user.me().user_name
    conninfo = (
        f"host={host} user={user} dbname={database} "
        f"password={token} sslmode=require"
    )
    # macOS long-hostname getaddrinfo workaround: pin hostaddr, keep host for TLS SNI.
    try:
        conninfo += f" hostaddr={socket.getaddrinfo(host, 5432)[0][4][0]}"
    except Exception:  # noqa: BLE001
        pass
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
                        if cur.description is None:
                            return []  # INSERT/UPDATE/DDL — no result set
                        cols = [d[0] for d in cur.description]
                        rows = await cur.fetchall()
                        return [dict(zip(cols, r)) for r in rows]
            return await asyncio.wait_for(_go(), timeout=_QUERY_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise QueryError(str(last))
