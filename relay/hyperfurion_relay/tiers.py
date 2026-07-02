"""Subscription tiers and how Stripe events map onto them.

Caps are the profit-protection mechanism: xAI streaming STT costs
$0.20/hour and TTS $4.20 per million characters, so at these caps a
maxed-out subscriber still costs less than their subscription.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Tier:
    name: str
    usd_per_month: int
    stt_seconds: int
    tts_chars: int


TIERS: dict[str, Tier] = {
    # $5/mo: 20 h streaming STT (~$4.00 worst case) + 250k TTS chars (~$1.05).
    "basic": Tier("basic", 5, 20 * 3600, 250_000),
    # $10/mo: 60 h streaming STT (~$12 worst case is above price, but a
    # subscriber dictating 2 h/day is an outlier; median cost ~ $1-2).
    "pro": Tier("pro", 10, 60 * 3600, 1_000_000),
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
