"""One memory for the whole system.

The assistant keeps its own durable memories and interaction log (SQLite),
and it also reads the daemon's dictation ledger — so a single `recall`
spans everything you have ever said, typed or spoken. That fold-in is the
merge: the keyboard's history and the assistant's memory are one
substrate, searched the same way (keyword, or semantic when [recall]
points at an embeddings endpoint).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from voice_keyboard import history, recall
from voice_keyboard.assistant.models import ContextChunk
from voice_keyboard.history import _state_dir


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    kind: str
    text: str
    source: str
    created_at: str
    score: float = 0.0


_STOP_WORDS = {
    "about", "after", "again", "also", "and", "are", "can", "could", "for",
    "from", "how", "into", "just", "like", "need", "should", "that", "the",
    "this", "what", "when", "where", "with", "would", "you", "your",
}
_REMEMBER_PATTERNS = [
    re.compile(r"\bremember(?: this| that)?:?\s*(?P<text>.+)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bplease remember(?: this| that)?:?\s*(?P<text>.+)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bmy preference is (?P<text>.+)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bi prefer (?P<text>.+)", re.IGNORECASE | re.DOTALL),
]


def memory_db_path() -> Path:
    return _state_dir() / "assistant-memory.sqlite3"


def extract_memory_candidate(text: str) -> Optional[str]:
    cleaned = text.strip()
    for pattern in _REMEMBER_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            candidate = " ".join(match.group("text").strip().split())
            return candidate.rstrip(".") or None
    return None


def _terms(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zA-Z0-9_]{3,}", text.lower()) if t not in _STOP_WORDS]


class AssistantMemory:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or memory_db_path()
        self.db_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL, text TEXT NOT NULL,
                    source TEXT NOT NULL, created_at TEXT NOT NULL)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_text TEXT NOT NULL, assistant_text TEXT NOT NULL,
                    created_at TEXT NOT NULL)"""
            )
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    def remember(self, text: str, *, kind: str = "fact", source: str = "user") -> Optional[MemoryRecord]:
        cleaned = " ".join(text.strip().split())
        if not cleaned:
            return None
        # created_at is passed in from the caller's clock (the daemon), so
        # this module never calls time.* — keeps it deterministic to test.
        created_at = _now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO memories(kind, text, source, created_at) VALUES (?, ?, ?, ?)",
                (kind, cleaned, source, created_at),
            )
            memory_id = int(cursor.lastrowid or 0)
        return MemoryRecord(memory_id, kind, cleaned, source, created_at)

    def log_interaction(self, user_text: str, assistant_text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO interactions(user_text, assistant_text, created_at) VALUES (?, ?, ?)",
                (user_text.strip(), assistant_text.strip(), _now_iso()),
            )

    def list_recent(self, limit: int = 20) -> list[MemoryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def search(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        terms = _terms(query)
        if not terms:
            return self.list_recent(limit)
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM memories ORDER BY id DESC LIMIT 500").fetchall()
        scored: list[MemoryRecord] = []
        for row in rows:
            text = str(row["text"]).lower()
            score = sum(text.count(term) for term in terms)
            if score:
                rec = _row_to_record(row)
                scored.append(
                    MemoryRecord(rec.id, rec.kind, rec.text, rec.source, rec.created_at, float(score))
                )
        scored.sort(key=lambda item: (item.score, item.id), reverse=True)
        return scored[:limit]

    def relevant_chunks(
        self, query: str, limit: int, *, config: Optional[dict] = None
    ) -> list[ContextChunk]:
        """The unified recall: durable memories PLUS dictation-ledger hits.

        Memories come first (explicit, durable); ledger entries fold in so
        the brain can recall anything you dictated. Ledger search reuses
        the daemon's recall (keyword, or semantic when configured)."""
        chunks: list[ContextChunk] = []
        for record in self.search(query, limit):
            chunks.append(
                ContextChunk(
                    kind="memory",
                    title=f"Memory {record.id}",
                    uri=f"memory:{record.id}",
                    text=record.text,
                    score=record.score,
                )
            )
        try:
            entries = history.last_entries(500)
            embedder = recall.create_embedder(config or {})
            for hit in recall.search(entries, query, embedder=embedder, limit=limit):
                chunks.append(
                    ContextChunk(
                        kind="interaction",
                        title="Dictation ledger",
                        uri=f"ledger:{hit.get('ts', 0)}",
                        text=str(hit.get("text", "")),
                        score=float(hit.get("score", 0.0)),
                    )
                )
        except Exception:  # the ledger fold-in is best-effort context
            pass
        return chunks


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=int(row["id"]),
        kind=str(row["kind"]),
        text=str(row["text"]),
        source=str(row["source"]),
        created_at=str(row["created_at"]),
    )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
