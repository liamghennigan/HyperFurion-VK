"""Semantic registers: compile speech instead of transcribing it.

"for i in range ten colon" becomes `for i in range(10):`; "pipe grep dash
i error" becomes `| grep -i error`. Deterministic tables only — fast,
offline, predictable; anything unknown falls through as a plain word.

Every compiler here is a pure prefix-stable left-to-right fold over
grammar items, exactly like `render_items`: rendering a longer item list
always extends the previous render, and all carried context lives in
RenderState (the engine snapshots it for "scratch that" rewinds). A
non-associative compiler would corrupt the molten commit/preview split.
"""

import keyword
import re
from dataclasses import replace

from voice_keyboard.flow.grammar import Item
from voice_keyboard.flow.registers import Register, RenderState

# Spoken words that become glyphs. mode mirrors the punctuation table:
# left = glue to the previous atom, right = glue to the next,
# both = glue both sides, none = spaced like a word.
_PYTHON_WORD_GLYPHS = {
    "dot": (".", "both"),
    "equals": ("=", "none"),
    "plus": ("+", "none"),
    "minus": ("-", "none"),
    "times": ("*", "none"),
    "modulo": ("%", "none"),
    "arrow": ("->", "none"),
}

# Python's constants, said in lowercase: "if x is not none" -> None.
_PYTHON_CONSTANTS = {"none": "None", "true": "True", "false": "False"}
# String prefixes a quote glues to: "f quote hi unquote" -> f"hi".
_STRING_PREFIXES = frozenset({"f", "r", "b", "rb", "br", "fr", "rf", "u"})

_SHELL_WORD_GLYPHS = {
    "pipe": ("|", "none"),
    "dot": (".", "both"),
    # A glob star starts a token: spaced from the command, glued rightward.
    "star": ("*", "right"),
    "slash": ("/", "both"),
    "plus": ("+", "right"),  # "chmod plus x" -> +x
}
_SHELL_PAIRS = {
    ("greater", "than"): ">",
    ("less", "than"): "<",
    ("and", "and"): "&&",
}
# A path after one of these starts a new argument: "cd slash etc" -> "cd /etc".
_SHELL_COMMANDS = frozenset(
    "cd ls cat tail head less more vim vi nano code rm cp mv mkdir rmdir touch chmod chown "
    "find grep source open sudo echo tree du df stat file ln tar unzip zip scp rsync".split()
)

# Two spoken words, one operator: "double equals" -> "==". The first word
# is held until the next says whether it was half an operator ("if not x"
# types "not").
_PYTHON_PAIRS = {
    ("double", "equals"): "==",
    ("not", "equals"): "!=",
    ("less", "than"): "<",
    ("greater", "than"): ">",
    # builtin exceptions: "raise value error" -> ValueError
    ("value", "error"): "ValueError",
    ("type", "error"): "TypeError",
    ("key", "error"): "KeyError",
    ("index", "error"): "IndexError",
    ("runtime", "error"): "RuntimeError",
    ("attribute", "error"): "AttributeError",
    ("import", "error"): "ImportError",
    ("name", "error"): "NameError",
    ("assertion", "error"): "AssertionError",
    ("lookup", "error"): "LookupError",
    ("permission", "error"): "PermissionError",
    ("timeout", "error"): "TimeoutError",
    ("connection", "error"): "ConnectionError",
    ("os", "error"): "OSError",
    ("memory", "error"): "MemoryError",
    ("recursion", "error"): "RecursionError",
    ("stop", "iteration"): "StopIteration",
    ("keyboard", "interrupt"): "KeyboardInterrupt",
}
# "=" glues onto these: "plus equals" -> "+=", "less than equals" -> "<=".
_AUGMENTED = frozenset({"+", "-", "*", "/", "%", "<", ">", "!", "=", "//", "**"})

# Shell glues these on both sides: "FOO=bar", "--name=value", "8080:80".
_SHELL_GLUED = {"=": "both", ":": "both"}

# Spoken callables: "range ten colon" -> "range(10):". The open paren is
# emitted eagerly; a following colon closes it ("):"), otherwise the user
# says "close paren" — deterministic, never guessed.
_PYTHON_CALLABLES = {
    "range", "print", "len", "str", "int", "float", "input", "enumerate",
    "sorted", "reversed", "abs", "min", "max", "sum", "type", "repr",
}


# A name an opening paren or bracket glues to: "get_user (" -> get_user(.
# Keywords keep their space ("if (", "in [").
_NAME = re.compile(r"^[A-Za-z_][\w.]*$")
_KEYWORDS = frozenset(keyword.kwlist) | frozenset(keyword.softkwlist)


def _is_name(text: str) -> bool:
    return bool(_NAME.match(text)) and text not in _KEYWORDS


def _compile(
    items: list[Item],
    state: RenderState,
    *,
    word_glyphs: dict,
    callables: frozenset | set,
    dash_hold: bool,
    glue_calls: bool = False,
    constants: dict | None = None,
    glued: dict | None = None,
    pairs: dict | None = None,
    dot_hold: bool = False,
) -> tuple[str, RenderState]:
    out: list[str] = []
    at_start = state.at_start
    glue_next = state.glue_next
    pending = state.pending
    after_name = state.after_name
    open_calls = state.open_calls
    inner_parens = state.inner_parens
    last_atom = state.last_atom
    firsts = {first for first, _ in (pairs or {})}

    def emit(text: str, *, glue_left: bool, name: bool = False) -> None:
        nonlocal at_start, glue_next, after_name, last_atom
        if text == "=" and last_atom in _AUGMENTED and glue_calls:
            glue_left = True  # "+=", "==", "<=", "!="
        if not at_start and not glue_next and not glue_left:
            out.append(" ")
        out.append(text)
        at_start = False
        glue_next = False
        after_name = name
        last_atom = text

    def emit_mode(glyph: str, mode: str) -> None:
        nonlocal glue_next
        if mode == "left":
            emit(glyph, glue_left=True)
        elif mode == "right":
            emit(glyph, glue_left=False)
            glue_next = True
        elif mode == "both":
            emit(glyph, glue_left=True)
            glue_next = True
        else:
            emit(glyph, glue_left=False)

    def flush_dash() -> None:
        nonlocal pending
        if pending in ("dash", "dashes"):
            emit("--" if pending == "dashes" else "-", glue_left=False)
            pending = ""
        elif pending.startswith("hold:"):
            held, pending = pending[5:], ""
            word(held)
        elif pending == "dot":
            pending = ""
            emit(".", glue_left=False)  # "find dot dash name": the dot is a path

    def word(text: str) -> None:
        nonlocal pending, glue_next, open_calls
        core = text.casefold()
        if pending == "call-open":
            pending = "call"
        glyph = word_glyphs.get(core)
        if glyph is not None:
            if dot_hold and glyph[0] == "/" and (last_atom in _SHELL_COMMANDS or last_atom[:1] == "-"):
                emit("/", glue_left=False)  # "cd slash etc", "tail dash f slash var"
                glue_next = True
                return
            emit_mode(glyph[0], glyph[1])
            return
        if core in callables and last_atom != "->":
            # Calls nest: "print range ten close paren close paren". After
            # "->" it is a type: "-> str:".
            emit(text + "(", glue_left=False)
            glue_next = True
            pending = "call-open"  # an explicit "open paren" next is absorbed
            open_calls += 1
            return
        if constants and core in constants:
            emit(constants[core], glue_left=False)
            return
        emit(text, glue_left=False, name=glue_calls and _is_name(text))

    for item in items:
        if item.kind == "flush":
            flush_dash()  # the dictation ended: a hold is typed as said
            continue
        if item.kind == "break" and item.mode in ("bullet", "number"):
            continue  # a list item means nothing in code
        if item.kind == "break":
            flush_dash()
            pending = ""
            open_calls = 0
            inner_parens = 0
            out.append(item.text)
            at_start = False
            glue_next = True
            after_name = False
        elif item.kind == "punct":
            if pending.startswith("hold:") or pending == "dot":
                flush_dash()  # the held word was a word ("type (" opens its call)
            if pending == "call-open" and item.text == "(" and item.mode == "right":
                pending = "call"  # "print open paren": the callable already opened it
                continue
            if open_calls and item.text == ":":
                # "range len xs colon": a colon closes every open call
                emit(")" * (inner_parens + open_calls) + ":", glue_left=True)
                pending = ""
                open_calls = inner_parens = 0
                continue
            if dash_hold and item.text == "-" and item.mode == "none":
                # Hold the dash: the next word becomes a flag ("-i", and
                # "dash dash rm" -> "--rm").
                if pending == "dash":
                    pending = "dashes"
                    continue
                flush_dash()
                pending = "dash"
                continue
            flush_dash()
            if glue_calls and last_atom.casefold() in _STRING_PREFIXES and item.text == '"' and item.mode == "right":
                emit('"', glue_left=True)  # f"…", r"…"
                glue_next = True
                continue
            if pending == "call-open":
                pending = "call" if open_calls else ""
            if item.text == ")" and inner_parens:
                inner_parens -= 1  # closes a paren said inside the call
            elif open_calls and item.text == ")":
                open_calls -= 1
                if not open_calls:
                    pending = ""
            elif open_calls and item.text == "(":
                inner_parens += 1
            if glue_calls and after_name and item.text in "([" and item.mode == "right":
                emit(item.text, glue_left=True)  # a call or a subscript: f(, xs[
                glue_next = True
                continue
            emit_mode(item.text, (glued or {}).get(item.text, item.mode))
        elif item.kind == "word":
            core = item.text.casefold()
            if dot_hold and core == "dot":
                # Held: "file dot txt" glues, "cd dot dot" is "..", a dot
                # before a flag or the end is a path of its own.
                if pending == "dot":
                    pending = ""
                    emit("..", glue_left=False)
                    continue
                flush_dash()
                pending = "dot"
                continue
            if pending == "dot":
                pending = ""
                # "file dot txt" glues; "dot slash run" starts a path
                emit(".", glue_left=word_glyphs.get(core, ("",))[0] != "/")
                glue_next = True
            if pending in ("dash", "dashes"):
                emit(("--" if pending == "dashes" else "-") + item.text, glue_left=False)
                pending = ""
                continue
            if pending.startswith("hold:"):
                operator = (pairs or {}).get((pending[5:].casefold(), core))
                if operator is not None:
                    pending = ""
                    emit(operator, glue_left=False, name=glue_calls and _is_name(operator))
                    continue
                flush_dash()  # the held word was a word
            if core in firsts:
                if pending == "call-open":
                    pending = "call" if open_calls else ""
                pending = "hold:" + item.text
                continue
            word(item.text)
        # scratch/instruction items render nothing; the engine acts on them.

    return "".join(out), replace(
        state,
        at_start=at_start,
        glue_next=glue_next,
        capitalize_next=False,
        pending=pending,
        after_name=after_name,
        open_calls=open_calls,
        inner_parens=inner_parens,
        last_atom=last_atom,
    )


def compile_python(
    items: list[Item], state: RenderState, register: Register
) -> tuple[str, RenderState]:
    return _compile(
        items,
        state,
        word_glyphs=_PYTHON_WORD_GLYPHS,
        callables=_PYTHON_CALLABLES,
        dash_hold=False,
        glue_calls=True,
        constants=_PYTHON_CONSTANTS,
        pairs=_PYTHON_PAIRS,
    )


def compile_shell(
    items: list[Item], state: RenderState, register: Register
) -> tuple[str, RenderState]:
    return _compile(
        items,
        state,
        word_glyphs=_SHELL_WORD_GLYPHS,
        callables=frozenset(),
        dash_hold=True,
        glued=_SHELL_GLUED,
        pairs=_SHELL_PAIRS,
        dot_hold=True,
    )


def flush_code(state: RenderState, register: Register) -> tuple[str, RenderState]:
    """At the end of a dictation: what a compiler still holds (a dash, the
    first word of a two-word operator) is typed as said."""
    if not register.compiler or not (
        state.pending in ("dash", "dashes", "dot") or state.pending.startswith("hold:")
    ):
        return "", state
    compiler = COMPILERS[register.compiler]
    # an empty item list only flushes: a break would reset more than the hold
    text, after = compiler([Item(kind="flush")], state, register)
    return text, after


COMPILERS = {
    "python": compile_python,
    "shell": compile_shell,
}
