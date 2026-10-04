"""Governed loyalty lookup, bound once at session start (spec §10, §12).

NEVER raises: missing / unknown / stale / error -> Standard, stale=True.
Always scoped by data_generation_id (multi-tenant isolation). The tier is a
governance signal used to route + build directives, never surfaced to the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.policy.tiers import VALID_TIERS
from src.services.db import SCHEMA, _run_query


@dataclass(frozen=True)
class LoyaltyContext:
    customer_id: str | None
    data_generation_id: str | None
    loyalty_tier: str
    display_name: str | None
    stale: bool


def _default(customer_id, data_generation_id) -> LoyaltyContext:
    return LoyaltyContext(customer_id, data_generation_id, "Standard", None, True)


async def read_loyalty_context(pool, customer_id, data_generation_id) -> LoyaltyContext:
    if pool is None or not customer_id or not data_generation_id:
        return _default(customer_id, data_generation_id)
    sql = (
        f"SELECT loyalty_tier, display_name FROM {SCHEMA}.customers "
        "WHERE customer_id = %(cid)s AND data_generation_id = %(gid)s"
    )
    try:
        rows = await _run_query(pool, sql, {"cid": customer_id, "gid": data_generation_id})
    except Exception:  # noqa: BLE001 — never take the session down on a lookup
        return _default(customer_id, data_generation_id)
    if not rows:
        return _default(customer_id, data_generation_id)
    tier = rows[0].get("loyalty_tier")
    tier = tier if tier in VALID_TIERS else "Standard"
    return LoyaltyContext(customer_id, data_generation_id, tier, rows[0].get("display_name"), False)
