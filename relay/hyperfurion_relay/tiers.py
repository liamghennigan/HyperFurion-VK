"""Subscription tiers and how Stripe events map onto them.

Caps are the no-loss guarantee: at xAI list prices (verified 2026-07-04:
streaming STT $0.20/hour, TTS $15.00 per million characters) a maxed-out
subscriber must cost LESS than the subscription nets after Stripe fees
(~$4.45 on $5, ~$9.19 on $10, assuming worst-case international card +
FX). Re-derive these caps whenever xAI pricing changes:

    basic: 20 h x $0.20 = $4.00  +  10k chars x $15/M = $0.15  -> $4.15
    pro:   40 h x $0.20 = $8.00  +  50k chars x $15/M = $0.75  -> $8.75
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Tier:
    name: str
    usd_per_month: int
    stt_seconds: int
    tts_chars: int


TIERS: dict[str, Tier] = {
    # $5/mo: worst case $4.15 — never exceeds what the subscription nets.
    "basic": Tier("basic", 5, 20 * 3600, 10_000),
    # $10/mo: worst case $8.75 — same guarantee, no outlier exposure.
    "pro": Tier("pro", 10, 40 * 3600, 50_000),
}

DEFAULT_TIER = "basic"


def tier_named(name: str) -> Tier:
    return TIERS.get(str(name).strip().lower(), TIERS[DEFAULT_TIER])


def tier_from_amount_cents(amount: int) -> Tier:
    """Fallback mapping when a checkout session carries no tier metadata."""
    for tier in TIERS.values():
        if tier.usd_per_month * 100 == amount:
            return tier
    return TIERS[DEFAULT_TIER]
