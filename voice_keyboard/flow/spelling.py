"""Spelled-out words: "spell that n g i n x", "spell capital k eight s".

Recognizers hand spelled letters back in several shapes — single-letter
tokens ("N G I N X"), one hyphenated token ("N-G-I-N-X"), one capitalized
token ("NGINX"), NATO words ("november golf ...") — and a letter can be
marked upper case with "capital". `letters_at` reads one letter (or a hyphenated run) at a
token index; the grammar strings them together.

Pure functions, no state: the grammar's parse is deterministic, and this
must be too.
"""

from typing import Optional

MAX_SPELLED_LETTERS = 32

NATO = {
    "alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d",
    "echo": "e", "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i",
    "juliet": "j", "juliett": "j", "kilo": "k", "lima": "l", "mike": "m",
    "november": "n", "oscar": "o", "papa": "p", "quebec": "q", "romeo": "r",
    "sierra": "s", "tango": "t", "uniform": "u", "victor": "v",
    "whiskey": "w", "whisky": "w", "x-ray": "x", "xray": "x",
    "yankee": "y", "zulu": "z",
}

# Spoken single digits inside a spelled word ("k eight s" -> k8s).
DIGITS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

CAPITAL = {"capital", "cap", "uppercase", "upper"}

_STRIP = ".,!?;:"


def _one(core: str) -> Optional[str]:
    """One spelled symbol from a casefolded, punctuation-stripped token."""
    if len(core) == 1 and core.isalnum() and core.isascii():
        return core
    if core in NATO:
        return NATO[core]
    return DIGITS.get(core)


def letters_at(tokens: list[str], index: int) -> tuple[str, int]:
    """The letters spelled starting at `index`, and how many tokens they
    used; ("", 0) when the token there is not a spelled letter.

    A "capital" prefix upper-cases the next letter (it needs one: a bare
    trailing "capital" is not a letter). A hyphenated token spells every
    part ("N-G-I-N-X")."""
    if index >= len(tokens):
        return "", 0
    core = tokens[index].casefold().strip(_STRIP)
    if core in CAPITAL:
        if index + 1 >= len(tokens):
            return "", 0
        letter = _one(tokens[index + 1].casefold().strip(_STRIP))
        if letter is None or not letter.isalpha():
            return "", 0
        return letter.upper(), 2
    letter = _one(core)
    if letter is not None:
        return letter, 1
    if "-" in core and core not in NATO:
        parts = core.split("-")
        spelled = [_one(part) for part in parts]
        if len(parts) > 1 and all(spelled):
            return "".join(spelled), 1  # type: ignore[arg-type]
    # A recognizer that heard the letters as one word writes it in capitals
    # ("NGINX", "K8S"): those are the letters, spelled.
    raw = tokens[index].strip(_STRIP)
    if len(raw) >= 2 and raw.isascii() and raw.isalnum() and raw.isupper():
        return raw.lower(), 1
    return "", 0


def could_be_capital(token: str) -> bool:
    """A trailing "capital" might still get its letter in the next update."""
    return token.casefold().strip(_STRIP) in CAPITAL
