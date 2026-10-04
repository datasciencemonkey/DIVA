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

## R1 — retrieval mechanism (RESOLVED: Lakebase Search ENABLED)

**Lakebase Search is a project-level feature** (`shared_preload_libraries`); its two agent-native extensions need it toggled on in project settings + a restart. The user **enabled it on your-project** (2026-09-24). Verified live: `CREATE EXTENSION` now succeeds for `lakebase_vector` (1.1.1), `lakebase_text` (0.1.2), `lakebase_tokenizer` (0.1.0); `vector` (pgvector 0.8.0) also installed. Index access methods present: **`lakebase_bm25`/`lakebase_bm25v0`** (BM25), **`lakebase_ann`/`lakebase_annv0`/`lakebase_annv1`** (ANN vector), plus pgvector `hnsw`/`ivfflat`. (Docs: `.../oltp/projects/extensions`.)

**Semantic (ANN) — `lakebase_vector`:**
- Embeddings are **bring-your-own** — store as pgvector `vector` (generate via FMAPI embeddings). Optional RaBitQ quantization for compact ANN: `quantize_to_rabitq4/8`, types `rabitq4`/`rabitq8`, `dequantize_to_vector/halfvec`.
- Index: `CREATE INDEX ... USING lakebase_ann (embedding <opclass>)`. Distance operators verified live: `<=>` cosine, `<->` L2, `<#>` inner-product (quantized variants `<<=>>`/`<<->>`/`<<#>>`). Query: `ORDER BY embedding <=> $q LIMIT k`.
- Mgmt: `lakebase_ann_index_info(regclass)`, `lakebase_ann_prewarm(regclass)`.

**Keyword (BM25) — `lakebase_text` (+ `lakebase_tokenizer`):**
- Tokenize text → `tsvector` (native `to_tsvector` or `lakebase_tokenizer`); index `CREATE INDEX ... USING lakebase_bm25 (...)`; query via `to_bm25query(tsvector, regclass) -> bm25query_tsvector` with BM25 ranking.

**Plan 2 retrieval design:**
- `semantic_search` → embed query (FMAPI) → `lakebase_ann` cosine kNN over `documents.embedding` scoped by `data_generation_id`.
- keyword → `lakebase_bm25` over doc/record text; structured `record_lookup` → plain SQL; all scoped by `data_generation_id`. **Hybrid BM25+ANN** is the agent-native retrieval story.
- Plan 2 adds `documents.embedding vector(<dim>)` (dim from the chosen FMAPI embedding model) + the two indexes. Exact opclass names / full DDL: confirm from the OLTP search docs at implementation (the access methods + operators above are the live ground truth). `pgvector` `hnsw` + native FTS/`pg_trgm` remain the no-Lakebase-Search fallback.

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
