"""The personal dictionary: corrections mined from the opt-in ledger.

Every re-dictation shortly after a previous attempt is a labeled pair —
what the engine heard vs what the user meant. `voice-keyboard learned`
mines the history ledger for those pairs; NOTHING applies until the user
accepts an entry. Accepted overrides merge into the grammar vocabulary at
the next recording start ([flow] personal_dictionary); accepted hotwords
feed the STT biasing context. Everything lives in
~/.local/state/voice-keyboard/dictionary.json (mode 600, same posture as
the ledger) — none of it ever leaves the machine.
"""

import json
import logging
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from voice_keyboard.history import _state_dir

logger = logging.getLogger(__name__)

# A re-dictation this soon after the previous entry is treated as a
# correction attempt; anything later is just new dictation.
MINE_WINDOW_S = 90.0
# A correction touches a word or two; instructed rewrites touch more. The
# budget is absolute AND relative (at most half the utterance) so short
# three-word repairs mine while different-sentence pairs do not.
MAX_CHANGED_TOKENS = 2
HOTWORD_MIN_COUNT = 3

_WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)


def dictionary_path() -> Path:
    return _state_dir() / "dictionary.json"


def _empty() -> dict:
    return {"overrides": {}, "hotwords": [], "rejected": [], "macros": {}}


def load_dictionary() -> dict:
    """Best-effort load; a missing or corrupt file is an empty dictionary."""
    try:
        with open(dictionary_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    overrides = data.get("overrides")
    hotwords = data.get("hotwords")
    rejected = data.get("rejected")
    macros = data.get("macros")
    return {
        "overrides": {
            str(k): str(v)
            for k, v in (overrides or {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        if isinstance(overrides, dict)
        else {},
        "hotwords": [str(w) for w in hotwords if isinstance(w, str)]
        if isinstance(hotwords, list)
        else [],
        "rejected": [str(r) for r in rejected if isinstance(r, str)]
        if isinstance(rejected, list)
        else [],
        "macros": {
            str(k).strip().casefold(): str(v)
            for k, v in (macros or {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        if isinstance(macros, dict)
        else {},
    }


def save_dictionary(data: dict) -> None:
    path = dictionary_path()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.chmod(path, 0o600)


def vocabulary_overrides() -> dict:
    """Accepted spoken→replacement pairs, for the grammar merge."""
    return dict(load_dictionary().get("overrides") or {})


def hotwords() -> list[str]:
    """Accepted hotwords, for the STT biasing context."""
    return list(load_dictionary().get("hotwords") or [])


def macro_text(name: str) -> Optional[str]:
    """The saved macro body for a spoken name; None when unknown."""
    key = name.strip().strip(".,!?").casefold()
    if not key:
        return None
    return load_dictionary()["macros"].get(key)


MACRO_MIN_COUNT = 3
MACRO_MIN_WORDS = 4


def mine_macros(entries: list[dict]) -> list[tuple[str, int]]:
    """Texts dictated verbatim again and again — procedural memory
    candidates. Nothing becomes a macro until the user names it."""
    counts: dict[str, int] = {}
    surface: dict[str, str] = {}
    for entry in entries:
        if str(entry.get("register", "")) in {"intent", "macro", "ask"}:
            continue
        text = str(entry.get("text", "")).strip()
        tokens = _tokens(text)
        if len(tokens) < MACRO_MIN_WORDS:
            continue
        key = " ".join(t.casefold() for t in tokens)
        counts[key] = counts.get(key, 0) + 1
        surface.setdefault(key, text)
    return sorted(
        ((surface[key], count) for key, count in counts.items() if count >= MACRO_MIN_COUNT),
        key=lambda item: (-item[1], item[0]),
    )


def open_macro_candidates(entries: list[dict]) -> list[tuple[str, int]]:
    data = load_dictionary()
    known = set(data["macros"].values())
    rejected = set(data["rejected"])
    return [
        (text, count)
        for text, count in mine_macros(entries)
        if text not in known and candidate_key("macro", text) not in rejected
    ]


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def candidate_key(heard: str, meant: str) -> str:
    return f"{heard} -> {meant}"


def mine_corrections(entries: list[dict]) -> list[tuple[str, str, int]]:
    """(heard, meant, count) candidates from consecutive re-dictations.

    A pair of ledger entries counts as a correction when the second landed
    within MINE_WINDOW_S of the first, in the same app, and differs in at
    most MAX_CHANGED_TOKENS same-position words. Intent entries are
    commands, not speech — skipped.
    """
    counts: dict[tuple[str, str], int] = {}
    skip = {"intent", "macro", "ask"}
    for a, b in zip(entries, entries[1:]):
        if skip & {str(a.get("register", "")), str(b.get("register", ""))}:
            continue
        try:
            dt = float(b.get("ts", 0)) - float(a.get("ts", 0))
        except (TypeError, ValueError):
            continue
        if not 0 <= dt <= MINE_WINDOW_S:
            continue
        if str(a.get("app", "")) != str(b.get("app", "")):
            continue
        heard_tokens = _tokens(str(a.get("text", "")))
        meant_tokens = _tokens(str(b.get("text", "")))
        if not heard_tokens or not meant_tokens:
            continue
        matcher = SequenceMatcher(
            None,
            [t.casefold() for t in heard_tokens],
            [t.casefold() for t in meant_tokens],
        )
        changed: list[tuple[str, str]] = []
        diff_budget = 0
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                continue
            diff_budget += max(i2 - i1, j2 - j1)
            if op == "replace" and (i2 - i1) == (j2 - j1):
                changed.extend(zip(heard_tokens[i1:i2], meant_tokens[j1:j2]))
        if not changed or diff_budget > MAX_CHANGED_TOKENS:
            continue
        if diff_budget * 2 > max(len(heard_tokens), len(meant_tokens)):
            continue
        for heard, meant in changed:
            if heard.casefold() == meant.casefold():
                continue
            if len(heard) < 2 or len(meant) < 2:
                continue
            key = (heard.casefold(), meant)
            counts[key] = counts.get(key, 0) + 1
    return sorted(
        ((heard, meant, count) for (heard, meant), count in counts.items()),
        key=lambda item: (-item[2], item[0], item[1]),
    )


def mine_hotwords(entries: list[dict]) -> list[tuple[str, int]]:
    """Recurring distinctive tokens (names, identifiers) worth biasing the
    STT toward: capitalized mid-sentence, CamelCase, or digit-bearing."""
    counts: dict[str, int] = {}
    for entry in entries:
        if str(entry.get("register", "")) in {"intent", "macro", "ask"}:
            continue
        tokens = _tokens(str(entry.get("text", "")))
        for index, token in enumerate(tokens):
            if len(token) < 3:
                continue
            distinctive = (
                (index > 0 and token[:1].isupper())
                or any(ch.isupper() for ch in token[1:])
                or any(ch.isdigit() for ch in token)
            )
            if distinctive:
                counts[token] = counts.get(token, 0) + 1
    return sorted(
        ((token, count) for token, count in counts.items() if count >= HOTWORD_MIN_COUNT),
        key=lambda item: (-item[1], item[0]),
    )


def open_candidates(entries: list[dict]) -> list[tuple[str, str, int]]:
    """Mined corrections that are neither accepted nor rejected yet."""
    data = load_dictionary()
    accepted = {k.casefold() for k in data["overrides"]}
    rejected = set(data["rejected"])
    return [
        (heard, meant, count)
        for heard, meant, count in mine_corrections(entries)
        if heard.casefold() not in accepted
        and candidate_key(heard, meant) not in rejected
    ]


def open_hotword_candidates(entries: list[dict]) -> list[tuple[str, int]]:
    data = load_dictionary()
    known = {w.casefold() for w in data["hotwords"]}
    rejected = set(data["rejected"])
    return [
        (token, count)
        for token, count in mine_hotwords(entries)
        if token.casefold() not in known and candidate_key("hotword", token) not in rejected
    ]
