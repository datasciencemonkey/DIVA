"""Bind a call session (spec §6, §10): governed tier -> model + directives + prompt.
The tier comes from read_loyalty_context (by customer_id+gid), NEVER from anything spoken."""
from __future__ import annotations

from dataclasses import dataclass

from src.policy.routing import route_for
from src.services.db import SCHEMA, _run_query
from src.services.loyalty_context import read_loyalty_context

_DEFAULT_PROMPT = "You are a helpful customer-support voice assistant."


@dataclass(frozen=True)
class BindContext:
    data_generation_id: str
    customer_id: str | None
    company: str
    system_prompt: str
    courtesy_name: str | None
    tier: str
    model: str
    directives: dict


async def read_dataset(pool, data_generation_id) -> dict | None:
    rows = await _run_query(
        pool,
        f"SELECT company_name, system_prompt FROM {SCHEMA}.datasets "
        "WHERE data_generation_id = %(g)s AND status = 'ready'",
        {"g": data_generation_id})
    return rows[0] if rows else None


async def bind_session(pool, data_generation_id, customer_id, courtesy_name=None) -> BindContext:
    ds = await read_dataset(pool, data_generation_id) or {}
    loyalty = await read_loyalty_context(pool, customer_id, data_generation_id)  # never raises; Standard default
    decision = route_for(loyalty.loyalty_tier)  # deterministic tier -> model + directives, outside the LLM
    return BindContext(
        data_generation_id=data_generation_id,
        customer_id=customer_id,
        company=ds.get("company_name") or "the company",
        system_prompt=ds.get("system_prompt") or _DEFAULT_PROMPT,
        courtesy_name=courtesy_name or loyalty.display_name,
        tier=loyalty.loyalty_tier,
        model=decision.model,
        directives=decision.directives,
    )
