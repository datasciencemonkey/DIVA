-- Lakebase Search extensions (must exist before the documents.embedding vector column).
CREATE EXTENSION IF NOT EXISTS lakebase_tokenizer CASCADE;
CREATE EXTENSION IF NOT EXISTS lakebase_vector CASCADE;
CREATE EXTENSION IF NOT EXISTS lakebase_text CASCADE;

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
    embedding          vector(1024),
    content_tsv        tsvector,
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

-- Idempotent migrations for existing installs: CREATE TABLE IF NOT EXISTS above
-- does NOT add new columns to a documents table created by an earlier plan.
ALTER TABLE {schema}.documents ADD COLUMN IF NOT EXISTS embedding vector(1024);
ALTER TABLE {schema}.documents ADD COLUMN IF NOT EXISTS content_tsv tsvector;
