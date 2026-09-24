# Discovery: FMAPI embeddings + Lakebase Search index/query DDL (Plan 2, Task 2)

_Verified live 2026-09-24 against DEFAULT (`your-workspace`) + Lakebase `projects/your-project/.../primary`, schema `ug` (scratch tables created and dropped)._

## Embeddings (FMAPI over the gateway)

Served embedding endpoints (verified `serving_endpoints.list` + a live `/ai-gateway/openai/v1/embeddings` call): `databricks-bge-large-en`, `databricks-gte-large-en`, `databricks-qwen3-embedding-0-6b` — **all dim 1024**.

- **`EMBED_MODEL = databricks-gte-large-en`**, **`EMBED_DIM = 1024`**.
- Call: `POST {host}/ai-gateway/openai/v1/embeddings` `{"model": EMBED_MODEL, "input": [texts]}` → `data[i].embedding` (len 1024). Bring-your-own embeddings (Lakebase does not generate them).

## Lakebase Search index opclasses (verified via pg_opclass)

- `lakebase_ann`: `vector_cosine_ops` / `vector_ip_ops` / `vector_l2_ops` (type `vector`) + `halfvec_*` + `rabitq4_*`/`rabitq8_*` (quantized). We use **`vector_cosine_ops`**.
- `lakebase_bm25`: **`tsvector_bm25_ops`** (type `tsvector`; the default for `tsvector`, so no opclass needed in DDL).

## ANN (semantic) — VERIFIED VERBATIM

Index:
```sql
CREATE INDEX IF NOT EXISTS documents_ann
  ON {schema}.documents USING lakebase_ann (embedding vector_cosine_ops);
```
Query (cosine; smaller distance = closer, so score = `1 - distance`, ORDER BY distance ASC):
```sql
SELECT doc_id, title, chunk_text, 1 - (embedding <=> %(q)s::vector) AS score
FROM {schema}.documents
WHERE data_generation_id = %(gid)s AND embedding IS NOT NULL
ORDER BY embedding <=> %(q)s::vector
LIMIT %(k)s;
```
Confirmed: exact-match row returned score 1.0. Pass the query vector as a bracketed literal (`'[...]'::vector`) — see `embeddings.to_pgvector`.

## BM25 (keyword) — VERIFIED VERBATIM

Column + index (populate `content_tsv = to_tsvector('english', chunk_text)` at write time):
```sql
-- documents.content_tsv tsvector
CREATE INDEX IF NOT EXISTS documents_bm25
  ON {schema}.documents USING lakebase_bm25 (content_tsv);
```
Query — the match/score operator is **`tsvector <@> bm25query_tsvector -> double precision`** (NOT `@@`). `to_bm25query`'s 2nd arg is the **BM25 index** regclass (not the table). Score is **negative for matches, ~0 for non-matches; more negative = more relevant → `ORDER BY … ASC`** (the index returns matches best-first and excludes non-matches):
```sql
SELECT doc_id, title, chunk_text,
       content_tsv <@> to_bm25query(to_tsvector('english', %(q)s),
                                    '{schema}.documents_bm25'::regclass) AS score
FROM {schema}.documents
WHERE data_generation_id = %(gid)s
ORDER BY content_tsv <@> to_bm25query(to_tsvector('english', %(q)s),
                                      '{schema}.documents_bm25'::regclass) ASC
LIMIT %(k)s;
```
Confirmed live: query "returns refund" ranked the returns/refund doc first (score -1.28) and excluded the shipping doc.

**Gotchas:** `to_bm25query(tsv, regclass)` regclass must be the **BM25 index** name; a table regclass errors `relation "…" is not an index`. The index must exist before the query references it. Extensions (`lakebase_tokenizer`, `lakebase_vector`, `lakebase_text`) require Lakebase Search enabled (done on your-project) and are `CREATE EXTENSION IF NOT EXISTS … CASCADE`.

## Net for Plan 2

- Task 3 embeddings: `EMBED_MODEL=databricks-gte-large-en`, `EMBED_DIM=1024`.
- Task 4 schema: `documents.embedding vector(1024)` + `documents.content_tsv tsvector`; `documents_ann` + `documents_bm25` indexes (DDL above).
- Task 5 retrieval: ANN + BM25 queries above (both scoped by `data_generation_id`).
- Task 6 generator: set `embedding = '[…]'::vector` and `content_tsv = to_tsvector('english', chunk_text)` on each document row.
