"""SQLite store for subscribers, usage metering, and one-time key pickup.

SQLite is deliberate: a relay for hundreds of subscribers sees a few
requests per second at peak, and a single WAL-mode file beats operating
a database server. All access goes through one lock; calls are
short-lived and never block the event loop for meaningful time.
"""

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from typing import Callable, Optional

PERIOD_SECONDS = 30 * 24 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    key_hint TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    stripe_customer_id TEXT NOT NULL DEFAULT '',
    stripe_subscription_id TEXT NOT NULL DEFAULT '',
    period_start REAL NOT NULL,
    stt_seconds_used REAL NOT NULL DEFAULT 0,
    tts_chars_used INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_keys (
    session_id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_codes (
    email TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    sent_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS demo_usage (
    day TEXT NOT NULL,
    ip TEXT NOT NULL,
    dictations INTEGER NOT NULL DEFAULT 0,
    tts INTEGER NOT NULL DEFAULT 0,
    asks INTEGER NOT NULL DEFAULT 0,
    spent_usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, ip)
);
"""


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_key() -> str:
    return "hfk_" + secrets.token_hex(20)


class Store:
    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- subscribers ------------------------------------------------------

    def create_user(
        self,
        tier: str,
        email: str = "",
        stripe_customer_id: str = "",
        stripe_subscription_id: str = "",
    ) -> tuple[int, str]:
        """Create a subscriber; returns (user_id, plaintext_key).

        The plaintext key exists only in the return value — the store
        keeps a SHA-256 hash and a 4-char hint for the admin listing.
        """
        api_key = generate_key()
        now = self._clock()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO users (key_hash, key_hint, email, tier, status,"
                " stripe_customer_id, stripe_subscription_id, period_start, created_at)"
                " VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                (
                    hash_key(api_key),
                    api_key[-4:],
                    email,
                    tier,
                    stripe_customer_id,
                    stripe_subscription_id,
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return int(cur.lastrowid), api_key

    def lookup_key(self, api_key: str) -> Optional[dict]:
        """Resolve a bearer key to an active-period user row (or None).

        Rolls the usage window forward if the stored period has lapsed,
        so quota checks against the returned row are always current.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE key_hash = ?", (hash_key(api_key),)
            ).fetchone()
        if row is None:
            return None
        return self._maybe_roll_period(dict(row))

    def _maybe_roll_period(self, user: dict) -> dict:
        now = self._clock()
        if now < user["period_start"] + PERIOD_SECONDS:
            return user
        # Advance in whole periods so the anchor day stays stable.
        periods = int((now - user["period_start"]) // PERIOD_SECONDS)
        new_start = user["period_start"] + periods * PERIOD_SECONDS
        with self._lock:
            self._conn.execute(
                "UPDATE users SET period_start = ?, stt_seconds_used = 0,"
                " tts_chars_used = 0 WHERE id = ?",
                (new_start, user["id"]),
            )
            self._conn.commit()
        user.update(period_start=new_start, stt_seconds_used=0.0, tts_chars_used=0)
        return user

    def add_usage(self, user_id: int, stt_seconds: float = 0.0, tts_chars: int = 0) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET stt_seconds_used = stt_seconds_used + ?,"
                " tts_chars_used = tts_chars_used + ? WHERE id = ?",
                (stt_seconds, tts_chars, user_id),
            )
            self._conn.commit()

    def reset_usage_for_subscription(self, subscription_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET stt_seconds_used = 0, tts_chars_used = 0,"
                " period_start = ? WHERE stripe_subscription_id = ?",
                (self._clock(), subscription_id),
            )
            self._conn.commit()

    def set_status_for_subscription(self, subscription_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET status = ? WHERE stripe_subscription_id = ?",
                (status, subscription_id),
            )
            self._conn.commit()

    def set_status(self, user_id: int, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET status = ? WHERE id = ?", (status, user_id)
            )
            self._conn.commit()

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, key_hint, email, tier, status, period_start,"
                " stt_seconds_used, tts_chars_used, created_at FROM users ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    # -- anonymous landing-page demo metering ------------------------------
    # Rows are keyed (day, ip); the ip='' row aggregates the whole day and
    # is what the global budget reads.

    def demo_day(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self._clock()))

    def demo_counts(self, ip: str) -> dict:
        day = self.demo_day()
        with self._lock:
            row = self._conn.execute(
                "SELECT dictations, tts, asks, spent_usd FROM demo_usage"
                " WHERE day = ? AND ip = ?",
                (day, ip),
            ).fetchone()
        if row is None:
            return {"dictations": 0, "tts": 0, "asks": 0, "spent_usd": 0.0}
        return dict(row)

    def _demo_rows(self, ip: str) -> tuple[str, ...]:
        # The per-IP row plus the global-aggregate (ip="") row, deduped so a
        # request whose resolved ip is itself "" can never double-apply.
        return tuple(dict.fromkeys((ip, "")))

    def demo_record(self, ip: str, kind: str, usd: float) -> None:
        assert kind in {"dictations", "tts", "asks"}
        day = self.demo_day()
        with self._lock:
            for row_ip in self._demo_rows(ip):
                self._conn.execute(
                    "INSERT INTO demo_usage (day, ip) VALUES (?, ?)"
                    " ON CONFLICT(day, ip) DO NOTHING",
                    (day, row_ip),
                )
                self._conn.execute(
                    f"UPDATE demo_usage SET {kind} = {kind} + 1,"
                    " spent_usd = spent_usd + ? WHERE day = ? AND ip = ?",
                    (usd, day, row_ip),
                )
            self._conn.commit()

    def demo_try_charge(
        self, ip: str, kind: str, usd: float, ip_cap: int, budget_usd: float
    ) -> str:
        """Atomically check the global budget + per-IP cap and, if both pass,
        record the charge. Returns "" on success, or a short refusal reason
        ("budget" | "ip-cap"). The whole check-and-record runs under one lock
        acquisition, so concurrent requests cannot each pass a stale check and
        collectively overshoot (the demo half of the quota-race fix)."""
        assert kind in {"dictations", "tts", "asks"}
        day = self.demo_day()
        with self._lock:
            grow = self._conn.execute(
                "SELECT spent_usd FROM demo_usage WHERE day = ? AND ip = ''", (day,)
            ).fetchone()
            spent = grow["spent_usd"] if grow else 0.0
            if spent + usd > budget_usd:
                return "budget"
            irow = self._conn.execute(
                f"SELECT {kind} AS c FROM demo_usage WHERE day = ? AND ip = ?", (day, ip)
            ).fetchone()
            if (irow["c"] if irow else 0) >= ip_cap:
                return "ip-cap"
            for row_ip in self._demo_rows(ip):
                self._conn.execute(
                    "INSERT INTO demo_usage (day, ip) VALUES (?, ?)"
                    " ON CONFLICT(day, ip) DO NOTHING",
                    (day, row_ip),
                )
                self._conn.execute(
                    f"UPDATE demo_usage SET {kind} = {kind} + 1,"
                    " spent_usd = spent_usd + ? WHERE day = ? AND ip = ?",
                    (usd, day, row_ip),
                )
            self._conn.commit()
            return ""

    def demo_adjust_spend(self, ip: str, usd_delta: float) -> None:
        """Add usd_delta (may be negative) to the spend counters without
        touching the request counts. Used to reconcile an STT session's
        reserved (max) charge down to actual duration at close."""
        day = self.demo_day()
        with self._lock:
            for row_ip in self._demo_rows(ip):
                self._conn.execute(
                    "UPDATE demo_usage SET spent_usd = MAX(0, spent_usd + ?)"
                    " WHERE day = ? AND ip = ?",
                    (usd_delta, day, row_ip),
                )
            self._conn.commit()

    # -- one-time key pickup after Stripe checkout ------------------------

    def put_pending_key(self, session_id: str, api_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO pending_keys (session_id, api_key, created_at)"
                " VALUES (?, ?, ?)",
                (session_id, api_key, self._clock()),
            )
            self._conn.commit()

    def pop_pending_key(self, session_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT api_key FROM pending_keys WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "DELETE FROM pending_keys WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
        return str(row["api_key"])

    # -- email-verified self-serve login ----------------------------------
    # The key stops being a secret the user must guard: identity is the
    # subscription email (already captured from Stripe), proven by a one-time
    # code, and the key is (re)issued on demand. Losing a key is a non-event.

    def user_by_email(self, email: str) -> Optional[dict]:
        """The active subscriber for an email (case-insensitive), or None.

        If someone somehow has two rows for one email, the newest active one
        wins — that's the subscription they'd expect to sign into.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE lower(email) = lower(?) AND status = 'active'"
                " ORDER BY id DESC LIMIT 1",
                (email,),
            ).fetchone()
        return dict(row) if row else None

    def put_login_code(
        self,
        email: str,
        code: str,
        ttl_seconds: float,
        resend_min_interval: float,
    ) -> str:
        """Store a fresh one-time code for an email. Returns "" if stored, or
        "rate" if a code was issued too recently (throttles email spam). A new
        code always supersedes any prior one and resets the attempt counter."""
        now = self._clock()
        with self._lock:
            prior = self._conn.execute(
                "SELECT sent_at FROM login_codes WHERE lower(email) = lower(?)",
                (email,),
            ).fetchone()
            if prior is not None and now - prior["sent_at"] < resend_min_interval:
                return "rate"
            self._conn.execute(
                "INSERT OR REPLACE INTO login_codes"
                " (email, code_hash, expires_at, sent_at, attempts)"
                " VALUES (?, ?, ?, ?, 0)",
                (email.lower(), hash_key(code), now + ttl_seconds, now),
            )
            self._conn.commit()
        return ""

    def check_login_code(self, email: str, code: str, max_attempts: int = 5) -> bool:
        """Validate and CONSUME a one-time code. A correct code is deleted (so
        it can't be replayed); a wrong code burns one of the limited attempts;
        an expired or exhausted code is discarded. Constant-time compare."""
        now = self._clock()
        with self._lock:
            row = self._conn.execute(
                "SELECT code_hash, expires_at, attempts FROM login_codes"
                " WHERE lower(email) = lower(?)",
                (email,),
            ).fetchone()
            if row is None:
                return False
            if now > row["expires_at"] or row["attempts"] >= max_attempts:
                self._conn.execute(
                    "DELETE FROM login_codes WHERE lower(email) = lower(?)", (email,)
                )
                self._conn.commit()
                return False
            if hmac.compare_digest(row["code_hash"], hash_key(code)):
                self._conn.execute(
                    "DELETE FROM login_codes WHERE lower(email) = lower(?)", (email,)
                )
                self._conn.commit()
                return True
            self._conn.execute(
                "UPDATE login_codes SET attempts = attempts + 1"
                " WHERE lower(email) = lower(?)",
                (email,),
            )
            self._conn.commit()
            return False

    def rotate_key(self, user_id: int) -> str:
        """Issue a fresh key for a user and return the plaintext. The old key
        stops authenticating immediately (only its hash was ever stored)."""
        api_key = generate_key()
        with self._lock:
            self._conn.execute(
                "UPDATE users SET key_hash = ?, key_hint = ? WHERE id = ?",
                (hash_key(api_key), api_key[-4:], user_id),
            )
            self._conn.commit()
        return api_key
