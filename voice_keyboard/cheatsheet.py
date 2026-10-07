"""`voice-keyboard commands`: everything you can say, from the live tables.

Built from the same Grammar the daemon builds — your [flow] commands,
punctuation and vocabulary merged in, and the words you accepted with
`voice-keyboard learned` — plus the caret commands, snippets, named
macros and wake-word channels your config enables, so the list can never
drift from what the engine does.
"""

import logging
from typing import Optional

from voice_keyboard import dictionary
from voice_keyboard.flow.grammar import FORMATTERS
from voice_keyboard.flow.registers import REGISTERS
from voice_keyboard.trial import dictation_grammar

logger = logging.getLogger(__name__)

_COMMAND_NOTES = {
    "scratch_that": "take back the last utterance",
    "new_line": "line break",
    "new_paragraph": "blank line",
    "bullet": "\"- \" on a line of its own",
    "number": "\"1. \", \"2. \", … on a line of its own",
    "heading": "\"# \" on a line of its own (markdown heading)",
    "subheading": "\"## \" on a line of its own",
    "checkbox": "\"- [ ] \" on a line of its own (a task)",
    "cap_that": "the last utterance in Title Case (said on its own)",
    "upper_that": "the last utterance in UPPER CASE (said on its own)",
    "lower_that": "the last utterance in lower case (said on its own)",
    "literal": "type the next word as a word, not a command",
}

_NAV = [
    ("go left three words / go back a word", "move the caret"),
    ("go to end of line / go to the start of the document", "jump"),
    ("select previous word / select all / select this line", "select"),
    ("select that", "select what you just said"),
    ("delete previous word / delete the line", "delete"),
    ("press tab / press escape twice / press page down", "press a key"),
    ("undo that / redo that / paste that", "editors only"),
]

_FOLDS = [
    ("twenty five percent / five dollars and fifty cents", "25% / $5.50"),
    ("one point five dollars / two million euros", "$1.50 / €2 million"),
    ("three thirty pm / three oh five am", "3:30 PM / 3:05 AM"),
    ("october sixth / june twenty first", "October 6 / June 21"),
    ("june fifth nineteen ninety nine", "June 5, 1999"),
    ("nineteen oh five / twenty twenty six", "1905 / 2026"),
    ("at three thirty / five o'clock", "at 3:30 / 5 o'clock"),
    ("room four oh two / page twenty five", "room 402 / page 25"),
    ("version three point one point four", "version 3.1.4"),
    ("q three / two point five megabytes", "Q3 / 2.5 MB"),
    ("five five five one two three four", "555-1234"),
    ("docs dot python dot org / example dot com slash docs", "docs.python.org / example.com/docs"),
    ("liam at example dot com", "liam@example.com"),
    ("quote ship it unquote / quote … end quote", "\"ship it\""),
    ("spell that n g i n x", "replace the last word, spelled"),
    ("correct monday to friday", "swap the last \"monday\" you said (said on its own)"),
]


def _grammar(config: dict):
    # the daemon's own builder: [flow] plus the accepted learned words
    return dictation_grammar(config, REGISTERS["prose"])


def _learned(config: dict) -> set:
    """The spoken keys `voice-keyboard learned` added to dictation (an
    explicit [flow.vocabulary] entry wins, so it is listed there)."""
    if not config.get("flow", {}).get("personal_dictionary", True):
        return set()
    try:
        return {str(k).strip().casefold() for k in dictionary.vocabulary_overrides()}
    except Exception:
        logger.exception("Could not load the personal dictionary")
        return set()


def _macros() -> list:
    try:
        return sorted(dictionary.load_dictionary().get("macros") or {})
    except Exception:
        logger.exception("Could not load the personal dictionary")
        return []


def render(config: dict, query: Optional[str] = None) -> str:
    """The cheat sheet as text; `query` keeps only lines that mention it."""
    flow = config.get("flow", {})
    wake = str(flow.get("wake_word", "vk")).strip() or "vk"
    phrases = _grammar(config).phrases()
    user_vocab = {str(k).strip().casefold() for k in (flow.get("vocabulary") or {})}
    learned = _learned(config) - user_vocab

    sections: list[tuple[str, list[tuple[str, str]]]] = []
    order = list(_COMMAND_NOTES)
    commands = [
        (phrase, _COMMAND_NOTES.get(str(payload), str(payload)))
        for phrase, kind, payload in sorted(
            (row for row in phrases if row[1] == "command"),
            key=lambda row: order.index(str(row[2])) if str(row[2]) in order else len(order),
        )
    ]
    sections.append(("Editing and layout", commands))
    punct = sorted((phrase, str(payload[0])) for phrase, kind, payload in phrases if kind == "punct")
    sections.append(("Punctuation", punct))
    sections.append(("Numbers, dates, addresses (prose)", _FOLDS))
    emoji = sorted((phrase, str(payload)) for phrase, kind, payload in phrases
                   if kind == "vocab" and phrase.startswith("emoji ") and phrase not in user_vocab | learned)
    sections.append(("Emoji", emoji))
    vocab = sorted((phrase, str(payload)) for phrase, kind, payload in phrases
                   if kind == "vocab" and phrase in user_vocab)
    if vocab:
        sections.append(("Your vocabulary ([flow.vocabulary])", vocab))
    taught = sorted((phrase, str(payload)) for phrase, kind, payload in phrases
                    if kind == "vocab" and phrase in learned)
    if taught:
        sections.append(("Words you taught it (voice-keyboard learned)", taught))
    if config.get("nav", {}).get("enabled", False):
        sections.append(("Caret commands (said on their own; never Enter)", _NAV))
    else:
        sections.append(("Caret commands", [("go left / select that / undo that …", "off: set [nav] enabled = true")]))
    formatters = sorted((" ".join(words) + " <words>", style) for words, style in FORMATTERS.items())
    sections.append((f"Formatters ([flow] formatters = {flow.get('formatters', 'code')})", formatters))

    channels = [(f"{wake}, make that formal", "rewrite the last dictation (or the selection) via [llm]")]
    for section, label in (("intent", "type a command, never Enter"), ("ask", "answer about the selection"),
                           ("recall", "search what you dictated")):
        cfg = config.get(section, {})
        verb = (cfg.get("verbs") or [section])[0]
        state = label if cfg.get("enabled", False) else f"off: set [{section}] enabled = true"
        channels.append((f"{wake}, {verb} …", state))
    snippets = sorted(config.get("snippets", {}) or {})
    for name in snippets:
        channels.append((f"{wake}, {name}", "types your snippet"))
    named = {str(name).strip().strip(".,!?;:").casefold() for name in snippets}
    for name in _macros():
        if name not in named:  # a [snippets] entry of the same name wins
            channels.append((f"{wake}, {name}", "types your macro (voice-keyboard learned)"))
    sections.append(("Wake word", channels))

    needle = (query or "").strip().casefold()
    lines: list[str] = []
    for title, rows in sections:
        if needle:
            rows = [row for row in rows if needle in row[0].casefold() or needle in row[1].casefold()
                    or needle in title.casefold()]
        if not rows:
            continue
        width = min(48, max(len(say) for say, _ in rows))
        lines.append(title)
        lines.extend(f"  {say:<{width}}  {what}" for say, what in rows)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n" if lines else f"Nothing matches {query!r}.\n"
