"""Stripe webhook signature verification and event handling.

Implemented against the documented `Stripe-Signature` scheme
(HMAC-SHA256 over "<timestamp>.<payload>") rather than the stripe SDK —
the relay needs exactly this one primitive, and zero extra dependencies
is a feature.
"""

import hmac
import hashlib
import json
import logging
import time
from typing import Callable

from .db import Store
from .tiers import tier_from_amount_cents, tier_named

logger = logging.getLogger(__name__)

SIGNATURE_TOLERANCE_SECONDS = 300


def verify_signature(
    payload: bytes,
    header: str,
    secret: str,
    clock: Callable[[], float] = time.time,
) -> bool:
    timestamp = ""
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidates.append(value)
    if not timestamp or not candidates:
        return False
    try:
        if abs(clock() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
            return False
    except ValueError:
        return False
    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, c) for c in candidates)


def sign_payload(payload: bytes, secret: str, timestamp: int) -> str:
    """Build a valid Stripe-Signature header (used by tests and the docs)."""
    signed = f"{timestamp}.".encode() + payload
    mac = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def handle_event(store: Store, event: dict) -> dict:
    """Apply one verified Stripe event to the store.

    Returns a small summary dict for logging/tests. Unknown event types
    are acknowledged and ignored — Stripe retries anything else.
    """
    event_type = str(event.get("type", ""))
    obj = event.get("data", {}).get("object", {}) or {}

    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata") or {}
        if metadata.get("tier"):
            tier = tier_named(metadata["tier"])
        else:
            tier = tier_from_amount_cents(int(obj.get("amount_total") or 0))
        email = str((obj.get("customer_details") or {}).get("email") or "")
        user_id, api_key = store.create_user(
            tier=tier.name,
            email=email,
            stripe_customer_id=str(obj.get("customer") or ""),
            stripe_subscription_id=str(obj.get("subscription") or ""),
        )
        session_id = str(obj.get("id") or "")
        if session_id:
            store.put_pending_key(session_id, api_key)
        logger.info("checkout completed: user %d tier %s", user_id, tier.name)
        return {"handled": event_type, "user_id": user_id, "tier": tier.name}

    if event_type == "invoice.paid":
        subscription_id = str(obj.get("subscription") or "")
        if subscription_id:
            store.reset_usage_for_subscription(subscription_id)
        logger.info("invoice paid: usage reset for %s", subscription_id or "<none>")
        return {"handled": event_type, "subscription": subscription_id}

    if event_type == "customer.subscription.deleted":
        subscription_id = str(obj.get("id") or "")
        if subscription_id:
            store.set_status_for_subscription(subscription_id, "revoked")
        logger.info("subscription deleted: revoked %s", subscription_id or "<none>")
        return {"handled": event_type, "subscription": subscription_id}

    return {"ignored": event_type}


def parse_event(payload: bytes) -> dict:
    return json.loads(payload.decode())
