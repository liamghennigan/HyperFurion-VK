"""SQLite store for subscribers, usage metering, and one-time key pickup.

SQLite is deliberate: a relay for hundreds of subscribers sees a few
requests per second at peak, and a single WAL-mode file beats operating
a database server. All access goes through one lock; calls are
short-lived and never block the event loop for meaningful time.
"""

import hashlib
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
