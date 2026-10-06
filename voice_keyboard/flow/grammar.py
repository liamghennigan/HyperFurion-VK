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

from dataclasses import dataclass, replace
import re
from typing import Optional

from voice_keyboard.flow.nav import PENDING as NAV_PENDING
from voice_keyboard.flow.languages import LANGUAGE_PACKS, PUNCT_GUARDS
from voice_keyboard.flow.nav import VERBS as NAV_VERBS
from voice_keyboard.flow.nav import parse_nav
from voice_keyboard.flow.numbers import (
    NUMBER_WORDS,
    CURRENCY,
    DATE_MONTHS,
    DIGIT_WORDS,
    NOT_A_DAY_AFTER,
    NOT_A_YEAR_AFTER,
    SCALE_WORDS,
    UNIT_WORDS,
    convert_numbers,
    fold_clock,
    fold_numbered,
    NUMBERED_NOUNS,
    fold_digits,
    fold_year,
    fold_unit,
    month_days,
    parse_cardinal,
    parse_day,
    split_compound,
)
from voice_keyboard.flow.pauses import COMMON_LOWER
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
    kind: str  # word | punct | break | scratch | instruction | respell | key | filler
    text: str = ""               # word text, punct glyph, break chars, instruction
    mode: str = "none"           # punct spacing: left | right | both | none;
    # respell: replace (the previous word) | insert; word: "verbatim" = never
    # auto-capitalized (a spoken address)
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
    "scratch_that": (
        "scratch that", "delete that",
        "scratched that",  # how recognizers often write it
    ),
    "new_line": ("new line",),
    "new_paragraph": ("new paragraph",),
    # Recase the last utterance, said on its own: "cap that" -> Title Case.
    # Not "all caps that" / "no caps that": "all" and "no" would wait on
    # every use for the rest of a phrase.
    "cap_that": ("cap that", "capitalize that"),
    "upper_that": ("uppercase that",),
    "lower_that": ("lowercase that",),
    "bullet": ("new bullet",),  # not "bullet point": that is a noun phrase
    "heading": ("new heading",),  # "# " — markdown, and Notion/Obsidian live
    "subheading": ("new subheading",),  # "## "
    "checkbox": ("new checkbox",),  # "- [ ] " — a task list item
    "number": ("new number",),  # "1. ", "2. ", … a numbered list
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
    "asterisk": ("*", "none", False),
    "hash sign": ("#", "right", False),
    "less than sign": ("<", "none", False),
    "greater than sign": (">", "none", False),
    "caret sign": ("^", "none", False),
}

# "emoji thumbs up" -> 👍. One code point each, so one Backspace removes
# one; [flow.vocabulary] can remap or add more.
DEFAULT_EMOJI: dict[str, str] = {
    "emoji thumbs up": "👍",
    "emoji thumbs down": "👎",
    "emoji smile": "🙂",
    "emoji grin": "😁",
    "emoji laughing": "😂",
    "emoji wink": "😉",
    "emoji sad": "😢",
    "emoji crying": "😭",
    "emoji thinking": "🤔",
    "emoji heart eyes": "😍",
    "emoji fire": "🔥",
    "emoji party": "🎉",
    "emoji check mark": "✅",
    "emoji cross mark": "❌",
    "emoji eyes": "👀",
    "emoji pray": "🙏",
    "emoji rocket": "🚀",
    "emoji clap": "👏",
    "emoji hundred": "💯",
    "emoji shrug": "🤷",
    "emoji wave": "👋",
    "emoji sparkles": "✨",
    "emoji star": "⭐",
    "emoji skull": "💀",
    "emoji facepalm": "🤦",
    "emoji ok hand": "👌",
    "emoji muscle": "💪",
}

_BREAKS = {"new_line": "\n", "new_paragraph": "\n\n"}
_MARKERS = {"bullet": "- ", "heading": "# ", "subheading": "## ", "checkbox": "- [ ] "}
_RECASE = {"cap_that": "title", "upper_that": "upper", "lower_that": "lower"}

SPELL_WORD = "spell"
CORRECT_WORD = "correct"

# Hesitation sounds a streaming recognizer writes down: dropped from what
# is typed ([flow] fillers). Only sounds — never words that carry meaning
# ("like", "so", "well", "hmm" in a chat).
DEFAULT_FILLERS = ("um", "umm", "uh", "uhh", "uhm", "erm")
_SENTENCE_STOPS = ".?!"

# Spoken addresses: "docs dot python dot org" -> docs.python.org, "liam at
# example dot com" -> liam@example.com. A run only becomes an address
# when it ends in one of these, so "meet at the office" stays prose.
TLDS = frozenset(
    "com org net io dev ai app co edu gov uk de fr ca info biz xyz sh gg tv "
    "eu nl se es jp au nz ch ly cc".split()
)  # never English words ("in", "at", "it", "to", "so", "me", "no", "be", "us")
_ADDRESS_GLUE = {"dot": ".", "at": "@", "slash": "/"}

# Spoken case formatters: "snake case user id" -> user_id. The formatter
# takes the plain words after it, up to punctuation, a command, or the end
# of the utterance (a pause), at most MAX_FORMATTED_WORDS of them.
FORMATTERS = {
    ("camel", "case"): "camel", ("pascal", "case"): "pascal", ("snake", "case"): "snake",
    ("kebab", "case"): "kebab", ("constant", "case"): "constant", ("title", "case"): "title",
    ("all", "caps"): "caps", ("no", "space"): "smash", ("dot", "case"): "dot",
}
_FORMATTER_FIRST = {first for first, _ in FORMATTERS}
MAX_FORMATTED_WORDS = 8
# Words the code registers compile into operators (flow/code.py) end a
# formatted run: "snake case user id equals five" -> user_id = 5.
FORMATTER_STOPS = frozenset(
    "equals plus minus times modulo arrow dot pipe star slash".split()
)
# In a code register a run also ends at a keyword or a spoken callable:
# "for snake case row count in range ten" -> for row_count in range(10.
CODE_FORMATTER_STOPS = FORMATTER_STOPS | frozenset(
    "in is not and or if elif else for while return import from as with def class "
    "lambda yield await async try except finally raise pass break continue global "
    "range print len str int float input enumerate sorted reversed abs min max sum "
    "type repr".split()
)
# Title case keeps these lowercase after the first word.
_TITLE_SMALL = frozenset("a an and as at but by for in nor of on or the to vs via".split())


def format_words(style: str, words: list[str]) -> str:
    words = [w for w in words if w]
    if style == "camel":
        return words[0] + "".join(w[:1].upper() + w[1:] for w in words[1:])
    if style == "pascal":
        return "".join(w[:1].upper() + w[1:] for w in words)
    if style == "snake":
        return "_".join(words)
    if style == "kebab":
        return "-".join(words)
    if style == "constant":
        return "_".join(words).upper()
    if style == "title":
        return " ".join(
            w if k and w in _TITLE_SMALL else w[:1].upper() + w[1:] for k, w in enumerate(words)
        )
    if style == "caps":
        return " ".join(words).upper()
    if style == "dot":
        return ".".join(words)
    return "".join(words)  # smash
# Spelled symbols that are also everyday words.
_AMBIGUOUS = {"a", "i", "one", "two", "four", "eight"}
_PENDING = "pending"


def _address_part(core: str) -> bool:
    """A spoken piece of a domain or a mailbox: letters, digits, hyphens."""
    return bool(core) and core.replace("-", "").isalnum() and core.isascii()


def _find_unquote(cores: list[str], start: int, end: int) -> Optional[tuple[int, int]]:
    """The first "unquote" or "end quote" in cores[start:end]: (index, length)."""
    for at in range(start, end):
        if cores[at] == "quote":
            return None  # another quote opens first: this one stays a word
        if cores[at] == "unquote":
            return at, 1
        if cores[at] == "end" and at + 1 < end and cores[at + 1] == "quote":
            return at, 2
    return None


def _core(token: str) -> str:
    return token.casefold().strip(_PUNCT_STRIP)


# Quotation marks a recognizer puts around words it hears as a name or a
# title: 'Select "Previous Word".' is still a command said on its own.
_QUOTES = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb\u201e"

# The wake word as a recognizer writes it: "VK", "V.K.", "V-K", "V K". Never
# a name or a word that only sounds close ("Vicky", "decay", "the k", "BK"):
# what follows a wake word is an instruction, never typed, so a false match
# would swallow dictation and hand it to [llm].
WAKE_ALIASES = {"vk": ("veekay",)}


def _bare(core: str) -> str:
    return core.replace(".", "").replace("-", "")


def _sentence_case(text: str) -> bool:
    """"List", "Twenty", "X": a capital the recognizer gave a sentence
    start. Not "GitHub", "TODO", "OK" or "iPhone", which are spelled so."""
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and text[:1].isupper() and not any(ch.isupper() for ch in letters[1:])


def _split_compounds(items: list[Item], unsplit: frozenset = frozenset(), oh: bool = True) -> tuple[list[Item], dict]:
    """Each "Twenty-five" as the words a speaker said, so the number folds
    read it as they read "twenty five" ("Twenty-five percent" -> "25%").
    _merge_compounds puts back together the parts of one that did not
    fold."""
    out: list[Item] = []
    chains: dict[int, tuple[list[Item], Item]] = {}
    for item in items:
        words = (
            split_compound(item.text)
            if item.kind == "word" and item.mode != "verbatim" and item.span[1] - item.span[0] == 1
            and item.span[0] not in unsplit
            else None
        )
        if words is None or (not oh and any(_core(word) == "oh" for word in words)):
            out.append(item)
            continue
        parts = [Item(kind="word", text=word, mode=item.mode, span=item.span) for word in words]
        out += parts
        chains[id(parts[0])] = (parts, item)
    return out, chains


def _merge_compounds(items: list[Item], chains: dict) -> list[Item]:
    out: list[Item] = []
    index = 0
    while index < len(items):
        chain = chains.get(id(items[index]))
        if chain is not None:
            parts, original = chain
            if all(index + k < len(items) and items[index + k] is part for k, part in enumerate(parts)):
                out.append(original)  # "Twenty-five people": it stays as written
                index += len(parts)
                continue
        out.append(items[index])
        index += 1
    return out


_PRONOUN = frozenset({"i", "i'm", "i'll", "i'd", "i've", "i\u2019m", "i\u2019ll", "i\u2019d", "i\u2019ve"})


DATE_MONTHS_SET = frozenset(DATE_MONTHS)


# Words a speaker restarts on ("the the", "I I"). Never ones a sentence
# can say twice ("had had", "that that", "is is", "told you you were",
# "log in in the morning", "turn it on on monday").
STUTTER_WORDS = frozenset(
    "the a an i to and we my of for at with i'm it's i'll we're they".split()
)
# After a numbered noun, these say the number was a count: "page two of
# three", "build one more", "the floor ten people stood on".
NOT_A_NUMBER_AFTER = NOT_A_YEAR_AFTER | {"of", "more", "thing", "things", "another", "less"}
_QUARTERS = {"one": 1, "two": 2, "three": 3, "four": 4}
_LEADING_WORD = re.compile(r"[a-z]+")


def _clean(token: str) -> bool:
    """No punctuation attached at either end ("five," "...five")."""
    return token.strip(_PUNCT_STRIP) == token


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
        fillers=DEFAULT_FILLERS,
        addresses: bool = True,
        formatters="code",
        code: bool = False,
        language: str = "en",
    ):
        self.enabled = enabled
        self._address_on = addresses
        # "code" (default): only in code and terminal registers, where "no
        # space" or "all caps" are never prose; "everywhere"; "off".
        mode = {True: "code", False: "off"}.get(formatters, formatters)
        self._formatters = mode == "everywhere" or (mode == "code" and code)
        self._formatter_stops = CODE_FORMATTER_STOPS if code else FORMATTER_STOPS
        self._spelling = spelling
        self._fillers = frozenset(
            str(f).strip().casefold() for f in (fillers or ()) if str(f).strip()
        ) - frozenset(
            LANGUAGE_PACKS.get(str(language or "en").lower(), {}).get("not_fillers", ())
        )
        # "the the meeting": a stutter is dropped wherever hesitations are
        self._stutters = bool(self._fillers) and not code and str(language or "en").lower() == "en"
        self._nav = nav
        self._wake = (wake_word or "").strip().casefold()
        # terminals and code: no sentence capitals, no sentence stops but spoken ones
        self._code = code
        numbers = numbers if numbers in {"auto", "always", "off"} else "auto"
        self._numbers_on = numbers == "always" or (numbers == "auto" and numbers_on)
        self._numbers_min = 0 if numbers == "always" else numbers_min
        # Prose: only "twenty five percent", "five dollars", "three pm".
        self._units_on = numbers == "auto" and not self._numbers_on and not code

        pack = LANGUAGE_PACKS.get(str(language or "en").lower(), LANGUAGE_PACKS["en"])
        self._guards = {
            word: guard for word, guard in PUNCT_GUARDS.items() if word in pack["punctuation"]
        }
        merged_commands = dict(DEFAULT_COMMANDS)
        for action, phrases in pack["commands"].items():
            merged_commands[action] = merged_commands.get(action, ()) + tuple(phrases)
        for action, phrases in (commands or {}).items():
            if action not in DEFAULT_COMMANDS:
                continue
            if isinstance(phrases, str):
                phrases = [phrases]
            merged_commands[action] = tuple(str(p) for p in phrases if str(p).strip())

        merged_punct = {**DEFAULT_PUNCTUATION, **pack["punctuation"]}
        if code:
            # Terminals and code get keyable marks only: a character with no
            # key is pasted, and a terminal must never be sent a paste.
            merged_punct = {k: (g.replace("\u202f", ""), m, e) for k, (g, m, e) in merged_punct.items()}
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
        # no paste into a terminal, as above; "Imoji rocket" is how a
        # recognizer may hear "emoji rocket" (not an English word)
        emoji = {} if code else {
            **DEFAULT_EMOJI, **{"imoji" + phrase[5:]: glyph for phrase, glyph in DEFAULT_EMOJI.items()},
        }
        for phrase, replacement in {**emoji, **(vocabulary or {})}.items():
            tokens = _phrase_tokens(str(phrase))
            if tokens:
                self._phrases[tokens] = ("vocab", str(replacement))
        # an emoji is a symbol, like a spoken period: said on its own it
        # takes no sentence stop from the recognizer
        user = {_phrase_tokens(str(phrase)) for phrase in (vocabulary or {})}
        self._emoji = frozenset(t for t in map(_phrase_tokens, emoji) if t and t not in user)

        self._max_phrase = max(
            (len(p) for p in self._phrases), default=1
        )

    @property
    def waits_for_final(self) -> frozenset:
        """Words decided only once the next word is final ("punto"): the
        engine never expires their hold."""
        return frozenset(self._guards)

    def phrases(self) -> list[tuple[str, str, object]]:
        """Every spoken phrase this grammar knows, after config merges:
        (phrase, kind, payload) with kind "command" | "punct" | "vocab"."""
        return [(" ".join(tokens), kind, payload) for tokens, (kind, payload) in self._phrases.items()]

    _TRAILING_SPECS = {
        ".": ("left", True), ",": ("left", False), "!": ("left", True),
        "?": ("left", True), ";": ("left", False), ":": ("left", False),
    }

    def _parse_formatter(self, tokens, cores, index: int, limit: int, decided: bool):
        """"camel case get user name" at `index`: ([items], next index);
        _PENDING while the words could still continue; None when this is
        not a formatter ("the camel", "all caps" with no words after)."""
        if index + 1 >= limit:
            return None if decided else _PENDING  # "snake" may become "snake case"
        style = FORMATTERS.get((cores[index], cores[index + 1]))
        if style is None:
            return None
        cursor, words, stop = index + 2, [], ""
        while cursor < limit and len(words) < MAX_FORMATTED_WORDS:
            core = cores[cursor]
            if (
                not core
                or self.wake_at(cores, cursor)
                or core in self._fillers
                or (words and core in self._formatter_stops)
                or (cores[cursor], cores[cursor + 1] if cursor + 1 < len(cores) else "") in FORMATTERS
                or core == "unquote"
                or (core == "end" and cursor + 1 < len(cores) and cores[cursor + 1] == "quote")
            ):
                break
            entry, _ = self._match_phrase(cores, cursor, limit - cursor)
            if entry is not None:
                break
            words.append(core)
            token = tokens[cursor]
            stop = token[len(token.rstrip(_PUNCT_STRIP)):]
            cursor += 1
            if stop:
                break  # "user id," — the comma ends the run
        if cursor >= limit and not decided and not stop and len(words) < MAX_FORMATTED_WORDS:
            return _PENDING  # more words may come before the pause
        if not words:
            return None
        span = (index, cursor)
        out = [Item(kind="word", text=format_words(style, words), mode="verbatim", span=span)]
        out.extend(self._trailing_punct(tokens[cursor - 1], span))
        return out, cursor

    def _address(self, cores: list[str], index: int, limit: int, *, decided: bool):
        """A spoken domain or email starting at `index`, read no further
        than `limit`: (text, end); _PENDING while an undecided tail could
        still become one; None. Shape: part ("dot" part)* ["at" part
        ("dot" part)*], ending in "dot" + a top-level domain — the longest
        such run wins ("example dot co dot uk"). A domain may go on as a
        path: ("slash" part ["dot" part]*)* — "example dot com slash docs"."""
        if not self._address_on or index + 1 >= limit:
            return None  # a lone word at the tail is just a word, for now
        first = cores[index]
        if (
            not _address_part(first)
            or first in _ADDRESS_GLUE
            or first in self._fillers
            or first in COMMON_LOWER  # "the dot com bubble", "a red dot co…"
        ):
            return None
        if cores[index + 1] not in _ADDRESS_GLUE:
            return None
        parts, seps = [first], []
        cursor, best = index + 1, None
        while True:
            if cursor >= limit:
                return best if decided else _PENDING  # the run touches an open tail
            sep = cores[cursor]
            if sep == "slash" and (best is None or "@" in seps):
                return best  # a path only follows a whole domain, never a mailbox
            if sep not in _ADDRESS_GLUE or (sep == "at" and ("@" in seps or "/" in seps)):
                return best
            if cursor + 1 >= limit:
                return best if decided else _PENDING
            part = cores[cursor + 1]
            if not _address_part(part) or (sep == "dot" and "/" in seps and part in COMMON_LOWER):
                return best  # "…slash docs dot then we leave": "then" is a word
            seps.append(_ADDRESS_GLUE[sep])
            parts.append(part)
            cursor += 2
            if "/" in seps or (
                seps[-1] == "." and part in TLDS and (seps.count("@") == 0 or seps.index("@") < len(seps) - 1)
            ):
                best = ("".join(p + q for p, q in zip(parts, seps + [""])), cursor)

    @staticmethod
    def _filler_stop(token: str, items: list[Item], span: tuple[int, int]) -> None:
        """A sentence end the recognizer attached to a hesitation ("… so,
        um.") still ends the sentence — after a word, and only once."""
        stop = next((ch for ch in token[len(token.rstrip(_PUNCT_STRIP)):] if ch in _SENTENCE_STOPS), "")
        if not stop or not items:
            return
        before = next((item for item in reversed(items) if item.kind != "filler"), None)
        if before is None or before.kind != "word" or before.text[-1:] in _SENTENCE_STOPS:
            return
        items.append(Item(kind="punct", text=stop, mode="left", sentence_end=True, span=span))

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
        return self.wake_at([_core(token)], 0) == 1

    def wake_at(self, cores: list[str], index: int) -> int:
        """How many tokens at `index` spell the wake word (0 = none)."""
        wake = _bare(self._wake)
        if not wake or index >= len(cores):
            return 0
        core = _bare(cores[index])
        if core and (core == wake or core in WAKE_ALIASES.get(wake, ())):
            return 1
        if len(wake) == 2 and core == wake[0] and index + 1 < len(cores) and _bare(cores[index + 1]) == wake[1]:
            return 2  # "V K"
        return 0

    def wake_in(self, tokens: list[str], index: int) -> int:
        """How many of these raw tokens at `index` spell the wake word."""
        return self.wake_at([_core(token) for token in tokens], index)

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
        commits: tuple[int, ...] = (),
        unsplit: tuple[int, ...] = (),
    ) -> ParseResult:
        """Parse raw tokens into items.

        With flush=False, trailing tokens that might still grow into a
        phrase (or extend a number run) are reported via `pending_from`
        and produce no items. flush=True resolves everything — the stop
        path uses it.

        `frozen` is the engine's committed-token fence: no phrase may span
        it. Tokens before it were already committed under some parse, and
        `commits` — the end of each committed item, ascending — lets this
        parse reproduce those items exactly: below the fence every item is
        parsed within its own span, so two words committed one at a time
        never merge into the phrase they would have formed together, and
        later tokens never retroactively complete a longer phrase.

        `settled` is how many tokens the provider has finalized. A spelled
        run or a navigation command starting inside them is decided there:
        it never waits for, or grows into, the next segment. `bounds` are
        the segment boundaries (token counts at each final, ascending): a
        command is decided against the segment it started in, so a verb
        that closed one segment as a word stays a word when the next
        segment arrives — whatever that segment says.

        `unsplit` are the tokens committed as written although they spell
        a number with hyphens ("three-thirty" before "euros"): the context
        that kept them from folding may be past the fence now, so they are
        never read as numbers again.
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

        # Where the committed item holding each token ends, computed once.
        ends: list[int] = []
        for end in commits:
            while len(ends) < min(end, frozen):
                ends.append(min(end, frozen))
        while len(ends) < frozen:
            ends.append(frozen)

        def item_end(at: int) -> int:
            return ends[at] if at < frozen else frozen

        close_quote: dict[int, int] = {}  # where an "unquote" closes, and its length
        while index < len(tokens):
            core = cores[index]
            fence = item_end(index) - index if index < frozen else len(tokens)

            # Wake word: everything after it is an instruction, never
            # typed. It resolves only at finalize; until then it holds the
            # tail back (the caption shows instruction-listening state).
            wake = self.wake_at(cores, index) if index >= frozen else 0
            if wake:
                if not flush:
                    pending_from = index
                    break
                instruction = " ".join(tokens[index + wake:]).strip()
                items.append(
                    Item(
                        kind="instruction",
                        text=instruction,
                        span=(index, len(tokens)),
                    )
                )
                index = len(tokens)
                break

            if core in self._fillers:
                # A hesitation sound renders nothing; a comma attached to it
                # goes with it.
                items.append(Item(kind="filler", span=(index, index + 1)))
                self._filler_stop(tokens[index], items, (index, index + 1))
                index += 1
                continue

            if self._stutters and core in STUTTER_WORDS and _clean(tokens[index]):
                # "the the meeting", "I I think", "the um the plan": the
                # repeat renders nothing — never across a segment end, where
                # a pause may yet decide the first one ended a sentence
                back = len(items) - 1
                while back >= 0 and items[back].kind == "filler":
                    back -= 1
                before = items[back] if back >= 0 else None
                if (
                    before is not None and before.kind == "word"
                    and before.span[1] - before.span[0] == 1
                    and tokens[before.span[0]].casefold() == tokens[index].casefold()
                    and not any(before.span[0] < bound <= index for bound in bounds)
                ):
                    items.append(Item(kind="filler", span=(index, index + 1)))
                    index += 1
                    continue

            if self._formatters and core in _FORMATTER_FIRST:
                limit, decided = self._limit(
                    index, len(tokens), frozen, settled, flush, bounds, item_end
                )
                formatted = self._parse_formatter(tokens, cores, index, limit, decided)
                if formatted == _PENDING:
                    pending_from = index
                    break
                if formatted is not None:
                    new_items, index = formatted  # type: ignore[misc]
                    items.extend(new_items)
                    continue

            # "quote ... unquote" / "quote ... end quote": the words between
            # in quotation marks. Only with words between ("his quote
            # unquote friend" stays prose), within one utterance.
            if index in close_quote:
                span = (index, index + close_quote[index])
                items.append(Item(kind="punct", text='"', mode="left", span=span))
                items.extend(self._trailing_punct(tokens[span[1] - 1], span))
                index = span[1]
                continue
            if core == "quote" and self.enabled:
                if (
                    index + 1 < len(cores) and cores[index + 1] == "unquote"
                    and not (index < frozen and item_end(index) == index + 1)
                ):
                    # "his quote unquote friend": both stay words, as one
                    # item — decided by "unquote", so they freeze together
                    # (a lone committed "quote" was an opening mark)
                    items.append(Item(kind="word", text=f"{tokens[index]} {tokens[index + 1]}", span=(index, index + 2)))
                    index += 2
                    continue
                end, decided = self._segment_end(index, len(tokens), settled, flush, bounds)
                if not decided:
                    pending_from = index  # the closer may still come, or go
                    break
                close = _find_unquote(cores, index + 2, end)
                if close is not None:
                    close_quote[close[0]] = close[1]
                    items.append(Item(kind="punct", text='"', mode="right", span=(index, index + 1)))
                    index += 1
                    continue
            if self._spelling and core == CORRECT_WORD and index in bounds:
                # "correct monday to friday", said on its own: the engine
                # swaps the last "monday" it typed. Decided at the end of
                # the segment, so it waits only when it starts one.
                limit, decided = self._limit(
                    index, len(tokens), frozen, settled, flush, bounds, item_end
                )
                if not decided:
                    pending_from = index
                    break
                rest = cores[index + 1:limit]
                split = next((k for k in range(1, len(rest) - 1) if rest[k] == "to"), None)
                # "Correct. Go to the store." / "Correct, I went to the bank":
                # an answer, not a command — the command has no punctuation
                # on "correct" or in what it corrects.
                if split is not None and _clean(tokens[index]) and all(
                    _clean(t) for t in tokens[index + 1:index + 2 + split]
                ):
                    old = " ".join(t.lower() for t in tokens[index + 1:index + 1 + split])
                    # the replacement as spoken: the engine renders it.
                    # "Correct Monday to Friday.": the recognizer's own stop
                    # ends the command, not the word ("period" said is kept)
                    new = " ".join(tokens[index + 2 + split:limit]).strip().rstrip(_PUNCT_STRIP)
                    if old and new:
                        items.append(Item(kind="correct", text=new, mode=old, span=(index, limit)))
                        index = limit
                        continue
            if self._spelling and core == SPELL_WORD:
                limit, decided = self._limit(
                    index, len(tokens), frozen, settled, flush, bounds, item_end
                )
                head = index + (2 if cores[index + 1:index + 2] == ["that"] else 1)
                if head >= limit:
                    # "spell that" ended its segment: the letters may come
                    # after a pause, in the next one. Behind the fence the
                    # run reads back exactly as it was committed.
                    if index < frozen:
                        limit, decided = item_end(index), True
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

            if self._nav and core.strip(_QUOTES) in NAV_VERBS:
                limit, decided = self._limit(
                    index, len(tokens), frozen, settled, flush, bounds, item_end
                )
                # 'Select "Previous Word".': quotes a recognizer added are
                # not part of a command (said mid-sentence it types as said)
                unquoted = [_core(c.strip(_QUOTES)) for c in cores[:limit]]
                command = parse_nav(unquoted, index, decided=decided)
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
            if entry is not None and consumed == 1 and core in self._guards:
                # "punto", "point", "Punkt" alone: a mark only where the
                # words around it don't make it ordinary speech, decided
                # once the next word is final (or the dictation ends).
                if index + 1 >= settled and not flush:
                    pending_from = index
                    break
                guard = self._guards[core]
                before = cores[index - 1] if index > 0 else ""
                after = cores[index + 1] if index + 1 < len(cores) else ""
                if (
                    before in guard["before"]
                    or after in guard["after"]
                    or (before in NUMBER_WORDS and after in NUMBER_WORDS)
                ):
                    entry = None
                    items.append(Item(kind="word", text=tokens[index], span=(index, index + 1)))
                    index += 1
                    continue
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
                    trailing = self._trailing_punct(tokens[index + consumed - 1], span)
                    if index in bounds and tuple(cores[index:index + consumed]) in self._emoji:
                        # "Emoji rocket." opening an utterance: a symbol, not a sentence
                        trailing = [p for p in trailing if not p.sentence_end]
                    items.extend(trailing)
                elif payload == "literal":
                    # Emit the next token verbatim, bypassing the grammar. A
                    # "literal" that was committed bare (its word never came
                    # before the fence closed) reads back bare.
                    target = index + consumed
                    if target >= len(tokens) or (index < frozen and target >= item_end(index)):
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
                elif payload in _MARKERS:
                    # "- ", "# ", … on a line of its own: the renderer adds
                    # the line break unless the caret is already at a line start.
                    items.append(Item(kind="break", text=_MARKERS[str(payload)], mode="bullet", span=span))
                elif payload == "number":
                    # "1. ", then "2. ": the renderer counts.
                    items.append(Item(kind="break", text="", mode="number", span=span))
                elif payload == "scratch_that":
                    items.append(Item(kind="scratch", span=span))
                elif payload in _RECASE:
                    items.append(Item(kind="recase", text=" ".join(tokens[index:index + consumed]),
                                      mode=_RECASE[str(payload)], span=span))
                else:  # a command with no stream effect (future actions)
                    items.append(Item(kind="word", text=tokens[index], span=(index, index + 1)))
                    index += 1
                    continue
                index += consumed
                continue

            limit = item_end(index) if index < frozen else len(tokens)
            address = self._address(cores, index, limit, decided=index < settled or flush or limit < len(tokens))
            if address == _PENDING:
                pending_from = index  # "liam at" may still become an address
                break
            if address is not None:
                text, end = address
                # mode "verbatim": an address is never auto-capitalized
                items.append(Item(kind="word", text=text, mode="verbatim", span=(index, end)))
                items.extend(self._trailing_punct(tokens[end - 1], (index, end)))
                index = end
                continue

            token = tokens[index]
            if self._fillers and token.endswith(","):
                # "we should, uh, ship it": the commas were the recognizer's
                # brackets around the hesitation, and go with it. The word
                # and the filler are one item, so they freeze together and
                # read back the same below the fence.
                limit = item_end(index) if index < frozen else len(tokens)
                after = index + 1
                if after < limit and cores[after] in self._fillers:
                    span = (index, after + 1)
                    items.append(Item(kind="word", text=token[:-1], span=span))
                    self._filler_stop(tokens[after], items, span)
                    index = after + 1
                    continue
                if after >= len(tokens) and index >= frozen and index >= settled and not flush:
                    pending_from = index  # the next word may be a hesitation
                    break
            items.append(Item(kind="word", text=token, span=(index, index + 1)))
            index += 1

        compounds: dict[int, tuple[Item, Item]] = {}
        if self._numbers_on or self._units_on:
            # in a terminal "oh" is a word, so "four-oh-two" stays as written
            items, compounds = _split_compounds(items, frozenset(unsplit), oh=not self._numbers_on)
        if self._numbers_on:
            items, number_pending = self._fold_numbers(
                items,
                flush=flush or pending_from is not None,
                frozen=frozen,
                item_end=item_end,
            )
            if number_pending is not None and pending_from is None:
                pending_from = number_pending
        elif self._units_on:
            items, pending_from = self._fold_units(
                items, frozen=frozen, item_end=item_end, pending_from=pending_from,
                flush=flush, settled=settled,
            )
        if compounds:
            items = _merge_compounds(items, compounds)
        if self._code and self.enabled:
            items = self._unprose(items, tokens, bounds)

        return ParseResult(items=items, pending_from=pending_from)

    def _unprose(self, items: list[Item], tokens: list[str], bounds: tuple[int, ...]) -> list[Item]:
        """Terminals and code take what was said, not the recognizer's prose:
        its sentence stops go ("List files." -> "list files"; a spoken
        "period" stays), and so does the capital it gave a sentence start
        ("For i in range" -> "for i in range"; "GitHub", "TODO" and "I"
        keep theirs)."""
        starts = set(bounds)
        out: list[Item] = []
        for item in items:
            before = out[-1] if out else None
            if item.kind == "punct" and item.text in (".", "?", "!") and before is not None and before.span == item.span:
                continue  # a stop attached to the word before, not said
            if item.kind != "word" or item.mode == "verbatim":
                out.append(item)
                continue
            text = item.text
            bare = text.rstrip(_SENTENCE_STOPS)
            if bare.strip(_PUNCT_STRIP):
                text = bare
            start = item.span[0]
            if (
                item.span[1] - start == 1 and start < len(tokens) and item.text == tokens[start]
                and (start == 0 or start in starts or tokens[start - 1].endswith(tuple(_SENTENCE_STOPS)))
                and _sentence_case(text) and _core(text) not in _PRONOUN
                and self._phrases.get((_core(text),), ("",))[0] != "vocab"
            ):
                text = text[:1].lower() + text[1:]
            out.append(item if text == item.text else replace(item, text=text))
        return out

    @staticmethod
    def _date(items: list[Item], at: int, check_after: bool = True) -> Optional[tuple[str, int]]:
        """"october sixth" at `at` -> ("October 6", index of the day's last
        word), or None. The day's trailing punctuation is kept; a day the
        month doesn't have, or an ordinal starting a noun phrase
        ("september second graders"), is not a date."""
        month = items[at].text
        if not _clean(month) or month.casefold() not in DATE_MONTHS:
            return None
        for size in (2, 1):
            last = at + size
            if last >= len(items) or any(it.kind != "word" for it in items[at + 1:last + 1]):
                continue
            words = [it.text for it in items[at + 1:last + 1]]
            if any(not _clean(w) for w in words[:-1]) or words[-1].lstrip(_PUNCT_STRIP) != words[-1]:
                continue
            day = parse_day([_core(w) for w in words])
            if day is None or day > month_days(month):
                continue
            after = items[last + 1] if last + 1 < len(items) else None
            if (
                check_after and after is not None and after.kind == "word" and _clean(words[-1])
                and _core(after.text) in NOT_A_DAY_AFTER
            ):
                return None
            suffix = words[-1][len(words[-1].rstrip(_PUNCT_STRIP)):]
            return f"{month[:1].upper()}{month[1:]} {day}{suffix}", last
        return None

    @staticmethod
    def _cents(items: list[Item], at: int) -> Optional[tuple[str, int]]:
        """"and fifty cents" at `at` -> (".50", index of "cents"), or None."""
        if at >= len(items) or items[at].kind != "word" or items[at].text.casefold() != "and":
            return None
        end = at + 1
        while (
            end < len(items) and items[end].kind == "word" and _clean(items[end].text)
            and _core(items[end].text) in NUMBER_WORDS - {"and", "point", "hundred", "thousand"}
        ):
            end += 1
        if end == at + 1 or end >= len(items) or items[end].kind != "word":
            return None
        if items[end].text.lstrip(_PUNCT_STRIP) != items[end].text:
            return None
        if _core(items[end].text) not in ("cent", "cents"):
            return None
        value = parse_cardinal([_core(it.text) for it in items[at + 1:end]])
        return (f".{value:02d}", end) if value is not None and 1 <= value <= 99 else None

    @staticmethod
    def _fold_units(
        items: list[Item],
        *,
        frozen: int,
        item_end=None,
        pending_from: Optional[int] = None,
        flush: bool = False,
        settled: int = 0,
    ) -> tuple[list[Item], Optional[int]]:
        """Prose folds of a spoken number: a unit right after it ("twenty
        five percent" -> "25%", "three pm" -> "3 PM"), a month before an
        ordinal ("october sixth" -> "October 6"), or seven or more digits
        read one by one ("555-1234").

        Nothing behind the fence changes: a run that starts there ends
        with the committed item it starts in, so it folds exactly as it
        was committed (as one token, or not at all). At the molten tail a
        run is held back while it might still grow or meet its unit: a
        digit run until it ends, a number before a pending word ("percent"
        might become "percent sign"), a time before "am" until the next
        word says it isn't the verb ("which one am I")."""
        result: list[Item] = []
        size = len(items)
        tail_open = not flush

        def molten(at: int) -> bool:
            return tail_open and at >= frozen and at >= settled

        index = 0
        after_date = -1  # the index right after a date with nothing attached ("June 5")

        def year_comma(at: int, span: tuple[int, int]) -> None:
            if at == after_date:  # "June 5, 1999"
                result.append(Item(kind="punct", text=",", mode="left", span=span))

        while index < size:
            item = items[index]
            # Fast path: most words can start no fold at all.
            lowered = item.text.casefold().strip(_PUNCT_STRIP)
            if item.kind != "word" or not (
                item.text[:1] in "0123456789"
                or lowered in NUMBER_WORDS
                or lowered in DATE_MONTHS_SET
                or lowered in ("q", "a")
            ):
                result.append(item)
                index += 1
                continue
            if lowered == "q":
                # "q three" -> "Q3": a quarter, one to four
                quarter = items[index + 1] if index + 1 < size and items[index + 1].kind == "word" else None
                committed_q = item.span[0] < frozen
                q_limit = (item_end(item.span[0]) if item_end is not None else frozen) if committed_q else None
                if quarter is None and _clean(item.text) and not committed_q and molten(item.span[0]) and index + 1 == size:
                    return result, item.span[0]  # the quarter may come next
                beyond = items[index + 2] if index + 2 < size and items[index + 2].kind == "word" else None
                if (
                    quarter is not None and _clean(item.text) and _core(quarter.text) in _QUARTERS
                    and not (beyond is not None and _clean(quarter.text) and _core(beyond.text) in NUMBER_WORDS)
                    and quarter.text.lstrip(_PUNCT_STRIP) == quarter.text
                    and (q_limit is None or quarter.span[1] <= q_limit)
                ):
                    suffix = quarter.text[len(quarter.text.rstrip(_PUNCT_STRIP)):]
                    result.append(Item(kind="word", text=f"Q{_QUARTERS[_core(quarter.text)]}{suffix}",
                                       mode="verbatim", span=(item.span[0], quarter.span[1])))
                    index += 2
                    continue
                result.append(item)
                index += 1
                continue
            committed = item.span[0] < frozen
            limit = (item_end(item.span[0]) if item_end is not None else frozen) if committed else None

            def inside(it: Item) -> bool:
                return limit is None or it.span[1] <= limit

            # a committed date was decided when it was typed: the noun check
            # ran then ("june first" + "graders" later stays June 1)
            date = Grammar._date(items, index, check_after=not committed) if item.kind == "word" else None
            if date is not None and not committed and date[1] == size - 1 and molten(items[date[1]].span[0]):
                return result, item.span[0]  # the next word says date or noun ("second graders")
            if date is not None and inside(items[date[1]]):
                result.append(Item(kind="word", text=date[0], span=(item.span[0], items[date[1]].span[1])))
                index = date[1] + 1
                after_date = index if _clean(items[date[1]].text) else -1
                continue

            # The run: number words with nothing attached ("five," ends a
            # thought), never starting on glue ("and", "oh"), never ending
            # on it ("five and percent"), never crossing the fence.
            # "a hundred and fifty dollars": "a" reads as "one" before a scale
            lead_a = (
                lowered == "a" and _clean(item.text) and index + 1 < size and items[index + 1].kind == "word"
                and _clean(items[index + 1].text) and _core(items[index + 1].text) in ("hundred", "thousand")
                and inside(items[index + 1])
            )
            end = index + 1 if lead_a else index
            while (
                end < size and items[end].kind == "word" and _clean(items[end].text)
                and inside(items[end])
                and (_core(items[end].text) in NUMBER_WORDS or (end > index and _core(items[end].text) == "oh"))
            ):
                end += 1
            if end > index and _core(item.text) == "and":
                end = index
            while end > index and _core(items[end - 1].text) in ("and", "point", "oh"):
                end -= 1
            # one digit token from the recognizer is a run too: "25 percent"
            if end == index and item.kind == "word" and _clean(item.text) and item.text[:1] in "0123456789":
                end = index + 1
            if end == index:
                before_noun = next((it for it in reversed(result) if it.kind != "filler"), None)
                if (
                    before_noun is not None and before_noun.kind == "word"
                    and before_noun.text.casefold() in NUMBERED_NOUNS and not _clean(item.text)
                    and item.text.lstrip(_PUNCT_STRIP) == item.text
                    and (label := fold_numbered([_core(item.text)])) is not None
                ):
                    # "page five.", "season one, episode two."
                    result.append(Item(kind="word", text=label + item.text[len(item.text.rstrip(_PUNCT_STRIP)):],
                                       span=item.span))
                    index += 1
                    continue
                result.append(item)
                index += 1
                continue
            before = result[-1] if result else None
            if before is not None and before.kind == "word" and _core(before.text) in NUMBER_WORDS - {"and"}:
                # the rest of a number with something attached ("...twenty
                # five percent"): half a number never folds
                result.extend(items[index:end])
                index = end
                continue
            words = [_core(it.text) for it in items[index:end]]
            if lead_a:
                words[0] = "one"

            scale = items[end] if end < size and items[end].kind == "word" else None
            if scale is not None and _clean(scale.text) and _core(scale.text) in SCALE_WORDS and inside(scale):
                if end + 1 == size and not committed and (
                    molten(scale.span[0]) or (pending_from is not None and scale.span[1] == pending_from)
                ):
                    return result, item.span[0]  # "two million" may yet be dollars
                money = items[end + 1] if end + 1 < size and items[end + 1].kind == "word" else None
                if (
                    money is not None and _core(money.text) in CURRENCY and inside(money)
                    and money.text.lstrip(_PUNCT_STRIP) == money.text
                ):
                    folded = fold_unit(words, _core(money.text), scale=True)
                    if folded is not None:
                        folded += " " + _core(scale.text) + money.text[len(money.text.rstrip(_PUNCT_STRIP)):]
                        result.append(Item(kind="word", text=folded, span=(item.span[0], money.span[1])))
                        index = end + 2
                        continue

            unit = items[end] if end < size and items[end].kind == "word" else None
            unit_core = _core(unit.text) if unit is not None else ""
            unit_end = end
            if (
                unit is not None and unit_core == "per" and _clean(unit.text) and end + 1 < size
                and items[end + 1].kind == "word" and _core(items[end + 1].text) == "cent"
                and items[end + 1].text.lstrip(_PUNCT_STRIP) == items[end + 1].text and inside(items[end + 1])
            ):
                unit_core, unit_end = "percent", end + 1  # "per cent", as a recognizer may write it
            if (
                unit is not None and unit_core in UNIT_WORDS and inside(unit)
                and unit.text.lstrip(_PUNCT_STRIP) == unit.text
            ):
                if unit_core == "am" and not committed:
                    after = items[end + 1] if end + 1 < size else None
                    if after is None and molten(unit.span[0]):
                        return result, item.span[0]  # "am" is a time or the verb: the next word decides
                    verb = after is not None and after.kind == "word" and _core(after.text) == "i"
                else:
                    verb = False
                folded = None if verb else fold_unit(words, unit_core)
                if folded is not None:
                    last, last_at = items[unit_end], unit_end
                    cents = Grammar._cents(items, end + 1) if folded[:1] in "$€" and "." not in folded else None
                    if cents is not None and inside(items[cents[1]]):
                        folded += cents[0]  # "five dollars and fifty cents" -> "$5.50"
                        last, last_at = items[cents[1]], cents[1]
                    if unit_core not in ("a.m", "p.m"):  # "percent." keeps its period
                        folded += last.text[len(last.text.rstrip(_PUNCT_STRIP)):]
                    result.append(Item(kind="word", text=folded, span=(item.span[0], last.span[1])))
                    index = last_at + 1
                    continue

            if end == size and not committed and pending_from is not None and items[end - 1].span[1] == pending_from:
                return result, item.span[0]  # its unit may be what is pending
            if (
                end == size and molten(item.span[0]) and end - index >= 2
                and all(w in DIGIT_WORDS or w == "oh" for w in words)
            ):
                return result, item.span[0]  # a number read digit by digit may still grow

            year = None
            if words[0] in ("nineteen", "twenty"):
                # a year: its last word may carry the stop ("…four."), after
                # an "oh" the run trimmed ("nineteen oh five.")
                at, tail = end, []
                if (
                    at < size and items[at].kind == "word" and _clean(items[at].text)
                    and _core(items[at].text) == "oh" and inside(items[at])
                ):
                    at, tail = at + 1, ["oh"]
                closer = items[at] if at < size and items[at].kind == "word" and inside(items[at]) else None
                attached = closer is not None and not _clean(closer.text)
                if attached and closer.text.lstrip(_PUNCT_STRIP) == closer.text:
                    year = fold_year(words + tail + [_core(closer.text)])
                    if year is not None:
                        year += closer.text[len(closer.text.rstrip(_PUNCT_STRIP)):]
                        year_comma(index, (item.span[0], closer.span[1]))
                        result.append(Item(kind="word", text=year, span=(item.span[0], closer.span[1])))
                        index = at + 1
                        continue
                if at == size and molten(item.span[0]) and (len(words) + len(tail) <= 2 or fold_year(words)):
                    return result, item.span[0]  # a year may still grow, or the next word says count
                year = None if tail else fold_year(words)
                after = items[end] if end < size and items[end].kind == "word" and inside(items[end]) else None
                if year is not None and after is not None and not committed:
                    lead = _LEADING_WORD.match(after.text.casefold())
                    if lead and lead.group(0) != after.text.casefold() and lead.group(0) in NUMBER_WORDS | {"oh"}:
                        year = None  # half a year never folds ("nineteen ninety nine's")
                    elif _core(after.text) in NOT_A_YEAR_AFTER:
                        year = None  # a count: "nineteen forty people"
            at_time = None
            # the word before, past a hesitation or a dropped stutter ("at at")
            prev = next((it for it in reversed(result) if it.kind != "filler"), None)
            if year is None and prev is not None and prev.kind == "word" and prev.text.casefold() == "at":
                # "meet at three thirty" -> "at 3:30"; a bare hour stays a word
                at, tail = end, []
                if at < size and items[at].kind == "word" and _clean(items[at].text) and _core(items[at].text) == "oh":
                    at, tail = at + 1, ["oh"]  # "twelve oh five.": the run trimmed its "oh"
                closer = items[at] if at < size and items[at].kind == "word" and inside(items[at]) else None
                if (
                    closer is not None and not _clean(closer.text)
                    and closer.text.lstrip(_PUNCT_STRIP) == closer.text
                    and (stamp := fold_clock(words + tail + [_core(closer.text)])) is not None
                ):
                    stamp += closer.text[len(closer.text.rstrip(_PUNCT_STRIP)):]  # "…thirty." keeps its stop
                    result.append(Item(kind="word", text=stamp, span=(item.span[0], closer.span[1])))
                    index = at + 1
                    continue
                if at == size and molten(item.span[0]) and (len(words) + len(tail) <= 2 or fold_clock(words)):
                    return result, item.span[0]  # it may grow, or the next word says count
                at_time = fold_clock(words)
                after = items[end] if end < size and items[end].kind == "word" and inside(items[end]) else None
                if after is not None and _core(after.text) in ("o'clock", "o\u2019clock"):
                    at_time = None  # never "3:30 o'clock"
                if at_time is not None and after is not None and not committed and (
                    _core(after.text) in NOT_A_YEAR_AFTER or not _clean(after.text) and _LEADING_WORD.match(
                        after.text.casefold()) and _LEADING_WORD.match(after.text.casefold()).group(0) in NUMBER_WORDS
                ):
                    at_time = None  # a count ("at three thirty people") or half a time
            numbered = None
            if (
                year is None and at_time is None and prev is not None and prev.kind == "word"
                and prev.text.casefold() in NUMBERED_NOUNS
            ):
                # "room four oh two" -> "room 402"; the last word may carry
                # the stop, after glue the run trimmed ("four oh two.")
                at, tail = end, []
                if (
                    at < size and items[at].kind == "word" and _clean(items[at].text)
                    and _core(items[at].text) in ("oh", "point", "and") and inside(items[at])
                ):
                    at, tail = at + 1, [_core(items[at].text)]
                closer = items[at] if at < size and items[at].kind == "word" and inside(items[at]) else None
                if (
                    closer is not None and not _clean(closer.text)
                    and closer.text.lstrip(_PUNCT_STRIP) == closer.text
                    and (label := fold_numbered(words + tail + [_core(closer.text)])) is not None
                ):
                    label += closer.text[len(closer.text.rstrip(_PUNCT_STRIP)):]
                    result.append(Item(kind="word", text=label, span=(item.span[0], closer.span[1])))
                    index = at + 1
                    continue
                if at == size and molten(item.span[0]):
                    return result, item.span[0]  # the number may still grow, or the next word says
                after = items[end] if end < size and items[end].kind == "word" else None
                lead = _LEADING_WORD.match(after.text.casefold()) if after is not None else None
                if lead and lead.group(0) in ("and", "point") and lead.group(0) == after.text.casefold():
                    # "page five and then" folds; "page five and six" is half a number
                    following = items[end + 1] if end + 1 < size and items[end + 1].kind == "word" else None
                    glue_lead = _LEADING_WORD.match(following.text.casefold()) if following is not None else None
                    lead = glue_lead if glue_lead and glue_lead.group(0) in NUMBER_WORDS | {"oh"} else None
                if len(words) == 1:
                    # one word ("page five", "the lesson one learns") folds
                    # only on its own stop, above — decided by itself, so it
                    # reads back the same once committed
                    pass
                elif committed:
                    numbered = fold_numbered(words)  # a multi-word item was folded when typed
                elif tail[:1] in (["oh"], ["point"]) or (lead and lead.group(0) in NUMBER_WORDS | {"oh"}):
                    pass  # half a number never folds ("room five twenty.", "one oh")
                elif after is not None and _core(after.text) in NOT_A_NUMBER_AFTER:
                    pass  # a count: "the floor ten people", "page twenty of thirty"
                else:
                    numbered = fold_numbered(words)
            if (
                year is None and at_time is None and numbered is None
                and all(w in DIGIT_WORDS or w == "oh" for w in words)
            ):
                # "call five five five one two three four.": a number read
                # digit by digit may end on the sentence's stop
                at, tail = end, []
                if (
                    at < size and items[at].kind == "word" and _clean(items[at].text)
                    and _core(items[at].text) == "oh" and inside(items[at])
                ):
                    at, tail = at + 1, ["oh"]
                closer = items[at] if at < size and items[at].kind == "word" and inside(items[at]) else None
                if (
                    closer is not None and not _clean(closer.text)
                    and closer.text.lstrip(_PUNCT_STRIP) == closer.text
                    and (phone := fold_digits(words + tail + [_core(closer.text)])) is not None
                ):
                    phone += closer.text[len(closer.text.rstrip(_PUNCT_STRIP)):]
                    result.append(Item(kind="word", text=phone, span=(item.span[0], closer.span[1])))
                    index = at + 1
                    continue
            digits = fold_digits(words)
            if digits is None and numbered is not None:
                digits = numbered
            if digits is None and at_time is not None:
                digits = at_time
            if digits is None and year is not None:
                digits = year
                year_comma(index, (item.span[0], items[end - 1].span[1]))
            if digits is not None:
                result.append(Item(kind="word", text=digits, span=(item.span[0], items[end - 1].span[1])))
            else:
                result.extend(items[index:end])  # a run that did not fold stays words, all of it
            index = end
        return result, pending_from

    @staticmethod
    def _segment_end(
        index: int, total: int, settled: int, flush: bool, bounds: tuple[int, ...]
    ) -> tuple[int, bool]:
        """The end of the utterance holding `index`, and whether it is
        decided (the recognizer closed it, or the dictation is ending)."""
        if index >= settled:
            return total, flush
        return next((bound for bound in bounds if bound > index), settled), True

    @staticmethod
    def _limit(
        index: int,
        total: int,
        frozen: int,
        settled: int,
        flush: bool,
        bounds: tuple[int, ...] = (),
        item_end=None,
    ) -> tuple[int, bool]:
        """How far an open-ended command at `index` may read, and whether
        its end is decided: inside the final segments it may not cross the
        end of the segment it started in and never waits; behind the
        committed fence it reads only its own committed item; in the
        molten tail it reads to the end and waits unless flushing."""
        if index >= settled and index >= frozen:
            return total, flush
        limit = max(settled, frozen)
        for bound in bounds:
            if bound > index:
                limit = min(limit, bound)
                break
        if index < frozen:
            limit = min(limit, item_end(index) if item_end is not None else frozen)
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
        item_end=None,
    ) -> tuple[list[Item], Optional[int]]:
        """Convert runs of consecutive number-word items into digit items.

        A number run still touching the molten tail is held back (it might
        keep growing) unless flushing. No run crosses the frozen fence or a
        committed item's end: a run committed as "23" folds to "23" again
        on every reparse, and a number word after it can never reach back
        and change it.
        """
        if item_end is None:
            def item_end(at: int) -> int:
                return frozen
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
                if run and run[0].span[0] < frozen and item.span[0] >= item_end(run[0].span[0]):
                    close_run(at_tail=False)  # the committed part folds alone
                before = result[-1] if result else None
                if (
                    not run and _core(item.text) in DIGIT_WORDS and before is not None
                    and before.kind == "punct" and before.text == "-" and before.mode == "none"
                ):
                    # "kill dash nine one two three four": a flag is one
                    # digit (-9 1234), decided by the dash behind it
                    run.append(item)
                    close_run(at_tail=False)
                    continue
                run.append(item)
            else:
                close_run(at_tail=False)
                result.append(item)
        # A run entirely behind the fence was decided when it was committed.
        close_run(at_tail=bool(run) and run[0].span[0] >= frozen)
        return result, pending_from


def grammar_from_config(
    config: dict,
    register,
    *,
    vocabulary: Optional[dict] = None,
    nav: Optional[bool] = None,
) -> "Grammar":
    """The Grammar the daemon dictates with, from the full config: one
    builder for the daemon, `voice-keyboard try` and `commands`, so they
    cannot drift apart. `vocabulary` replaces [flow.vocabulary] (the daemon
    merges in the personal dictionary); `nav` overrides [nav] enabled (the
    daemon also asks whether the injector can press chords)."""
    flow = config.get("flow", {})
    return Grammar(
        enabled=bool(flow.get("grammar", True)) and register.grammar_enabled,
        commands=flow.get("commands") or {},
        punctuation=flow.get("punctuation") or {},
        vocabulary=dict(flow.get("vocabulary") or {}) if vocabulary is None else vocabulary,
        wake_word=str(flow.get("wake_word", "vk")),
        numbers=str(flow.get("numbers", "auto")).lower(),
        numbers_on=register.numbers_on,
        numbers_min=register.numbers_min,
        spelling=bool(flow.get("spelling", True)),
        fillers=flow.get("fillers", DEFAULT_FILLERS),
        addresses=bool(flow.get("addresses", True)),
        formatters=str(flow.get("formatters", "code")).lower(),
        code=bool(register.compiler) or register.terminal,
        nav=bool(config.get("nav", {}).get("enabled", False)) if nav is None else nav,
        language=str(flow.get("language", "en")).lower(),
    )
