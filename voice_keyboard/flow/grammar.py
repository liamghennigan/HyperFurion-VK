"""The spoken edit grammar: a pure token-stream parser.

Turns raw transcript tokens into render items — words, punctuation glyphs,
line breaks — plus action items the engine executes ("scratch that", a
wake-word instruction). Parsing is a deterministic left-to-right scan with
bounded lookahead, so parsing a token prefix yields a prefix of the items:
the engine relies on this to keep committed output frozen.

Everything is data-driven: command phrases, the punctuation table, and the
user vocabulary all come from config and can be remapped or disabled.
"""

from dataclasses import dataclass
from typing import Optional

from voice_keyboard.flow.numbers import NUMBER_WORDS, convert_numbers

_PUNCT_STRIP = ".,!?;:"

# Longest supported phrase, in tokens; also bounds the parse holdback.
MAX_PHRASE_TOKENS = 4


@dataclass(frozen=True)
class Item:
    kind: str                    # word | punct | break | scratch | instruction
    text: str = ""               # word text, punct glyph, break chars, instruction
    mode: str = "none"           # punct spacing: left | right | both | none
    sentence_end: bool = False
    span: tuple[int, int] = (0, 0)  # [start, end) raw-token indices


@dataclass(frozen=True)
class ParseResult:
    items: list[Item]
    pending_from: Optional[int]  # raw-token index where an incomplete phrase
    # begins (held back from rendering), or None


# action name -> default trigger phrases
DEFAULT_COMMANDS: dict[str, tuple[str, ...]] = {
    "scratch_that": ("scratch that", "delete that"),
    "new_line": ("new line",),
    "new_paragraph": ("new paragraph",),
    "literal": ("literal",),
}

# phrase -> (glyph, mode, sentence_end)
DEFAULT_PUNCTUATION: dict[str, tuple[str, str, bool]] = {
    "period": (".", "left", True),
    "full stop": (".", "left", True),
    "comma": (",", "left", False),
    "question mark": ("?", "left", True),
    "exclamation point": ("!", "left", True),
    "exclamation mark": ("!", "left", True),
    "colon": (":", "left", False),
    "semicolon": (";", "left", False),
    "dash": ("-", "none", False),
    "hyphen": ("-", "both", False),
    "em dash": ("—", "both", False),
    "ellipsis": ("...", "left", False),
    "dot dot dot": ("...", "left", False),
    "open quote": ('"', "right", False),
    "close quote": ('"', "left", False),
    "apostrophe": ("'", "both", False),
    "open paren": ("(", "right", False),
    "close paren": (")", "left", False),
    "open bracket": ("[", "right", False),
    "close bracket": ("]", "left", False),
    "open brace": ("{", "right", False),
    "close brace": ("}", "left", False),
    "at sign": ("@", "both", False),
    "ampersand": ("&", "none", False),
    "percent sign": ("%", "left", False),
    "dollar sign": ("$", "right", False),
    "underscore": ("_", "both", False),
    "forward slash": ("/", "both", False),
    "backslash": ("\\", "both", False),
    "pipe symbol": ("|", "none", False),
    "tilde": ("~", "right", False),
    "backtick": ("`", "both", False),
    "equals sign": ("=", "none", False),
    "plus sign": ("+", "none", False),
}

_BREAKS = {"new_line": "\n", "new_paragraph": "\n\n"}


def _core(token: str) -> str:
    return token.casefold().strip(_PUNCT_STRIP)


def _phrase_tokens(phrase: str) -> tuple[str, ...]:
    return tuple(word.casefold() for word in phrase.split())


class Grammar:
    def __init__(
        self,
        *,
        enabled: bool = True,
        commands: Optional[dict] = None,
        punctuation: Optional[dict] = None,
        vocabulary: Optional[dict] = None,
        wake_word: str = "furion",
        numbers: str = "auto",
        numbers_on: bool = False,
        numbers_min: int = 10,
    ):
        self.enabled = enabled
        self._wake = (wake_word or "").strip().casefold()
        numbers = numbers if numbers in {"auto", "always", "off"} else "auto"
        self._numbers_on = numbers == "always" or (numbers == "auto" and numbers_on)
        self._numbers_min = 0 if numbers == "always" else numbers_min

        merged_commands = dict(DEFAULT_COMMANDS)
        for action, phrases in (commands or {}).items():
            if action not in DEFAULT_COMMANDS:
                continue
            if isinstance(phrases, str):
                phrases = [phrases]
            merged_commands[action] = tuple(str(p) for p in phrases if str(p).strip())

        merged_punct = dict(DEFAULT_PUNCTUATION)
        for phrase, glyph in (punctuation or {}).items():
            phrase_key = str(phrase).strip().casefold()
            glyph = str(glyph)
            if not phrase_key:
                continue
            if not glyph:
                merged_punct.pop(phrase_key, None)
                continue
            _, mode, sentence_end = merged_punct.get(phrase_key, ("", "left", False))
            merged_punct[phrase_key] = (glyph, mode, sentence_end)

        # phrase tuple -> ("command", action) | ("punct", spec) | ("vocab", text)
        self._phrases: dict[tuple[str, ...], tuple[str, object]] = {}
        for phrase, spec in merged_punct.items():
            self._phrases[_phrase_tokens(phrase)] = ("punct", spec)
        for action, phrases in merged_commands.items():
            for phrase in phrases:
                self._phrases[_phrase_tokens(phrase)] = ("command", action)
        for phrase, replacement in (vocabulary or {}).items():
            tokens = _phrase_tokens(str(phrase))
            if tokens:
                self._phrases[tokens] = ("vocab", str(replacement))

        self._max_phrase = max(
            (len(p) for p in self._phrases), default=1
        )

    _TRAILING_SPECS = {
        ".": ("left", True), ",": ("left", False), "!": ("left", True),
        "?": ("left", True), ";": ("left", False), ":": ("left", False),
    }

    def _trailing_punct(self, token: str, span: tuple[int, int]) -> list[Item]:
        suffix = token[len(token.rstrip(_PUNCT_STRIP)):]
        return [
            Item(
                kind="punct",
                text=ch,
                mode=self._TRAILING_SPECS[ch][0],
                sentence_end=self._TRAILING_SPECS[ch][1],
                span=span,
            )
            for ch in suffix
            if ch in self._TRAILING_SPECS
        ]

    def is_wake_word(self, token: str) -> bool:
        return bool(self._wake) and _core(token) == self._wake

    def _match_phrase(
        self, cores: list[str], index: int, max_len: int
    ) -> tuple[Optional[tuple[str, object]], int]:
        """Longest phrase match at `index`; returns (entry, tokens consumed)."""
        limit = min(self._max_phrase, len(cores) - index, max_len)
        for length in range(limit, 0, -1):
            candidate = tuple(cores[index:index + length])
            entry = self._phrases.get(candidate)
            if entry is not None:
                return entry, length
        return None, 0

    def _could_extend(self, cores: list[str], index: int) -> bool:
        """True if the tokens from `index` to the end are a proper prefix of
        some longer phrase — i.e. the next transcript update might complete
        a command, so these tokens should be held back."""
        tail = tuple(cores[index:])
        if not tail or len(tail) >= self._max_phrase:
            return False
        for phrase in self._phrases:
            if len(phrase) > len(tail) and phrase[:len(tail)] == tail:
                return True
        return False

    def parse(
        self,
        tokens: list[str],
        *,
        flush: bool = False,
        frozen: int = 0,
    ) -> ParseResult:
        """Parse raw tokens into items.

        With flush=False, trailing tokens that might still grow into a
        phrase (or extend a number run) are reported via `pending_from`
        and produce no items. flush=True resolves everything — the stop
        path uses it.

        `frozen` is the engine's committed-token fence: no phrase may span
        it. Tokens before it were already committed under some parse, and
        fencing guarantees this parse reproduces those items exactly even
        if later tokens would retroactively complete a longer phrase.
        """
        if not self.enabled:
            items = [
                Item(kind="word", text=token, span=(i, i + 1))
                for i, token in enumerate(tokens)
            ]
            return ParseResult(items=items, pending_from=None)

        cores = [_core(token) for token in tokens]
        items: list[Item] = []
        pending_from: Optional[int] = None
        index = 0

        while index < len(tokens):
            core = cores[index]
            fence = frozen - index if index < frozen else len(tokens)

            # Wake word: everything after it is an instruction, never
            # typed. It resolves only at finalize; until then it holds the
            # tail back (the caption shows instruction-listening state).
            if index >= frozen and self._wake and core == self._wake:
                if not flush:
                    pending_from = index
                    break
                instruction = " ".join(tokens[index + 1:]).strip()
                items.append(
                    Item(
                        kind="instruction",
                        text=instruction,
                        span=(index, len(tokens)),
                    )
                )
                index = len(tokens)
                break

            entry, consumed = self._match_phrase(cores, index, fence)
            if (
                entry is None
                and not flush
                and index >= frozen
                and self._could_extend(cores, index)
            ):
                pending_from = index
                break

            if entry is not None:
                # A phrase spoken with attached sentence punctuation
                # ("scratch that.") still matches: cores strip it.
                kind, payload = entry
                span = (index, index + consumed)
                if kind == "punct":
                    glyph, mode, sentence_end = payload  # type: ignore[misc]
                    items.append(
                        Item(
                            kind="punct",
                            text=glyph,
                            mode=mode,
                            sentence_end=sentence_end,
                            span=span,
                        )
                    )
                elif kind == "vocab":
                    items.append(Item(kind="word", text=str(payload), span=span))
                    # Punctuation the provider attached to the phrase's last
                    # token survives the replacement ("hyper furion," -> ",").
                    items.extend(self._trailing_punct(tokens[index + consumed - 1], span))
                elif payload == "literal":
                    # Emit the next token verbatim, bypassing the grammar.
                    if index < frozen or index + consumed >= len(tokens):
                        if index >= frozen and not flush:
                            pending_from = index
                            break
                        items.append(
                            Item(kind="word", text=tokens[index], span=(index, index + 1))
                        )
                        index += 1
                        continue
                    items.append(
                        Item(
                            kind="word",
                            text=tokens[index + consumed],
                            span=(index, index + consumed + 1),
                        )
                    )
                    index += consumed + 1
                    continue
                elif payload in _BREAKS:
                    items.append(
                        Item(kind="break", text=_BREAKS[str(payload)], span=span)
                    )
                elif payload == "scratch_that":
                    items.append(Item(kind="scratch", span=span))
                else:  # a command with no stream effect (future actions)
                    items.append(Item(kind="word", text=tokens[index], span=(index, index + 1)))
                    index += 1
                    continue
                index += consumed
                continue

            items.append(Item(kind="word", text=tokens[index], span=(index, index + 1)))
            index += 1

        if self._numbers_on:
            items, number_pending = self._fold_numbers(
                items,
                flush=flush or pending_from is not None,
                frozen=frozen,
            )
            if number_pending is not None and pending_from is None:
                pending_from = number_pending

        return ParseResult(items=items, pending_from=pending_from)

    def _fold_numbers(
        self,
        items: list[Item],
        *,
        flush: bool,
        frozen: int,
    ) -> tuple[list[Item], Optional[int]]:
        """Convert runs of consecutive number-word items into digit items.

        A number run still touching the molten tail is held back (it might
        keep growing) unless flushing. Runs never start before the frozen
        fence — committed words stay exactly as they were committed.
        """
        result: list[Item] = []
        run: list[Item] = []
        pending_from: Optional[int] = None

        def close_run(at_tail: bool) -> None:
            nonlocal pending_from
            if not run:
                return
            if at_tail and not flush:
                pending_from = run[0].span[0]
                run.clear()
                return
            texts = [item.text for item in run]
            converted = convert_numbers(texts, min_value=self._numbers_min)
            if converted == texts:
                result.extend(run)
            else:
                span = (run[0].span[0], run[-1].span[1])
                for text in converted:
                    result.append(Item(kind="word", text=text, span=span))
            run.clear()

        for item in items:
            if (
                item.kind == "word"
                and item.span[0] >= frozen
                and _core(item.text) in NUMBER_WORDS
            ):
                run.append(item)
            else:
                close_run(at_tail=False)
                result.append(item)
        close_run(at_tail=True)
        return result, pending_from
