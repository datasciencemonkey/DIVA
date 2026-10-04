-- Lakebase Search indexes over documents (opclasses verified in
-- docs/discovery/embeddings-and-index-contract.md). Extensions are created by
-- lakebase_schema.sql (before the vector column), so they exist by now.

-- ANN (semantic), cosine — queried with `embedding <=> $q::vector`.
CREATE INDEX IF NOT EXISTS documents_ann
    ON {schema}.documents USING lakebase_ann (embedding vector_cosine_ops);

-- BM25 (keyword) over the tsvector — queried with
-- `content_tsv <@> to_bm25query(to_tsvector('english', $q), '{schema}.documents_bm25'::regclass)`.
CREATE INDEX IF NOT EXISTS documents_bm25
    ON {schema}.documents USING lakebase_bm25 (content_tsv);
