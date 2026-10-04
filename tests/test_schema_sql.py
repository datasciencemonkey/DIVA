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


def test_documents_has_embedding_and_tsv_columns():
    assert "embedding vector(" in SQL and "content_tsv tsvector" in SQL
    # extensions must be created (before the vector column) in the schema file
    assert "create extension if not exists lakebase_vector" in SQL
    assert "create extension if not exists lakebase_text" in SQL


def test_indexes_use_lakebase_search_access_methods():
    idx = Path("infra/lakebase_indexes.sql").read_text().lower()
    assert "using lakebase_ann" in idx and "using lakebase_bm25" in idx
