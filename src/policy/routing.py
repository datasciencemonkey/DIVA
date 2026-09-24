"""Deterministic loyalty->model routing (spec §11). Pure, no I/O, outside the LLM.

v1 is the StaticTierStrategy: tier -> model from env, plus LLM-safe behavior
directives. A future served "decision model" (spec Future extensions) drops in
behind route_for() with the same RoutingDecision contract.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

VALID_TIERS = ("Standard", "Premium", "VIP")

_ENV_BY_TIER = {
    "Standard": "UG_MODEL_STANDARD",
    "Premium": "UG_MODEL_PREMIUM",
    "VIP": "UG_MODEL_VIP",
}

_THOROUGHNESS = {"Standard": "concise", "Premium": "balanced", "VIP": "thorough"}


@dataclass(frozen=True)
class RoutingDecision:
    tier: str          # governance signal — used to build directives, never sent to the LLM
    model: str         # UAIG-served conversational model for this session
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
    return RoutingDecision(tier=tier, model=model, directives=_directives_for(tier))
