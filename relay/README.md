# HyperFurion relay

> **The hosted subscription is not on sale right now.** The hosted relay
> serves existing subscribers only, and nothing in this README is an offer.
> It is operator documentation: how the relay meters and authenticates, how
> to run your own, and how billing is wired up should sales reopen.

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

These are the tiers the relay meters, as defined in
`hyperfurion_relay/tiers.py`. They are not on sale right now: the prices are
what the caps were derived from, and what Stripe checkout amounts map to on
a relay that sells them, not a current offer.

| Tier  | Price the caps assume | Streaming STT | TTS characters | Worst-case upstream cost |
|-------|-----------------------|---------------|----------------|--------------------------|
| basic | $5/month              | 20 h/month    | 10,000/month   | ~$4.15 (median user ~$1) |
| pro   | $10/month             | 40 h/month    | 50,000/month   | ~$8.75 (median ~$2)      |

Caps are hard: STT refuses (and cuts off mid-session) past the limit, TTS
returns 429, both with a message naming the reset date. A subscriber cannot
run up your bill — the caps are derived from xAI list prices (streaming STT
$0.20/h, TTS $15/M chars, verified 2026-07-04) so a maxed-out month never
exceeds what the subscription nets after Stripe fees. Re-derive in
`hyperfurion_relay/tiers.py` whenever xAI pricing changes.

## Endpoints

- `WS /v1/stt` — streaming STT, proxied to `wss://api.x.ai/v1/stt`
- `POST /v1/tts` — TTS, proxied to `https://api.x.ai/v1/tts`
- `GET /v1/usage` — quota status for the presented key
- `GET /healthz` — liveness + tier catalog
- `POST /stripe/webhook` — signature-verified Stripe events
- `POST /auth/request` — email a 6-digit sign-in code to a subscriber
  (generic 200 either way — no user enumeration; 30s resend throttle)
- `POST /auth/verify` — redeem the code; (re)issues that subscriber's key
- `GET /welcome?session_id=…` — landing after checkout (points at login)

Auth everywhere: `Authorization: Bearer hfk_…`. Keys are stored as SHA-256
hashes; the plaintext exists only in the moment it is issued.

**Seamless login (no key to lose).** The hosted service is meant to need
no setup, so the key is never a secret the subscriber must save. Identity
is the Stripe email; `voice-keyboard login <email>` proves it with a
one-time code and writes the (re)issued key into `config.toml`. Lose a key
or move machines → just log in again. Sending the code needs an email
transport, pluggable and env-driven (see the config table): set
`RESEND_API_KEY` (recommended) or `SMTP_HOST`+creds. With neither set, the
code is logged at WARNING so the flow still works in dev.

## Landing-page demo endpoints

The landing page recognizes speech with a model in the visitor's tab and
never calls the relay on its own. Its "opt in" sheet (in the privacy
section) is the only way in: each endpoint below is reached only when the
visitor presses that sheet's button for it. It is a keyless demo surface —
real xAI engines, defended in depth instead of authenticated:

- `GET /v1/demo/status` — "check the relay": liveness, caps, and
  served-today counts, which the sheet shows; only a live answer unlocks
  the other controls
- `WS /v1/demo/stt` — the sheet's switch sends the visitor's next
  dictation here, then turns itself off: streaming Grok STT, hard-capped
  at 20 s per dictation (finalized mid-stream at the cap, not dropped)
- `POST /v1/demo/ask` — the sheet's question box: Q&A via Grok chat from a
  short fact sheet (`DOCS_CONTEXT` in `hyperfurion_relay/demo.py`), bounded
  completion
- `POST /v1/demo/tts` — "hear it in xAI's voice": Grok `eve`, text
  truncated to 220 chars, the voice is not client-selectable

Three layers keep it un-abusable: a **global daily budget** in USD
(`DEMO_DAILY_BUDGET_USD`, default $1 — worst case ≈ $30/month, period),
per-IP daily counters (8 dictations / 12 voice lines / 15 questions), and
the per-request size caps above. When the budget is spent, everything
refuses with a reason: the sheet says the hosted demo is unavailable, and
the page keeps using the model in the tab. If a relay dictation fails
midway, the page stops, keeps what was typed, and offers the in-tab model;
it never switches to another engine on its own. Demo responses send
`Access-Control-Allow-Origin: *` — they are public and rate-limited by
design.

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

A small VPS is plenty — a few hundred subscribers is a few requests per
second at peak.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `XAI_API_KEY` | *(required)* | master upstream key |
| `RELAY_DB` | `relay.db` | SQLite path (WAL mode) |
| `STRIPE_WEBHOOK_SECRET` | *(empty)* | webhook signature secret |
| `RESEND_API_KEY` | *(empty)* | primary sign-in-code email transport (Resend) |
| `EMAIL_FROM` | `HyperFurion VK <login@hyperfurion.com>` | From: on the sign-in email (use a verified sender/domain) |
| `SMTP_HOST` / `SMTP_PORT` | *(empty)* / `587` | fallback email transport (STARTTLS) |
| `SMTP_USER` / `SMTP_PASS` | *(empty)* | SMTP auth, if the host needs it |
| `UPSTREAM_STT_URL` | `wss://api.x.ai/v1/stt` | override for tests/self-host |
| `UPSTREAM_TTS_URL` | `https://api.x.ai/v1/tts` | override for tests/self-host |
| `UPSTREAM_CHAT_URL` | `https://api.x.ai/v1/chat/completions` | for the demo `ask` |
| `DEMO_DAILY_BUDGET_USD` | `1.0` | global daily cap on demo spend |
| `DEMO_CHAT_MODEL` | `grok-4-fast` | model behind the demo `ask` |
| `DEMO_TRUST_FORWARDED` | `` (off) | set to `1` only behind a trusted reverse proxy — then per-IP demo caps read `X-Forwarded-For`; otherwise the peer address is used so the header can't be spoofed to evade caps |
| `RELAY_HOST` / `RELAY_PORT` | `0.0.0.0` / `8787` | bind address |

## Issuing keys and billing (operators)

The hosted subscription is not on sale right now, and there is no public
checkout. This section is for operators: whoever runs a relay (the project
for its existing subscribers, or you on your own server) issues keys by
hand, and the Stripe wiring below is what the relay supports if a relay
ever sells subscriptions.

### Issuing keys by hand (no Stripe)

```bash
hyperfurion-relay-admin issue --tier basic --email someone@example.com
hyperfurion-relay-admin list
hyperfurion-relay-admin revoke --id 3
```

This is all a self-hosted relay for yourself or a small group needs.

### Stripe automation (if you sell subscriptions on your own relay)

1. In Stripe, create a **subscription product** per tier (the prices in
   [Tiers](#tiers)), then a **Payment Link** for each with (on your own
   relay, your domain in place of `api.hyperfurion.com`):
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

- A subscription sells convenience and funds the project — nothing
  else. Subscribers gain no abilities over bring-your-own-key users;
  every capability is open source and free forever. Say this plainly
  wherever a tier is sold. (The project's landing page and README say the
  hosted subscription isn't on sale right now.)

- Subscriber audio transits this relay to xAI. It is never written to
  disk, but you become a data processor — say so wherever you sell a
  subscription, and publish a privacy statement before charging strangers.
- If the relay is down, subscribers' dictation is down. Keep the
  bring-your-own-key providers first-class; this is a convenience tier,
  not a lock-in.
