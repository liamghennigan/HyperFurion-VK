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

_SHELL_WORD_GLYPHS = {
    "pipe": ("|", "none"),
    "dot": (".", "both"),
    # A glob star starts a token: spaced from the command, glued rightward.
    "star": ("*", "right"),
    "slash": ("/", "both"),
}

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
) -> tuple[str, RenderState]:
    out: list[str] = []
    at_start = state.at_start
    glue_next = state.glue_next
    pending = state.pending
    after_name = state.after_name

    def emit(text: str, *, glue_left: bool, name: bool = False) -> None:
        nonlocal at_start, glue_next, after_name
        if not at_start and not glue_next and not glue_left:
            out.append(" ")
        out.append(text)
        at_start = False
        glue_next = False
        after_name = name

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
        if pending == "dash":
            emit("-", glue_left=False)
            pending = ""

    for item in items:
        if item.kind == "break":
            flush_dash()
            pending = ""
            out.append(item.text)
            at_start = False
            glue_next = True
            after_name = False
        elif item.kind == "punct":
            if pending == "call-open" and item.text == "(" and item.mode == "right":
                pending = "call"  # "print open paren": the callable already opened it
                continue
            if pending in ("call", "call-open") and item.text == ":":
                emit("):", glue_left=True)
                pending = ""
                continue
            if dash_hold and item.text == "-" and item.mode == "none":
                # Hold the dash: the next word becomes a flag ("-i").
                flush_dash()
                pending = "dash"
                continue
            flush_dash()
            if pending in ("call", "call-open") and item.text == ")":
                pending = ""
            elif pending == "call-open":
                pending = "call"
            if glue_calls and after_name and item.text in "([" and item.mode == "right":
                emit(item.text, glue_left=True)  # a call or a subscript: f(, xs[
                glue_next = True
                continue
            emit_mode(item.text, item.mode)
        elif item.kind == "word":
            core = item.text.casefold()
            if pending == "dash":
                emit("-" + item.text, glue_left=False)
                pending = ""
                continue
            if pending == "call-open":
                pending = "call"
            glyph = word_glyphs.get(core)
            if glyph is not None:
                emit_mode(glyph[0], glyph[1])
                continue
            if core in callables and pending != "call":
                emit(item.text + "(", glue_left=False)
                glue_next = True
                pending = "call-open"  # an explicit "open paren" next is absorbed
                continue
            emit(item.text, glue_left=False, name=glue_calls and _is_name(item.text))
        # scratch/instruction items render nothing; the engine acts on them.

    return "".join(out), replace(
        state,
        at_start=at_start,
        glue_next=glue_next,
        capitalize_next=False,
        pending=pending,
        after_name=after_name,
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
    )


COMPILERS = {
    "python": compile_python,
    "shell": compile_shell,
}
