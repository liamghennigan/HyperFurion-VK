"""The dictation ledger: an opt-in, local-only record of what was typed.

Off by default ([flow] history = true enables it). Entries are appended
as JSON lines to ~/.local/state/voice-keyboard/history.jsonl (or under
$XDG_STATE_HOME), file mode 600, directory 700 — same posture as the
config file. `voice-keyboard history` lists entries; `voice-keyboard
recall N` re-types one.
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_READ_BYTES = 4 * 1024 * 1024  # read at most the trailing 4 MiB


def _state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        return Path(xdg) / "voice-keyboard"
    return Path.home() / ".local" / "state" / "voice-keyboard"


def history_path() -> Path:
    return _state_dir() / "history.jsonl"


def append_entry(text: str, *, app: str = "", register: str = "") -> None:
    """Best-effort append; never raises into the dictation path."""
    if not text:
        return
    try:
        path = history_path()
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        entry = {
            "ts": round(time.time(), 3),
            "app": app,
            "register": register,
            "text": text,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        logger.exception("Could not append to dictation history")


def last_entries(count: int = 10) -> list[dict]:
    """The most recent `count` entries, newest last."""
    path = history_path()
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > MAX_READ_BYTES:
                f.seek(size - MAX_READ_BYTES)
                f.readline()  # skip the partial line
            raw_lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []

    entries: list[dict] = []
    for line in raw_lines[-max(1, count):]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("text"):
            entries.append(entry)
    return entries
