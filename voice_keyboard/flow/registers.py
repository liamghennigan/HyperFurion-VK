"""Context registers: how dictation renders in the focused app.

A register bundles the rendering policy (capitalization, spacing, numbers)
plus injection details (which paste chord the app understands). The daemon
probes the focused app at recording start and resolves a register from
config; rendering itself is a pure left-to-right fold so a longer
transcript always renders with the previous render as a prefix — the
property the molten repair engine depends on.
"""

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


PROSE = Register(name="prose", smart_caps=True, numbers_on=False)
TERMINAL = Register(
    name="terminal",
    smart_caps=False,
    numbers_on=True,
    numbers_min=0,
    paste_chord_shift=True,
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
)

REGISTERS = {r.name: r for r in (PROSE, TERMINAL, VERBATIM, PYTHON, SHELL)}

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
    pending: str = ""              # semantic-compiler hold (dash, open call)


def initial_state(register: Register) -> RenderState:
    return RenderState(capitalize_next=register.smart_caps)


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
    """
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

    def emit(text: str, *, glue_left: bool) -> None:
        nonlocal at_start, glue_next
        if not at_start and not glue_next and not glue_left:
            out.append(" ")
        out.append(text)
        at_start = False
        glue_next = False

    for item in items:
        if item.kind == "break":
            out.append(item.text)
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
        elif item.kind == "word":
            text = item.text
            if capitalize_next and register.smart_caps:
                text = _capitalized(text)
            emit(text, glue_left=False)
            capitalize_next = register.smart_caps and text.rstrip().endswith(_SENTENCE_ENDERS)
        # scratch/instruction items render nothing; the engine acts on them.

    return "".join(out), replace(
        state,
        at_start=at_start,
        glue_next=glue_next,
        capitalize_next=capitalize_next,
    )
