import pytest
from src.policy.routing import RoutingDecision, route_for, VALID_TIERS


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setenv("UG_MODEL_STANDARD", "model-standard")
    monkeypatch.setenv("UG_MODEL_PREMIUM", "model-premium")
    monkeypatch.setenv("UG_MODEL_VIP", "model-vip")
    monkeypatch.setenv("UG_MODEL_FALLBACK", "model-fallback")


@pytest.mark.parametrize("tier,model", [
    ("Standard", "model-standard"),
    ("Premium", "model-premium"),
    ("VIP", "model-vip"),
])
def test_each_tier_routes_to_its_model(tier, model):
    d = route_for(tier)
    assert isinstance(d, RoutingDecision)
    assert d.tier == tier and d.model == model


@pytest.mark.parametrize("bad", [None, "", "Gold", "vip", "UNKNOWN"])
def test_unknown_tier_falls_back_to_standard(bad):
    d = route_for(bad)
    assert d.tier == "Standard" and d.model == "model-standard"


def test_missing_model_env_uses_fallback(monkeypatch):
    monkeypatch.delenv("UG_MODEL_VIP", raising=False)
    assert route_for("VIP").model == "model-fallback"


def test_directives_never_leak_the_raw_tier():
    d = route_for("VIP")
    blob = repr(d.directives).lower()
    assert "vip" not in blob and "tier" not in blob and "loyalty" not in blob


def test_warm_recognition_only_for_premium_and_vip():
    assert route_for("Standard").directives["recognition_tone"] == "neutral"
    assert route_for("Premium").directives["recognition_tone"] == "warm"
    assert route_for("VIP").directives["recognition_tone"] == "warm"


def test_route_for_raises_when_no_model_resolves(monkeypatch):
    for v in ("UG_MODEL_STANDARD", "UG_MODEL_PREMIUM", "UG_MODEL_VIP", "UG_MODEL_FALLBACK"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(RuntimeError):
        route_for("Standard")
