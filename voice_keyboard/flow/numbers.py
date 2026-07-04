"""Spoken-cardinal parsing: "one hundred twenty three" -> "123".

Covers 0..999_999, "point"-separated decimals ("three point one four" ->
"3.14"), and plain digit sequences ("one two seven" -> "127", handy for
IPs and phone numbers). Deliberately conservative: anything it does not
fully understand is left as spoken words, and single small words ("one",
"nine") are only converted in aggressive mode so prose like "no one knows"
survives untouched.
"""

from typing import Optional

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}

_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

_DIGITS = {word: value for word, value in _UNITS.items() if value <= 9}

NUMBER_WORDS = set(_UNITS) | set(_TENS) | {"hundred", "thousand", "and", "point"}

_PUNCT = ".,!?;:"


def _parse_cardinal(words: list[str]) -> Optional[int]:
    """Parse a complete cardinal word sequence; None if it isn't one."""
    if not words:
        return None
    total = 0
    current = 0
    seen_value = False
    for word in words:
        if word == "and":
            # "one hundred and five" — glue word, only valid mid-number.
            if not seen_value:
                return None
            continue
        if word in _UNITS:
            value = _UNITS[word]
            if value == 0:
                # "zero" only stands alone.
                if seen_value or len(words) > 1:
                    return None
                current = 0
            elif value >= 10:
                # Teens claim the whole tens+units slot.
                if current % 100 != 0:
                    return None
                current += value
            else:
                if current % 10 != 0 or current % 100 in range(10, 20):
                    return None
                current += value
            seen_value = True
        elif word in _TENS:
            if current % 100 != 0:
                return None
            current += _TENS[word]
            seen_value = True
        elif word == "hundred":
            if not seen_value or current == 0 or current >= 100:
                return None
            current *= 100
        elif word == "thousand":
            if not seen_value or current == 0 or current >= 1000:
                return None
            total += current * 1000
            current = 0
            seen_value = True
        else:
            return None
    return total + current if seen_value else None


def _parse_digit_sequence(words: list[str]) -> Optional[str]:
    """"one two seven" -> "127" — all words must be single digits."""
    if len(words) < 2 or any(word not in _DIGITS for word in words):
        return None
    return "".join(str(_DIGITS[word]) for word in words)


def parse_number_run(words: list[str]) -> Optional[str]:
    """Parse a run of spoken-number words into a digit string.

    "point" splits whole and fractional parts; fractional digits are read
    out one by one and must be zero..nine.
    """
    lowered = [w.casefold() for w in words]
    if "point" in lowered:
        split = lowered.index("point")
        whole, frac = lowered[:split], lowered[split + 1:]
        if not frac or "point" in frac or any(w not in _DIGITS for w in frac):
            return None
        whole_value = _parse_cardinal(whole) if whole else 0
        if whole_value is None:
            return None
        return f"{whole_value}." + "".join(str(_DIGITS[w]) for w in frac)
    value = _parse_cardinal(lowered)
    if value is not None:
        return str(value)
    return _parse_digit_sequence(lowered)


def _core(token: str) -> str:
    return token.casefold().strip(_PUNCT)


def convert_numbers(tokens: list[str], *, min_value: int = 0) -> list[str]:
    """Replace maximal runs of spoken-number words with digit strings.

    Single-word runs below `min_value` are left as words (prose keeps
    "five" but converts "twenty three"); multi-word runs always convert —
    several number words in a row is a clear signal.
    """
    result: list[str] = []
    index = 0
    while index < len(tokens):
        core = _core(tokens[index])
        if core not in NUMBER_WORDS or core in {"and", "point"}:
            result.append(tokens[index])
            index += 1
            continue

        # Greedily extend the run, then trim trailing glue words. A token
        # with attached punctuation ("four.") ends the run after itself.
        end = index
        while end < len(tokens) and _core(tokens[end]) in NUMBER_WORDS:
            end += 1
            if tokens[end - 1].rstrip(_PUNCT) != tokens[end - 1]:
                break
        while end > index and _core(tokens[end - 1]) in {"and", "point"}:
            end -= 1

        run = [_core(tokens[k]) for k in range(index, end)]
        parsed = parse_number_run(run)
        multi_word = end - index > 1
        if parsed is not None and (multi_word or abs(float(parsed)) >= min_value):
            # Trailing punctuation of the run's last token survives.
            tail = tokens[end - 1]
            suffix = tail[len(tail.rstrip(_PUNCT)):]
            result.append(parsed + suffix)
            index = end
        else:
            result.append(tokens[index])
            index += 1
    return result
