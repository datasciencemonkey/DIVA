"""CLI: generate a synthetic company dataset into the ug schema.

Run:
    DATABRICKS_CONFIG_PROFILE=DEFAULT \
    LAKEBASE_ENDPOINT=projects/your-project/branches/production/endpoints/primary \
    LAKEBASE_DATABASE=databricks_postgres UG_SCHEMA=ug \
    uv run python generate.py "<company>" --role "<role>" --prompt "<system prompt>"
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# Make the repo root importable when run as a script (mirrors infra/apply_schema.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.generate import generate_dataset  # noqa: E402
from src.services.db import create_pool  # noqa: E402


async def _main(args) -> None:
    pool = await create_pool()
    try:
        gid = await generate_dataset(args.company, args.role, args.prompt, model=args.model, pool=pool)
        print(gid)
    finally:
        await pool.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("company")
    p.add_argument("--role", default="customer support")
    p.add_argument("--prompt",
                   default="Answer questions about the company's products, orders, and policies.")
    # Generator model is decoupled from the routing tiers: use a chat model that
    # supports response_format=json_object (verified: system.ai.gpt-5-4, the name .env.example uses).
    p.add_argument("--model", default=os.getenv("UG_GEN_MODEL", "system.ai.gpt-5-4"))
    asyncio.run(_main(p.parse_args()))
