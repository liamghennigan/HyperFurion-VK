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
}

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
) -> tuple[str, RenderState]:
    out: list[str] = []
    at_start = state.at_start
    glue_next = state.glue_next
    pending = state.pending
    after_name = state.after_name
    open_calls = state.open_calls
    inner_parens = state.inner_parens
    after_prefix = state.after_prefix

    def emit(text: str, *, glue_left: bool, name: bool = False, prefix: bool = False) -> None:
        nonlocal at_start, glue_next, after_name, after_prefix
        if not at_start and not glue_next and not glue_left:
            out.append(" ")
        out.append(text)
        at_start = False
        glue_next = False
        after_name = name
        after_prefix = prefix

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

    for item in items:
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
            if after_prefix and item.text == '"' and item.mode == "right":
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
            if pending in ("dash", "dashes"):
                emit(("--" if pending == "dashes" else "-") + item.text, glue_left=False)
                pending = ""
                continue
            if pending == "call-open":
                pending = "call"
            glyph = word_glyphs.get(core)
            if glyph is not None:
                emit_mode(glyph[0], glyph[1])
                continue
            if core in callables:
                # Calls nest: "print range ten close paren close paren".
                emit(item.text + "(", glue_left=False)
                glue_next = True
                pending = "call-open"  # an explicit "open paren" next is absorbed
                open_calls += 1
                continue
            if constants and core in constants:
                emit(constants[core], glue_left=False)
                continue
            emit(
                item.text,
                glue_left=False,
                name=glue_calls and _is_name(item.text),
                prefix=glue_calls and core in _STRING_PREFIXES,
            )
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
        after_prefix=after_prefix,
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
    )


COMPILERS = {
    "python": compile_python,
    "shell": compile_shell,
}
