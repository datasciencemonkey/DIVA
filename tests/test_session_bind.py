import pytest
import src.services.session_bind as sb
from src.services.session_bind import bind_session, BindContext
from src.services.loyalty_context import LoyaltyContext


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setenv("UG_MODEL_STANDARD", "m-std")
    monkeypatch.setenv("UG_MODEL_PREMIUM", "m-prem")
    monkeypatch.setenv("UG_MODEL_VIP", "m-vip")
    monkeypatch.setenv("UG_MODEL_FALLBACK", "m-fb")


async def test_bind_reads_dataset_and_routes_by_governed_tier(monkeypatch):
    async def fake_ds(pool, gid): return {"company_name": "Acme", "system_prompt": "Help with orders."}
    async def fake_loyalty(pool, cid, gid):
        return LoyaltyContext(cid, gid, "VIP", "Grace", False)
    monkeypatch.setattr(sb, "read_dataset", fake_ds)
    monkeypatch.setattr(sb, "read_loyalty_context", fake_loyalty)
    ctx = await bind_session(object(), "G1", "C1")
    assert isinstance(ctx, BindContext)
    assert ctx.company == "Acme" and ctx.system_prompt == "Help with orders."
    assert ctx.tier == "VIP" and ctx.model == "m-vip"
    assert ctx.directives["recognition_tone"] == "warm"
    assert ctx.courtesy_name == "Grace"


async def test_unknown_caller_defaults_to_standard(monkeypatch):
    async def fake_ds(pool, gid): return {"company_name": "Acme", "system_prompt": "Help."}
    async def fake_loyalty(pool, cid, gid):
        return LoyaltyContext(cid, gid, "Standard", None, True)  # miss -> Standard default
    monkeypatch.setattr(sb, "read_dataset", fake_ds)
    monkeypatch.setattr(sb, "read_loyalty_context", fake_loyalty)
    ctx = await bind_session(object(), "G1", None)
    assert ctx.tier == "Standard" and ctx.model == "m-std"


async def test_missing_dataset_uses_safe_default_prompt(monkeypatch):
    async def fake_ds(pool, gid): return None
    async def fake_loyalty(pool, cid, gid):
        return LoyaltyContext(cid, gid, "Standard", None, True)
    monkeypatch.setattr(sb, "read_dataset", fake_ds)
    monkeypatch.setattr(sb, "read_loyalty_context", fake_loyalty)
    ctx = await bind_session(object(), "nope", "C1")
    assert ctx.system_prompt and ctx.company  # non-empty safe defaults, no crash


async def test_read_dataset_returns_none_when_pool_is_none():
    # Degraded mode (spec §17): no pool -> None, mirroring read_loyalty_context. Never raises.
    assert await sb.read_dataset(None, "G1") is None


async def test_bind_degrades_safely_when_pool_is_none():
    # Whole fail-soft path with Lakebase down: safe default company/prompt + Standard tier,
    # no crash (read_dataset None + read_loyalty_context's own pool-None guard -> Standard).
    ctx = await bind_session(None, "G1", "C1")
    assert ctx.company and ctx.system_prompt
    assert ctx.tier == "Standard" and ctx.model == "m-std"
