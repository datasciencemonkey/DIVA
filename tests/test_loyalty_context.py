import src.services.loyalty_context as lc
from src.services.loyalty_context import read_loyalty_context, LoyaltyContext


class _Pool:  # truthy sentinel so the None-guard doesn't short-circuit
    pass


async def test_pool_none_returns_standard_default():
    ctx = await read_loyalty_context(None, "C1", "G1")
    assert ctx.loyalty_tier == "Standard" and ctx.stale is True


async def test_missing_customer_returns_default(monkeypatch):
    async def fake(pool, sql, params=None): return []
    monkeypatch.setattr(lc, "_run_query", fake)
    ctx = await read_loyalty_context(_Pool(), "NOPE", "G1")
    assert ctx.loyalty_tier == "Standard" and ctx.stale is True


async def test_valid_row_returns_tier_and_name(monkeypatch):
    async def fake(pool, sql, params=None):
        return [{"loyalty_tier": "VIP", "display_name": "Sam"}]
    monkeypatch.setattr(lc, "_run_query", fake)
    ctx = await read_loyalty_context(_Pool(), "C1", "G1")
    assert ctx == LoyaltyContext("C1", "G1", "VIP", "Sam", False)


async def test_invalid_tier_value_coerced_to_standard(monkeypatch):
    async def fake(pool, sql, params=None):
        return [{"loyalty_tier": "Platinum", "display_name": None}]
    monkeypatch.setattr(lc, "_run_query", fake)
    ctx = await read_loyalty_context(_Pool(), "C1", "G1")
    assert ctx.loyalty_tier == "Standard" and ctx.stale is False


async def test_never_raises_on_query_error(monkeypatch):
    async def boom(pool, sql, params=None): raise RuntimeError("db down")
    monkeypatch.setattr(lc, "_run_query", boom)
    ctx = await read_loyalty_context(_Pool(), "C1", "G1")
    assert ctx.loyalty_tier == "Standard" and ctx.stale is True


async def test_query_is_scoped_by_data_generation_id(monkeypatch):
    seen = {}
    async def fake(pool, sql, params=None):
        seen["sql"], seen["params"] = sql, params
        return [{"loyalty_tier": "Premium", "display_name": "A"}]
    monkeypatch.setattr(lc, "_run_query", fake)
    await read_loyalty_context(_Pool(), "C1", "G1")
    assert "data_generation_id" in seen["sql"]
    assert seen["params"]["gid"] == "G1" and seen["params"]["cid"] == "C1"
