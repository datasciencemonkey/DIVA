from src.policy import tiers


def test_single_source_of_truth_for_tiers():
    assert tiers.VALID_TIERS == ("Standard", "Premium", "VIP")
    # every valid tier has a thoroughness mapping (guards the _THOROUGHNESS KeyError trap)
    assert set(tiers.THOROUGHNESS) == set(tiers.VALID_TIERS)


def test_routing_and_loyalty_share_the_same_constant():
    from src.policy import routing
    from src.services import loyalty_context
    assert routing.VALID_TIERS is tiers.VALID_TIERS
    assert loyalty_context.VALID_TIERS is tiers.VALID_TIERS
