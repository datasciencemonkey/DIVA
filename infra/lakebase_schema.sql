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
