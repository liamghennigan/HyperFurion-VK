"""`voice-keyboard try [register:] <words…>`: what the keyboard would type.

A `|` is a pause between utterances: commands said on their own ("correct
monday to friday", "select that", "cap that") need one before them. A
leading register ("python:") counts inside the quotes or outside them.

The words go through the same grammar and engine the daemon builds — your
[flow] vocabulary, the words you taught it (`voice-keyboard learned`),
commands, punctuation, language, numbers and formatters — as one
utterance, without a microphone or a daemon; "VK, <name>" types the
snippet or macro of that name, as it would when you dictate. Handy for
testing a command, a snippet name, or a config change.

The grammar builder and the snippet lookup live here, and the daemon
imports them, so the two cannot drift apart; this module imports nothing
that needs a microphone or a keyboard.
"""

import datetime
import logging
import re
from typing import Optional

from voice_keyboard import dictionary
from voice_keyboard.flow.engine import FlowConfig, FlowEngine
from voice_keyboard.flow.grammar import Grammar, grammar_from_config
from voice_keyboard.flow.registers import REGISTERS

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{(date|isodate|time|weekday)\}")


def dictation_grammar(config: dict, register, *, nav: Optional[bool] = None) -> Grammar:
    """The grammar a dictation is heard with: the config's, plus the
    personal dictionary's accepted overrides (`voice-keyboard learned
    add` / `accept`) while [flow] personal_dictionary is on; an explicit
    [flow.vocabulary] entry wins over a learned one."""
    flow_cfg = config.get("flow", {})
    vocabulary = dict(flow_cfg.get("vocabulary") or {})
    if flow_cfg.get("personal_dictionary", True):
        try:
            for spoken, replacement in dictionary.vocabulary_overrides().items():
                vocabulary.setdefault(spoken, replacement)
        except Exception:
            logger.exception("Could not load the personal dictionary")
    return grammar_from_config(config, register, vocabulary=vocabulary, nav=nav)


def expand_placeholders(text: str, now: Optional[datetime.datetime] = None) -> str:
    """A [snippets] entry's {date} (October 6, 2026), {isodate}
    (2026-10-06), {time} (14:05) and {weekday} (Tuesday), filled in when
    it is typed. Any other braces are typed as written."""
    now = now or datetime.datetime.now()
    values = {
        "date": f"{now:%B} {now.day}, {now.year}",
        "isodate": f"{now:%Y-%m-%d}",
        "time": f"{now:%H:%M}",
        "weekday": f"{now:%A}",
    }
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


def snippet_text(config: dict, name: str) -> Optional[str]:
    """The text saved under a spoken name: a [snippets] entry, else a
    macro you named via `voice-keyboard learned`. None when unknown.
    Names match without case or trailing punctuation ("My email.")."""
    key = name.strip().strip(".,!?;:").casefold()
    if not key:
        return None
    for spoken, text in (config.get("snippets") or {}).items():
        if str(spoken).strip().strip(".,!?;:").casefold() == key:
            return expand_placeholders(str(text))
    return dictionary.macro_text(name)


def snippet_gap(before: str, snippet: str) -> str:
    """The space between dictated words and a snippet after them."""
    if not before or before[-1:].isspace() or snippet[:1] in ".,;:!?)":
        return ""
    return " "


def _split_register(words: list[str]):
    """The register a leading "python:" picks — whether the shell passed
    it as its own word or inside the quotes with the rest — and the
    words after it."""
    tokens = " ".join(words).split()
    if tokens and tokens[0].endswith(":") and tokens[0][:-1].lower() in REGISTERS:
        return REGISTERS[tokens[0][:-1].lower()], tokens[1:]
    return REGISTERS["prose"], tokens


def run(config: dict, words: list[str]) -> str:
    """The typed result, with any caret command or wake-word instruction
    the utterance carries noted after it."""
    register, tokens = _split_register(words)
    utterances = [u.strip() for u in " ".join(tokens).split("|") if u.strip()]
    if not utterances:
        raise ValueError("say something: voice-keyboard try [register:] <words…> [| <words…>]")
    engine = FlowEngine(FlowConfig(), dictation_grammar(config, register), register)
    notes = []
    said, now = "", 0.0
    for utterance in utterances:
        said = (said + " " + utterance).strip()
        now += 1.0
        engine.on_transcript(said, is_final=True, now=now)
        while engine.pending_action() is not None:
            action = engine.pending_action()
            notes.append(f"[keys: {action.action.replace(':', ' ')}"
                         + (f" ×{action.count}" if action.count > 1 else "") + "]")
            engine.complete_action(now, pressed=True)
    result = engine.finalize(said, now=now + 1.0)
    while result.action is not None:
        notes.append(f"[keys: {result.action.action.replace(':', ' ')}"
                     + (f" ×{result.action.count}" if result.action.count > 1 else "") + "]")
        result = engine.complete_action(1.0, pressed=True)
    text = (result.typed_before + result.text) if result.typed_before else result.text
    snippet = snippet_text(config, result.instruction) if result.instruction else None
    if snippet is not None:
        # "send it to VK, my email": the saved text lands after the words.
        text = text + snippet_gap(text, snippet) + snippet
        notes.append(f"[snippet: {result.instruction}]")
    elif result.instruction:
        notes.append(f"[instruction: {result.instruction}]")
    if result.scratches:
        notes.append(f"[scratched {result.scratches}]")
    return text + ("\n" + " ".join(notes) if notes else "")


def repl(config: dict, stdin, stdout) -> int:
    """`voice-keyboard try` with no words: each line is one dictation —
    typed at a prompt, or piped in for a batch of cases."""
    interactive = stdin.isatty()
    if interactive:
        stdout.write("Say it as words (\"|\" marks a pause, \"python:\" picks a register). Ctrl-D quits.\n")
    while True:
        if interactive:
            stdout.write("› ")
            stdout.flush()
        line = stdin.readline()
        if not line:
            if interactive:
                stdout.write("\n")
            return 0
        if not line.strip():
            continue
        try:
            stdout.write(run(config, line.split()) + "\n")
        except ValueError as exc:
            stdout.write(f"{exc}\n")
        stdout.flush()
