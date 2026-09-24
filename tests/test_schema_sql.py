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
