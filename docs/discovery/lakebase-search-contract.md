# Discovery: Lakebase connectivity + retrieval (R6, R1)

_Verified 2026-09-24 against the DEFAULT profile (workspace `your-workspace`)._

## R6 — Lakebase reachable (resolved: YES)

- DEFAULT profile → `https://your-workspace.cloud.databricks.com` (same workspace as the `your-workspace` profile). Verified by `databricks auth profiles` + `databricks current-user me`.
- The user-provided instance maps to endpoint resource path
  **`projects/your-project/branches/production/endpoints/primary`** — verified by iterating `w.postgres.list_projects/list_branches/list_endpoints` and matching the host.
- Host: `ep-your-endpoint-id.database.us-east-1.cloud.databricks.com` (pooled host also available: `…-pooler.…`).
- Database: `databricks_postgres`. Postgres **16.15**. Connected user: `you@example.com`.
- Verified by a live `psycopg.connect(...)` running `SELECT version()`.
- **Update to ReferenceApp's caveat:** ReferenceApp (2026-08-29) reported `your-workspace` could not provision Lakebase → degraded mode. As of 2026-09-24 it **can** (this instance). Degraded mode (spec §17) is now the fallback, not the expected path.

## R1 — retrieval mechanism (resolved)

Extensions available (verified `SELECT name FROM pg_available_extensions WHERE name ~* 'vector|search|bm25|trgm'`):
`vector` (pgvector), `lakebase_vector` (Lakebase-native), `pg_trgm`. Installed: only `plpgsql` → **must `CREATE EXTENSION`**.

- **Semantic search** → pgvector. Use `vector` (standard, well-documented) or `lakebase_vector` (Lakebase-native) — decide in Plan 2; embed doc chunks (FMAPI embeddings), cosine/L2 kNN over `documents` scoped by `data_generation_id`.
- **Keyword / exact** → Postgres native full-text search (`tsvector`/`tsquery`, built-in, no extension) and/or `pg_trgm` fuzzy match. **No standalone BM25/`pg_search` extension surfaced**, so the blog's "Lakebase Search (BM25)" is realized here via native FTS + trigram rather than a separately-named extension.
- Net: retrieval is fully available on this instance; `semantic_search` = pgvector, `record_lookup`/keyword = SQL + FTS/`pg_trgm`. No blocker.

## create_pool auth recipe (CORRECTED — supersedes the provisional Task 3 code)

Verified SDK signatures (databricks-sdk 0.133.0):
- `w.postgres.get_endpoint(name: str) -> Endpoint` — host at `endpoint.as_dict()["status"]["hosts"]["host"]`.
- `w.postgres.generate_database_credential(endpoint: str, *, claims=None, expire_time=None, group_name=None, ttl=None) -> DatabaseCredential` — token at `.token` (JWT, ~1h TTL). **Positional `endpoint` = the endpoint resource path; NOT `instance_names=[...]`/`request_id` (the provisional Task 3 code was wrong).**

Recipe:
```python
w = WorkspaceClient()                       # respects DATABRICKS_CONFIG_PROFILE env
ep_path = os.environ["LAKEBASE_ENDPOINT"]   # projects/your-project/branches/production/endpoints/primary
host = w.postgres.get_endpoint(ep_path).as_dict()["status"]["hosts"]["host"]
token = w.postgres.generate_database_credential(ep_path).token
user = w.current_user.me().user_name
conninfo = f"host={host} user={user} dbname={os.getenv('LAKEBASE_DATABASE','databricks_postgres')} password={token} sslmode=require"
# macOS long-hostname getaddrinfo workaround: add hostaddr=<resolved ip>, keep host for TLS SNI.
```
Env for local runs: `DATABRICKS_CONFIG_PROFILE=DEFAULT`, `LAKEBASE_ENDPOINT=projects/your-project/branches/production/endpoints/primary`, `LAKEBASE_DATABASE=databricks_postgres`, `UG_SCHEMA=ug`.
Token TTL ~1h → implement refresh for long-lived workers (Plan 3 concern).
