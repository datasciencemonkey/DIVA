"""Canonical loyalty-tier constants — the single source of truth (spec §11)."""
from __future__ import annotations

VALID_TIERS: tuple[str, ...] = ("Standard", "Premium", "VIP")

THOROUGHNESS: dict[str, str] = {
    "Standard": "concise",
    "Premium": "balanced",
    "VIP": "thorough",
}
