# HyperFurion relay

A metered subscription relay in front of xAI STT/TTS. Subscribers get one
`hfk_` key instead of a provider account; the relay authenticates it,
enforces per-tier monthly quotas, and forwards traffic to xAI on the
operator's master key. It speaks the **same wire protocol as xAI**, so the
voice-keyboard daemon talks to it through its built-in `hyperfurion`
provider — no client-side special cases.

```
daemon (provider = "hyperfurion", hfk_ key)
   │  wss /v1/stt   ── streaming PCM, xAI protocol
   │  POST /v1/tts  ── xAI request shape
   ▼
relay ── auth ── quota check ── meter ──► api.x.ai (master XAI_API_KEY)
   ▲
Stripe webhooks (checkout → key issued, invoice → usage reset,
                 cancellation → key revoked)
```

## Tiers

| Tier  | Price | Streaming STT | TTS characters | Worst-case upstream cost |
|-------|-------|---------------|----------------|--------------------------|
| basic | $5/mo | 20 h/month    | 250,000/month  | ~$5.05 (median user ~$1) |
| pro   | $10/mo| 60 h/month    | 1,000,000/month| capped, median ~$2       |

Caps are hard: STT refuses (and cuts off mid-session) past the limit, TTS
returns 429, both with a message naming the reset date. A subscriber cannot
run up your bill. Edit `hyperfurion_relay/tiers.py` to change the numbers.

## Endpoints

- `WS /v1/stt` — streaming STT, proxied to `wss://api.x.ai/v1/stt`
- `POST /v1/tts` — TTS, proxied to `https://api.x.ai/v1/tts`
- `GET /v1/usage` — quota status for the presented key
- `GET /healthz` — liveness + tier catalog
- `POST /stripe/webhook` — signature-verified Stripe events
- `GET /welcome?session_id=…` — one-time key pickup after checkout

Auth everywhere: `Authorization: Bearer hfk_…`. Keys are stored as SHA-256
hashes; the plaintext exists only in the moment it is issued.

## Run it

```bash
cd relay
pip install .
export XAI_API_KEY="xai-..."           # your master key
export RELAY_DB="/var/lib/hyperfurion/relay.db"
export STRIPE_WEBHOOK_SECRET="whsec_..."
hyperfurion-relay                       # listens on :8787
```

Or Docker:

```bash
docker build -t hyperfurion-relay relay/
docker run -p 8787:8787 -v relay-data:/data \
  -e XAI_API_KEY=xai-... -e STRIPE_WEBHOOK_SECRET=whsec_... hyperfurion-relay
```

Put TLS in front (Caddy makes this two lines):

```
api.hyperfurion.com {
    reverse_proxy localhost:8787
}
```

Any $5 VPS or a Fly.io free-tier machine is plenty — a few hundred
subscribers is a few requests per second at peak.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `XAI_API_KEY` | *(required)* | master upstream key |
| `RELAY_DB` | `relay.db` | SQLite path (WAL mode) |
| `STRIPE_WEBHOOK_SECRET` | *(empty)* | webhook signature secret |
| `UPSTREAM_STT_URL` | `wss://api.x.ai/v1/stt` | override for tests/self-host |
| `UPSTREAM_TTS_URL` | `https://api.x.ai/v1/tts` | override for tests/self-host |
| `RELAY_HOST` / `RELAY_PORT` | `0.0.0.0` / `8787` | bind address |

## Selling subscriptions

### Phase 0 — validate first (no Stripe, no code)

Hand-issue keys to early sponsors:

```bash
hyperfurion-relay-admin issue --tier basic --email fan@example.com
hyperfurion-relay-admin list
hyperfurion-relay-admin revoke --id 3
```

Add a $5 GitHub Sponsors tier that says "hosted voice tier — I'll email
your key", and issue keys as sponsorships arrive. If people bite, wire up
Stripe.

### Phase 1 — Stripe automation

1. In Stripe, create a **subscription product** per tier ($5 basic,
   $10 pro), then a **Payment Link** for each with:
   - metadata: `tier = basic` (or `pro`)
   - confirmation redirect:
     `https://api.hyperfurion.com/welcome?session_id={CHECKOUT_SESSION_ID}`
2. Add a webhook endpoint `https://api.hyperfurion.com/stripe/webhook`
   subscribed to `checkout.session.completed`, `invoice.paid`,
   `customer.subscription.deleted`; put its signing secret in
   `STRIPE_WEBHOOK_SECRET`.
3. Done. Checkout → the webhook creates the subscriber and parks the key →
   the redirect shows it exactly once with a ready-to-paste config snippet →
   renewals reset quotas → cancellations revoke keys.

## What subscribers put in their config

```toml
[providers.hyperfurion]
api_key = "hfk_..."

[stt]
provider = "hyperfurion"

[tts]
provider = "hyperfurion"
```

## Tests

```bash
pip install aiohttp pytest pytest-asyncio
python3 -m pytest relay/tests -q
```

The suite runs a fake xAI upstream and drives the daemon's own streaming
STT and TTS clients through a live relay — auth, byte-exact proxying,
metering, mid-session cutoff, Stripe signature verification, the full
checkout→welcome→working-key flow, and 30-day quota rollover.

## Operator's honesty notes

- Subscriber audio transits this relay to xAI. It is never written to
  disk, but you become a data processor — say so wherever you sell this,
  and publish a privacy statement before charging strangers.
- If the relay is down, subscribers' dictation is down. Keep the
  bring-your-own-key providers first-class; this is a convenience tier,
  not a lock-in.
