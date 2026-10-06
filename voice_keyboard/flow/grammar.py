"""The spoken edit grammar: a pure token-stream parser.

Turns raw transcript tokens into render items — words, punctuation glyphs,
line breaks — plus action items the engine executes ("scratch that", a
wake-word instruction, "spell that n g i n x", "select previous word").
Parsing is a deterministic left-to-right scan with bounded lookahead, so
parsing a token prefix yields a prefix of the items: the engine relies on
this to keep committed output frozen.

Everything is data-driven: command phrases, the punctuation table, and the
user vocabulary all come from config and can be remapped or disabled.
"""

from dataclasses import dataclass
from typing import Optional

from voice_keyboard.flow.nav import PENDING as NAV_PENDING
from voice_keyboard.flow.nav import VERBS as NAV_VERBS
from voice_keyboard.flow.nav import parse_nav
from voice_keyboard.flow.numbers import NUMBER_WORDS, convert_numbers
from voice_keyboard.flow.spelling import (
    MAX_SPELLED_LETTERS,
    could_be_capital,
    letters_at,
)

_PUNCT_STRIP = ".,!?;:"

# Longest supported phrase, in tokens; also bounds the parse holdback.
MAX_PHRASE_TOKENS = 4


@dataclass(frozen=True)
class Item:
    kind: str  # word | punct | break | scratch | instruction | respell | key
    text: str = ""               # word text, punct glyph, break chars, instruction
    mode: str = "none"           # punct spacing: left | right | both | none;
    # respell: replace (the previous word) | insert
    sentence_end: bool = False
    span: tuple[int, int] = (0, 0)  # [start, end) raw-token indices
    count: int = 1               # key: how many times (go left THREE words)


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

SPELL_WORD = "spell"
# Spelled symbols that are also everyday words.
_AMBIGUOUS = {"a", "i", "one", "two", "four", "eight"}
_PENDING = "pending"


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
        wake_word: str = "vk",
        numbers: str = "auto",
        numbers_on: bool = False,
        numbers_min: int = 10,
        spelling: bool = True,
        nav: bool = False,
    ):
        self.enabled = enabled
        self._spelling = spelling
        self._nav = nav
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
        settled: int = 0,
        bounds: tuple[int, ...] = (),
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

        `settled` is how many tokens the provider has finalized. A spelled
        run or a navigation command starting inside them is decided there:
        it never waits for, or grows into, the next segment. `bounds` are
        the segment boundaries (token counts at each final, ascending): a
        command is decided against the segment it started in, so a verb
        that closed one segment as a word stays a word when the next
        segment arrives — whatever that segment says.
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

            if self._spelling and core == SPELL_WORD:
                limit, decided = self._limit(index, len(tokens), frozen, settled, flush, bounds)
                head = index + (2 if cores[index + 1:index + 2] == ["that"] else 1)
                if head >= limit:
                    # "spell that" ended its segment: the letters may come
                    # after a pause, in the next one. Behind the fence the
                    # run reads back exactly as it was committed.
                    if index < frozen:
                        limit, decided = frozen, True
                    elif settled > head:
                        limit, decided = settled, True
                    else:
                        limit, decided = len(tokens), flush
                spelled = self._parse_spelling(
                    tokens[:limit], cores[:limit], index, decided
                )
                if spelled == _PENDING:
                    pending_from = index
                    break
                if spelled is not None:
                    item, index = spelled  # type: ignore[misc]
                    items.append(item)
                    continue

            if self._nav and core in NAV_VERBS:
                limit, decided = self._limit(index, len(tokens), frozen, settled, flush, bounds)
                command = parse_nav(cores[:limit], index, decided=decided)
                if command == NAV_PENDING:
                    pending_from = index
                    break
                if command is not None:
                    action, count, end = command  # type: ignore[misc]
                    items.append(
                        Item(kind="key", text=action, count=count, span=(index, end))
                    )
                    index = end
                    continue

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
                    # Emit the next token verbatim, bypassing the grammar. A
                    # "literal" that was committed bare (its word never came
                    # before the fence closed) reads back bare.
                    target = index + consumed
                    if target >= len(tokens) or (index < frozen and target >= frozen):
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
                            text=tokens[target],
                            span=(index, target + 1),
                        )
                    )
                    index = target + 1
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

    @staticmethod
    def _limit(
        index: int,
        total: int,
        frozen: int,
        settled: int,
        flush: bool,
        bounds: tuple[int, ...] = (),
    ) -> tuple[int, bool]:
        """How far an open-ended command at `index` may read, and whether
        its end is decided: inside the final segments it may not cross the
        end of the segment it started in (nor the committed fence) and
        never waits; in the molten tail it reads to the end and waits
        unless flushing."""
        if index >= settled and index >= frozen:
            return total, flush
        limit = max(settled, frozen)
        for bound in bounds:
            if bound > index:
                limit = min(limit, bound)
                break
        if index < frozen:
            limit = min(limit, frozen)
        return limit, True

    def _parse_spelling(
        self, tokens: list[str], cores: list[str], index: int, flush: bool
    ):
        """"spell that <letters>" (replace the previous word) or "spell
        <letters>" (type the spelled word) at `index`.

        Returns (item, next index); _PENDING while the letter run touches
        the tail and might keep growing; None when this "spell" is just a
        word (an insert needs two letters, so "cast a spell" and "spell
        a ..." stay prose)."""
        start = index + 1
        mode = "insert"
        if start < len(tokens) and cores[start] == "that":
            mode = "replace"
            start += 1
        if start >= len(tokens):
            return None if flush else _PENDING
        pieces: list[tuple[str, int]] = []  # (letters, tokens used)
        cursor = start
        ended_on_word = False
        while cursor < len(tokens) and sum(len(p) for p, _ in pieces) < MAX_SPELLED_LETTERS:
            letters, used = letters_at(tokens, cursor)
            if not used:
                if (
                    not flush
                    and cursor == len(tokens) - 1
                    and could_be_capital(tokens[cursor])
                ):
                    return _PENDING
                ended_on_word = True
                break
            pieces.append((letters, used))
            cursor += used
        if cursor >= len(tokens) and not flush:
            return _PENDING  # the next update may spell more letters
        if ended_on_word:
            # A real word follows: trailing "a" / "I" / "one" are likely
            # that sentence's words, not letters ("... x a good one").
            while len(pieces) > 1 and cores[cursor - pieces[-1][1]] in _AMBIGUOUS:
                cursor -= pieces.pop()[1]
        word = "".join(letters for letters, _ in pieces)
        if len(word) < (1 if mode == "replace" else 2):
            return None
        word = word[:MAX_SPELLED_LETTERS]
        return Item(kind="respell", text=word, mode=mode, span=(index, cursor)), cursor

    def _fold_numbers(
        self,
        items: list[Item],
        *,
        flush: bool,
        frozen: int,
    ) -> tuple[list[Item], Optional[int]]:
        """Convert runs of consecutive number-word items into digit items.

        A number run still touching the molten tail is held back (it might
        keep growing) unless flushing. No run crosses the frozen fence: a
        run committed as "23" folds to "23" again on every reparse, and a
        number word after the fence can never reach back and change it.
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
            if item.kind == "word" and _core(item.text) in NUMBER_WORDS:
                if run and run[0].span[0] < frozen <= item.span[0]:
                    close_run(at_tail=False)  # the committed part folds alone
                run.append(item)
            else:
                close_run(at_tail=False)
                result.append(item)
        # A run entirely behind the fence was decided when it was committed.
        close_run(at_tail=bool(run) and run[0].span[0] >= frozen)
        return result, pending_from
