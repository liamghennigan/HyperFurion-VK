"""Hands-free navigation: spoken caret commands and the chords they press.

    go left three words        select previous word       delete next word
    go to end of line          select all / select line   delete line
    move up two lines          press tab / press escape twice

`parse_nav` reads one command at a token index (pure, deterministic — the
grammar's prefix property holds). `chords_for` turns a command into the
key chords for the platform and the kind of app: editors and prose fields
share one map on Linux and Windows, and macOS has its own (option+arrows
by word, command+arrows to the ends of a line or the document); terminals
differ (readline on Linux: alt+b / ctrl+a / ctrl+w; on macOS the word
motions go as Esc b / Esc f, which readline reads as Meta whether or not
Option is set to send it; Windows Terminal, conhost and PSReadLine take
ctrl+left / home / ctrl+backspace, and ctrl+a would select all there).
Selection is refused in terminals — there is no text selection to extend.

Enter is never a navigation key: no command produces it, and user
overrides that try (enter, return, kpenter, ctrl+j, ctrl+m) are dropped.
Pressing Enter stays a human act.
"""

import logging
import sys
from typing import Optional

from voice_keyboard.flow.numbers import _TENS, _UNITS

logger = logging.getLogger(__name__)

MAX_COUNT = 20
PENDING = "pending"

_STRIP = ".,!?;:"

VERBS = {"go": "move", "move": "move", "select": "select", "delete": "delete", "press": "press"}

# spoken direction -> (direction, needs an explicit unit)
_DIRECTIONS = {
    "left": ("left", False), "right": ("right", False),
    "back": ("left", False), "backward": ("left", False), "backwards": ("left", False),
    "forward": ("right", False), "forwards": ("right", False),
    "up": ("up", False), "down": ("down", False),
    "previous": ("left", True), "last": ("left", True), "next": ("right", True),
}

_UNIT_WORDS = {
    "char": "char", "chars": "char", "character": "char", "characters": "char",
    "letter": "char", "letters": "char",
    "word": "word", "words": "word",
    "line": "line", "lines": "line",
}

_EDGES = {"start": "start", "beginning": "start", "top": "start", "end": "end", "bottom": "end"}
_EDGE_UNITS = {"line": "line", "document": "doc", "file": "doc", "page": "doc"}

# spoken key (one or two words) -> key name
PRESS_KEYS = {
    ("tab",): "tab", ("escape",): "escape", ("up",): "up", ("down",): "down",
    ("left",): "left", ("right",): "right", ("home",): "home", ("end",): "end",
    ("page", "up"): "pageup", ("page", "down"): "pagedown",
    ("backspace",): "backspace", ("back", "space"): "backspace",
    ("delete",): "delete", ("space",): "space",
}

_TIMES = {"times", "time", "x"}
_REPEAT_WORDS = {"once": 1, "twice": 2, "thrice": 3}

# Chords nothing may ever press: each one submits a line somewhere.
_FORBIDDEN_KEYS = {"enter", "return", "kpenter"}
# ctrl+o: readline's operate-and-get-next runs the line.
_FORBIDDEN_CHORDS = ({"ctrl", "j"}, {"ctrl", "m"}, {"ctrl", "o"})

# Key names every backend's press_combo knows (Linux uinput and Windows
# SendInput); single characters resolve on both too.
KNOWN_KEYS = {
    "ctrl", "control", "shift", "alt", "option", "super", "meta", "win", "cmd", "command",
    "tab", "esc", "escape", "space", "backspace", "delete", "del", "insert",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    *(f"f{n}" for n in range(1, 13)),
}


class _NeedMore(Exception):
    """The command ran into the end of the tokens it may read."""


def _count_of(core: str) -> Optional[int]:
    if core in ("a", "an"):
        return 1  # "go back a word"
    if core.isascii() and core.isdecimal():
        value = int(core)
    elif core in _UNITS or core in _TENS:
        value = _UNITS[core] if core in _UNITS else _TENS[core]
    else:
        return None
    return value if 1 <= value <= MAX_COUNT else None


def parse_nav(cores: list[str], index: int, *, decided: bool):
    """The navigation command starting at `index`, read from `cores`
    (casefolded, punctuation-stripped tokens; the caller slices them to
    the fence it must not cross).

    Returns (action, count, end) — action like "move:word:left",
    "select:all", "press:tab" — or PENDING when the tokens run out where
    the command could still continue and the tail is not `decided`, or
    None when this is not a command (the verb is just a word)."""

    def at(position: int) -> str:
        if position >= len(cores):
            raise _NeedMore
        return cores[position]

    def optional(position: int) -> Optional[str]:
        return cores[position] if position < len(cores) else None

    verb = VERBS.get(cores[index]) if index < len(cores) else None
    if verb is None:
        return None
    try:
        if verb == "press":
            return _parse_press(cores, index + 1, at, optional, decided)
        return _parse_motion(verb, index + 1, at, optional, decided, len(cores))
    except _NeedMore:
        return None if decided else PENDING


def _finish(action: str, count: int, end: int, total: int, decided: bool, *, more: bool):
    """A complete command; PENDING if it touches the tail and optional
    words (a count, a unit, "times") could still follow."""
    if more and end >= total and not decided:
        return PENDING
    return action, count, end


def _has_two_word_key(first: str) -> bool:
    return any(len(key) == 2 and key[0] == first for key in PRESS_KEYS)


def _parse_press(cores, cursor, at, optional, decided):
    first = at(cursor)
    second = optional(cursor + 1)
    if second is not None and (first, second) in PRESS_KEYS:
        key = PRESS_KEYS[(first, second)]
        cursor += 2
    elif second is None and not decided and _has_two_word_key(first):
        raise _NeedMore  # "page" may become "page up"
    elif (first,) in PRESS_KEYS:
        key = PRESS_KEYS[(first,)]
        cursor += 1
    else:
        return None
    action = f"press:{key}"
    word = optional(cursor)
    if word in _REPEAT_WORDS:
        return action, _REPEAT_WORDS[word], cursor + 1
    count = _count_of(word) if word is not None else None
    if count is None:
        return _finish(action, 1, cursor, len(cores), decided, more=True)
    cursor += 1
    if optional(cursor) in _TIMES:
        return action, count, cursor + 1
    return _finish(action, count, cursor, len(cores), decided, more=True)


def _parse_motion(verb, cursor, at, optional, decided, total):
    word = at(cursor)

    # select all / select (this) line / delete (the) line
    if verb == "select" and word == "all":
        return "select:all", 1, cursor + 1
    if verb in ("select", "delete"):
        probe = cursor + 1 if word in ("this", "the", "current") else cursor
        if at(probe) == "line":
            return f"{verb}:line:here", 1, probe + 1

    # go to (the) start/end of (the) line/document
    probe = cursor + 1 if word == "to" else cursor
    if at(probe) == "the":
        probe += 1
    edge = _EDGES.get(at(probe))
    if edge is not None:
        if verb == "delete" or at(probe + 1) != "of":
            return None
        probe += 2
        if at(probe) == "the":
            probe += 1
        unit = _EDGE_UNITS.get(at(probe))
        if unit is None:
            return None
        return f"{verb}:{unit}:{edge}", 1, probe + 1
    if word == "to":
        return None

    # go/select/delete (the) <direction> [count] [unit]
    if word == "the":
        cursor += 1
        word = at(cursor)
    spoken = _DIRECTIONS.get(word)
    if spoken is None:
        return None
    direction, needs_unit = spoken
    cursor += 1
    count = 1
    word = optional(cursor)
    counted = _count_of(word) if word is not None else None
    if counted is None and word is not None and (word.isdecimal() or word in _UNITS or word in _TENS):
        return None  # a count out of range: not a command
    if counted is not None:
        count = counted
        cursor += 1
        word = optional(cursor)
    unit = _UNIT_WORDS.get(word) if word is not None else None
    explicit = unit is not None
    if explicit:
        cursor += 1
    elif word is None and not decided and (needs_unit or counted is not None):
        raise _NeedMore  # "select previous" / "go left three" want a unit
    elif needs_unit:
        return None
    else:
        unit = "line" if direction in ("up", "down") else "char"
    if unit == "line" and direction in ("left", "right"):
        if not needs_unit:
            return None  # "go left line" is not a thing
        direction = "up" if direction == "left" else "down"
    if direction in ("up", "down") and unit != "line":
        return None
    if verb == "delete" and unit == "line":
        return None
    return _finish(
        f"{verb}:{unit}:{direction}", count, cursor, total, decided, more=not explicit
    )


# ------------------------------------------------------------------ keymaps

_EDITOR_MOVES = {
    "char:left": ["left"], "char:right": ["right"],
    "word:left": ["ctrl", "left"], "word:right": ["ctrl", "right"],
    "line:up": ["up"], "line:down": ["down"],
    "line:start": ["home"], "line:end": ["end"],
    "doc:start": ["ctrl", "home"], "doc:end": ["ctrl", "end"],
}

EDITOR: dict[str, Optional[list[list[str]]]] = {
    **{f"move:{key}": [chord] for key, chord in _EDITOR_MOVES.items()},
    **{f"select:{key}": [["shift", *chord]] for key, chord in _EDITOR_MOVES.items()},
    "select:all": [["ctrl", "a"]],
    "select:line:here": [["home"], ["shift", "end"]],
    "delete:char:left": [["backspace"]], "delete:char:right": [["delete"]],
    "delete:word:left": [["ctrl", "backspace"]], "delete:word:right": [["ctrl", "delete"]],
    "delete:line:here": [["home"], ["shift", "end"], ["backspace"]],
}

_NO_SELECTION = {key: None for key in EDITOR if key.startswith("select:")}

# Readline / zsh emacs keys: work in bash, zsh, python, psql, ...
LINUX_TERMINAL: dict[str, Optional[list[list[str]]]] = {
    **{key: value for key, value in EDITOR.items() if key.startswith("move:")},
    **_NO_SELECTION,
    "move:word:left": [["alt", "b"]], "move:word:right": [["alt", "f"]],
    "move:line:start": [["ctrl", "a"]], "move:line:end": [["ctrl", "e"]],
    "move:doc:start": None, "move:doc:end": None,
    # Up/down in a shell is history, not a line: "press up" says it plainly.
    "move:line:up": None, "move:line:down": None,
    "delete:char:left": [["backspace"]], "delete:char:right": [["delete"]],
    "delete:word:left": [["ctrl", "w"]], "delete:word:right": [["alt", "d"]],
    "delete:line:here": [["ctrl", "e"], ["ctrl", "u"]],
}

# Windows Terminal / conhost / PSReadLine (and WSL shells inside them).
WINDOWS_TERMINAL: dict[str, Optional[list[list[str]]]] = {
    **{key: value for key, value in EDITOR.items() if key.startswith("move:")},
    **_NO_SELECTION,
    "move:doc:start": None, "move:doc:end": None,
    "move:line:up": None, "move:line:down": None,
    "delete:char:left": [["backspace"]], "delete:char:right": [["delete"]],
    "delete:word:left": [["ctrl", "backspace"]], "delete:word:right": [["ctrl", "delete"]],
    "delete:line:here": None,
}

# macOS editors and text fields: words by option+arrow, line ends by
# command+arrow, the document by command+up/down; shift extends.
_MAC_MOVES = {
    "char:left": ["left"], "char:right": ["right"],
    "word:left": ["alt", "left"], "word:right": ["alt", "right"],
    "line:up": ["up"], "line:down": ["down"],
    "line:start": ["cmd", "left"], "line:end": ["cmd", "right"],
    "doc:start": ["cmd", "up"], "doc:end": ["cmd", "down"],
}
MAC_EDITOR: dict[str, Optional[list[list[str]]]] = {
    **{f"move:{key}": [chord] for key, chord in _MAC_MOVES.items()},
    **{f"select:{key}": [["shift", *chord]] for key, chord in _MAC_MOVES.items()},
    "select:all": [["cmd", "a"]],
    "select:line:here": [["cmd", "left"], ["shift", "cmd", "right"]],
    "delete:char:left": [["backspace"]], "delete:char:right": [["delete"]],
    "delete:word:left": [["alt", "backspace"]], "delete:word:right": [["alt", "delete"]],
    "delete:line:here": [["cmd", "left"], ["shift", "cmd", "right"], ["backspace"]],
}

# Terminal.app, iTerm2, Ghostty, Warp: readline as on Linux, except that
# the Meta chords go as an Escape prefix — readline reads "Esc b" as
# backward-word whether or not Option is set to send Meta.
MAC_TERMINAL: dict[str, Optional[list[list[str]]]] = {
    **LINUX_TERMINAL,
    "move:word:left": [["escape"], ["b"]], "move:word:right": [["escape"], ["f"]],
    "delete:word:right": [["escape"], ["d"]],
}

# Commands that leave a selection or a gap: the next dictated word replaces
# or fills it, so it starts glued (no leading space) — see FlowEngine.
# Moves to a line/document start glue too.
GLUED = ("select:", "delete:")
# Keys that likely land in another field: dictation after them starts
# fresh (no leading space, a capital in prose).
FRESH_FIELD = ("press:tab", "press:escape", "press:pageup", "press:pagedown")


def _forbidden(chord: list[str]) -> bool:
    names = {name.strip().lower() for name in chord}
    return bool(names & _FORBIDDEN_KEYS) or any(bad <= names for bad in _FORBIDDEN_CHORDS)


def parse_override(value) -> Optional[list[list[str]]]:
    """A config value — "ctrl+left", ["home", "shift+end"], or "" to
    disable — as a chord sequence; None when it disables. Raises
    ValueError for a chord that would press Enter."""
    if isinstance(value, str):
        value = [value] if value.strip() else []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("a nav key must be a chord string or a list of them")
    chords = [[part.strip().lower() for part in v.split("+") if part.strip()] for v in value]
    chords = [chord for chord in chords if chord]
    for chord in chords:
        if _forbidden(chord):
            raise ValueError(f"{'+'.join(chord)!r} would press Enter")
        unknown = [n for n in chord if len(n) != 1 and n not in KNOWN_KEYS]
        if unknown:
            raise ValueError(f"unknown key {unknown[0]!r}")
    return chords or None


def keymap(*, terminal: bool, platform: str = sys.platform, overrides: Optional[dict] = None):
    """The action -> chord-sequence map for this kind of app; None entries
    are refused. `overrides` is the [nav.keys.terminal] or
    [nav.keys.editor] table."""
    if terminal:
        table = dict(
            WINDOWS_TERMINAL if platform == "win32"
            else MAC_TERMINAL if platform == "darwin"
            else LINUX_TERMINAL
        )
    else:
        table = dict(MAC_EDITOR if platform == "darwin" else EDITOR)
    for action, value in (overrides or {}).items():
        try:
            table[str(action)] = parse_override(value)
        except ValueError as exc:
            logger.warning("nav: ignoring [nav.keys] %s: %s", action, exc)
    return table


def chords_for(action: str, count: int, table: dict) -> Optional[list[list[str]]]:
    """The full chord sequence for a command (repeated `count` times), or
    None when this kind of app has no binding for it. Presses are capped
    and Enter is never in the result, whatever the table says."""
    if action.startswith("press:"):
        key = action.split(":", 1)[1]
        sequence: Optional[list[list[str]]] = [[key]]
    else:
        sequence = table.get(action)
    if not sequence:
        return None
    if any(_forbidden(chord) for chord in sequence):
        return None
    repeat = 1 if action in ("select:all",) or action.endswith(":here") else count
    return [list(chord) for _ in range(max(1, min(MAX_COUNT, repeat))) for chord in sequence]
