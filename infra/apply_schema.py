"""Apply infra/lakebase_schema.sql to Lakebase.

Run (local, DEFAULT profile):
    DATABRICKS_CONFIG_PROFILE=DEFAULT \
    LAKEBASE_ENDPOINT=projects/your-project/branches/production/endpoints/primary \
    LAKEBASE_DATABASE=databricks_postgres UG_SCHEMA=ug \
    uv run python infra/apply_schema.py
"""
import asyncio
import sys
from pathlib import Path

# Make the repo root importable when run as a script (python infra/apply_schema.py),
# which otherwise only puts infra/ on sys.path. Mirrors ReferenceApp's _REPO_ROOT pattern.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.db import SCHEMA, create_pool  # noqa: E402


async def main() -> None:
    root = Path(__file__).resolve().parent
    schema_ddl = (root / "lakebase_schema.sql").read_text().replace("{schema}", SCHEMA)
    index_ddl = (root / "lakebase_indexes.sql").read_text().replace("{schema}", SCHEMA)
    pool = await create_pool()
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(schema_ddl)
            print(f"[schema] applied to {SCHEMA}")
            async with conn.cursor() as cur:
                await cur.execute(index_ddl)
            print("[indexes] applied")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
