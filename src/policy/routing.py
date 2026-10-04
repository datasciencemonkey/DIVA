"""Deterministic loyalty->model routing (spec §11). Pure, no I/O, outside the LLM.

v1 is the StaticTierStrategy: tier -> model from env, plus LLM-safe behavior
directives. A future served "decision model" (spec Future extensions) drops in
behind route_for() with the same RoutingDecision contract.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from src.policy.tiers import VALID_TIERS, THOROUGHNESS as _THOROUGHNESS

_ENV_BY_TIER = {
    "Standard": "UG_MODEL_STANDARD",
    "Premium": "UG_MODEL_PREMIUM",
    "VIP": "UG_MODEL_VIP",
}


@dataclass(frozen=True)
class RoutingDecision:
    tier: str          # governance signal — used to build directives, never sent to the LLM
    model: str         # Unity Gateway-served conversational model for this session
    directives: dict   # LLM-safe behavior flags only (no tier/loyalty/spend)


def _directives_for(tier: str) -> dict:
    warm = tier in ("Premium", "VIP")
    return {
        "recognition_tone": "warm" if warm else "neutral",
        "be_proactive": tier == "VIP",
        "thoroughness": _THOROUGHNESS[tier],
        "offer_human_escalation": tier == "VIP",
    }


def route_for(loyalty_tier: str | None) -> RoutingDecision:
    tier = loyalty_tier if loyalty_tier in VALID_TIERS else "Standard"
    model = os.getenv(_ENV_BY_TIER[tier], "") or os.getenv("UG_MODEL_FALLBACK", "")
    if not model:
        raise RuntimeError(
            f"No model configured for tier {tier!r} and no UG_MODEL_FALLBACK set — "
            "set UG_MODEL_STANDARD/PREMIUM/VIP + UG_MODEL_FALLBACK "
            "(see docs/discovery/model-routing-contract.md)."
        )
    return RoutingDecision(tier=tier, model=model, directives=_directives_for(tier))
