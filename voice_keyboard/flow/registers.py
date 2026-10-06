"""Context registers: how dictation renders in the focused app.

A register bundles the rendering policy (capitalization, spacing, numbers)
plus injection details (which paste chord the app understands). The daemon
probes the focused app at recording start and resolves a register from
config; rendering itself is a pure left-to-right fold so a longer
transcript always renders with the previous render as a prefix — the
property the molten repair engine depends on.
"""

import re
from dataclasses import dataclass, replace
from typing import Optional

from voice_keyboard.flow.grammar import Item


@dataclass(frozen=True)
class Register:
    name: str
    smart_caps: bool = True        # capitalize sentence starts
    grammar_enabled: bool = True   # spoken punctuation / commands active
    numbers_on: bool = False       # convert spoken cardinals by default
    numbers_min: int = 10          # single-word conversion threshold
    paste_chord_shift: bool = False  # terminals paste with ctrl+shift+v
    compiler: str = ""             # semantic compiler key (flow/code.py)
    terminal: bool = False         # navigation uses the terminal's keys


PROSE = Register(name="prose", smart_caps=True, numbers_on=False)
TERMINAL = Register(
    name="terminal",
    smart_caps=False,
    numbers_on=True,
    numbers_min=0,
    paste_chord_shift=True,
    terminal=True,
)
VERBATIM = Register(name="verbatim", smart_caps=False, grammar_enabled=False)
PYTHON = Register(
    name="python",
    smart_caps=False,
    numbers_on=True,
    numbers_min=0,
    compiler="python",
)
SHELL = Register(
    name="shell",
    smart_caps=False,
    numbers_on=True,
    numbers_min=0,
    paste_chord_shift=True,
    compiler="shell",
    terminal=True,
)

JAVASCRIPT = Register(
    name="javascript",
    smart_caps=False,
    numbers_on=True,
    numbers_min=0,
    compiler="javascript",
)

REGISTERS = {r.name: r for r in (PROSE, TERMINAL, VERBATIM, PYTHON, SHELL, JAVASCRIPT)}

# App identifiers (AT-SPI application names, macOS app names, Windows exe
# basenames — lowercased) that default to the terminal register.
TERMINAL_APPS = {
    "gnome-terminal-server", "gnome-terminal", "kgx", "gnome-console",
    "kitty", "alacritty", "foot", "footclient", "konsole", "xterm",
    "urxvt", "rxvt", "tilix", "terminator", "wezterm", "wezterm-gui",
    "st", "sakura", "xfce4-terminal", "lxterminal", "eterm", "ptyxis",
    # macOS
    "terminal", "iterm2", "warp", "ghostty",
    # Windows (exe basenames without .exe)
    "windowsterminal", "cmd", "powershell", "pwsh", "conhost",
    "mintty", "hyper",
}

# Apps where Enter sends the message and Shift+Enter is a line break: a
# spoken "new line" there must not send half a message.
CHAT_APPS = {
    "slack", "discord", "teams", "ms-teams", "signal", "signal-desktop",
    "telegram-desktop", "telegram", "whatsapp", "element", "element-desktop",
    "mattermost", "mattermost-desktop", "zulip", "skype", "rocket.chat",
    "beeper", "wire", "threema", "session",
}


def is_chat_app(app: str, extra=()) -> bool:
    """Enter sends here; a line break must be Shift+Enter."""
    key = (app or "").strip().lower()
    key = key[:-4] if key.endswith(".exe") else key
    return bool(key) and (key in CHAT_APPS or key in {str(x).strip().lower() for x in extra})


# A lowercase recognizer's "i", "i'm", "i'll": the pronoun is always a capital.
# Days and months a lowercase recognizer writes small. Not "may" or
# "march" (verbs), nor "sun"/"wed" (words).
PROPER_WORDS = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday january february "
    "april june july august september october november december".split()
)
# Initialisms a lowercase recognizer writes small ("ok", "pdf", "asap").
# Never ones that are also words ("us", "it", "am", "it's").
ACRONYMS = frozenset(
    "ok tv usa uk faq pdf url api ai ceo eta asap fyi diy gps html css json sql usb "
    "eod eow kpi roi okr ui ux qa sdk cli crm saas pto".split()
)
_PRONOUN_I = re.compile(r"i(?:['\u2019](?:m|ll|d|ve))?[.,!?;:]*")

def resolve_register(name: str) -> Register:
    return REGISTERS.get(str(name).strip().lower(), PROSE)


def register_for_app(
    app: str,
    role: str,
    *,
    config_map: Optional[dict] = None,
    default: str = "prose",
) -> Register:
    """Pick a register for the focused app: a password widget always wins
    (verbatim — no smart rewriting inside a secret field), then the config
    map, then built-in terminal detection, then the configured default."""
    if (role or "").strip().lower() == "password text":
        return VERBATIM
    app_key = (app or "").strip().lower()
    exe_key = app_key[:-4] if app_key.endswith(".exe") else app_key
    for key in (app_key, exe_key):
        if config_map and key and key in config_map:
            return resolve_register(str(config_map[key]))
    if exe_key in TERMINAL_APPS or (role or "").strip().lower() == "terminal":
        return TERMINAL
    return resolve_register(default)


@dataclass(frozen=True)
class RenderState:
    """Forward-carried fold state. Frozen so the engine can snapshot it at
    segment boundaries and rewind on "scratch that"."""
    at_start: bool = True          # nothing rendered yet this session
    glue_next: bool = False        # suppress the space before the next atom
    capitalize_next: bool = True   # next word starts a sentence
    pending: str = ""              # semantic-compiler hold (dash, call-open, call)
    after_name: bool = False       # code: the last atom was a name ("f" before "(")
    open_calls: int = 0            # code: calls a spoken callable opened, not yet closed
    inner_parens: int = 0          # code: explicit parens opened inside those calls
    line_start: bool = False       # the caret is surely at the start of a line
    list_number: int = 0           # the last "new number" item typed (0 = none)
    last_atom: str = ""            # code: the last atom typed ("f" before a quote, "+" before "=")


def initial_state(register: Register) -> RenderState:
    return RenderState(capitalize_next=register.smart_caps)


def continuation_state(previous_tail: str, register: Register) -> Optional[RenderState]:
    """The state a recording starts in when it continues text the previous
    one left at the caret: a space before its first word, and a capital
    only if that text ended a sentence. None when there is nothing to
    continue (no text, or it ended in whitespace or a line break)."""
    tail = previous_tail[-1:] if previous_tail else ""
    if not tail or tail.isspace():
        return None
    return RenderState(
        at_start=False,
        glue_next=False,
        capitalize_next=register.smart_caps and tail in _SENTENCE_ENDERS,
        line_start=False,
    )


_SENTENCE_ENDERS = (".", "!", "?")


def _capitalized(text: str) -> str:
    for index, ch in enumerate(text):
        if ch.isalpha():
            return text[:index] + ch.upper() + text[index + 1:]
        if not ch.isdigit() and ch not in "\"'([{":
            break
    return text


def render_items(
    items: list[Item],
    state: RenderState,
    register: Register,
) -> tuple[str, RenderState]:
    """Render grammar items to text, returning the new carried state.

    Pure and associative over concatenation: render(a+b) ==
    render(a) + render_continue(b) — the prefix-stability property.

    In a terminal register a spoken line break renders as nothing: there,
    a line break is Enter, and Enter runs the command. Only a hand sends.
    """
    if register.terminal:
        items = [item for item in items if item.kind != "break"]
    if register.compiler:
        # Semantic registers compile speech; lazy import avoids a cycle.
        from voice_keyboard.flow.code import COMPILERS

        compiler = COMPILERS.get(register.compiler)
        if compiler is not None:
            return compiler(items, state, register)
    out: list[str] = []
    at_start = state.at_start
    glue_next = state.glue_next
    capitalize_next = state.capitalize_next
    line_start = state.line_start
    list_number = state.list_number

    def emit(text: str, *, glue_left: bool) -> None:
        nonlocal at_start, glue_next, line_start
        if not at_start and not glue_next and not glue_left:
            out.append(" ")
        out.append(text)
        at_start = False
        glue_next = False
        line_start = False

    for item in items:
        if item.kind == "break":
            if item.mode == "number":
                list_number += 1
                out.append(("" if line_start else "\n") + f"{list_number}. ")
            elif item.mode == "bullet":
                out.append(("" if line_start else "\n") + item.text)
                if item.text.startswith("#"):
                    list_number = 0  # a heading starts a new list
            else:
                out.append(item.text)
                if "\n\n" in item.text:
                    list_number = 0  # a new paragraph starts a new list
            line_start = item.mode not in ("bullet", "number")
            at_start = False
            glue_next = True
            capitalize_next = register.smart_caps
        elif item.kind == "punct":
            glyph = item.text
            if item.mode == "left":
                emit(glyph, glue_left=True)
            elif item.mode == "right":
                emit(glyph, glue_left=False)
                glue_next = True
            elif item.mode == "both":
                emit(glyph, glue_left=True)
                glue_next = True
            else:
                emit(glyph, glue_left=False)
            if item.sentence_end and register.smart_caps:
                capitalize_next = True
            if glyph in ("#", "@"):
                capitalize_next = False  # #tags and @mentions stay as said
        elif item.kind == "word":
            text = item.text
            core = text.rstrip(".,!?;:")
            if register.smart_caps and item.mode != "verbatim" and core.casefold() in ACRONYMS:
                text = core.upper() + text[len(core):]
            if register.smart_caps and item.mode != "verbatim" and (
                capitalize_next or _PRONOUN_I.fullmatch(text)
                or text.rstrip(".,!?;:'\u2019s").casefold() in PROPER_WORDS
            ):
                text = _capitalized(text)
            emit(text, glue_left=False)
            capitalize_next = register.smart_caps and text.rstrip().endswith(_SENTENCE_ENDERS)
        # scratch/instruction items render nothing; the engine acts on them.

    return "".join(out), replace(
        state,
        at_start=at_start,
        glue_next=glue_next,
        capitalize_next=capitalize_next,
        line_start=line_start,
        list_number=list_number,
    )
