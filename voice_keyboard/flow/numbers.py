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
DIGIT_WORDS = frozenset(_DIGITS)

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


def parse_cardinal(words: list[str]) -> Optional[int]:
    """A complete cardinal ("fifty five" -> 55), or None."""
    return _parse_cardinal([w.casefold() for w in words])


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

        # The longest prefix of the run that reads as a number: "one
        # thousand hundred" is 1000 then "hundred", the same split it gets
        # when "one thousand" was typed before "hundred" was heard.
        parsed = None
        while end > index:
            run = [_core(tokens[k]) for k in range(index, end)]
            if run[-1] not in {"and", "point"}:
                parsed = parse_number_run(run)
                if parsed is not None and (end - index > 1 or abs(float(parsed)) >= min_value):
                    break
                parsed = None
            end -= 1
        if parsed is not None:
            # Trailing punctuation of the run's last token survives.
            tail = tokens[end - 1]
            suffix = tail[len(tail.rstrip(_PUNCT)):]
            result.append(parsed + suffix)
            index = end
        else:
            result.append(tokens[index])
            index += 1
    return result


# Prose keeps spoken numbers as words, except right before a unit that
# makes the reading certain: "twenty five percent" -> "25%", "five
# dollars" -> "$5", "three thirty pm" -> "3:30 PM".
UNIT_WORDS = frozenset({
    "percent", "dollar", "dollars", "euro", "euros", "yen", "am", "pm", "a.m", "p.m",
    "o'clock", "o\u2019clock",
})
# Currencies written before the amount. Not "pounds" (weight) or "francs".
CURRENCY = {"dollar": "$", "dollars": "$", "euro": "€", "euros": "€", "yen": "¥"}
# "three point two billion dollars" -> "$3.2 billion": a scale word between
# an amount and its currency stays a word after the figure.
SCALE_WORDS = frozenset({"million", "billion", "trillion"})
_MERIDIEM = {"am": "AM", "a.m": "AM", "pm": "PM", "p.m": "PM"}


def _clock(words: list[str]) -> Optional[str]:
    """"three" -> "3", "three thirty five" -> "3:35", "three oh five" ->
    "3:05"; None unless an hour one..twelve, then nothing, "oh" and a
    digit, or minutes ten..fifty-nine."""
    if not words or words[0] not in _UNITS or not 1 <= _UNITS[words[0]] <= 12:
        return None
    hour = _UNITS[words[0]]
    if len(words) == 1:
        return str(hour)
    if len(words) == 3 and words[1] == "oh" and words[2] in _DIGITS:
        return f"{hour}:0{_DIGITS[words[2]]}"
    minutes = _parse_cardinal(words[1:])
    if minutes is None or not 10 <= minutes <= 59 or words[1] == "and":
        return None
    return f"{hour}:{minutes:02d}"


def _ascii_number(token: str) -> bool:
    """"25", "2.5": ASCII digits only (str.isdigit takes "٢٥" and "²")."""
    whole, _, frac = token.partition(".")
    return bool(whole) and all(c in "0123456789" for c in whole + frac)


def fold_unit(words: list[str], unit: str, *, scale: bool = False) -> Optional[str]:
    """A number run and the unit word after it as one token, or None.

    `words` are spoken-number words (or one digit token from the
    recognizer: "25 percent" -> "25%"); spoken digit strings ("one two
    percent") are never amounts."""
    words = [w.casefold() for w in words]
    unit = unit.casefold()
    if unit in _MERIDIEM:
        clock = _clock(words)
        return f"{clock} {_MERIDIEM[unit]}" if clock else None
    if unit in ("o'clock", "o\u2019clock"):
        clock = _clock(words)  # "five o'clock" -> "5 o'clock"; never "5:30 o'clock"
        return f"{clock} {unit}" if clock and ":" not in clock else None
    if len(words) == 1 and _ascii_number(words[0]):
        amount = words[0]
    elif "point" in words or _parse_cardinal(words) is not None:
        amount = parse_number_run(words)
    else:
        return None
    if amount is None:
        return None
    if unit == "percent":
        return amount + "%"
    whole, dot, frac = amount.partition(".")
    if len(whole) > 3 and not whole.startswith("0"):
        whole = f"{int(whole):,}"  # "$1,500", "¥2,000"
    if len(frac) == 1 and not scale and CURRENCY[unit] in "$€":
        frac += "0"  # "$1.50", but "$3.2 billion"
    return CURRENCY[unit] + whole + dot + frac


# "october sixth" -> "October 6": a month, then an ordinal day. Only an
# ordinal: "in june twenty people came" stays words. Not "may" or
# "march", which are verbs ("you may first check").
DATE_MONTHS = (
    "january february april june july august september october november december".split()
)
ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30,
}


_MONTH_DAYS = {
    "january": 31, "february": 29, "april": 30, "june": 30, "july": 31, "august": 31,
    "september": 30, "october": 31, "november": 30, "december": 31,
}
# An ordinal that starts a noun phrase, not a day: "september second graders".
NOT_A_DAY_AFTER = frozenset(
    "grader graders grade half quarter time times place round floor class year".split()
)


def month_days(month: str) -> int:
    return _MONTH_DAYS.get(month.casefold(), 31)


def parse_day(words: list[str]) -> Optional[int]:
    """"sixth" -> 6, "twenty first" -> 21, "thirtieth" -> 30; None unless
    a day of a month spoken as an ordinal."""
    words = [w.casefold() for w in words]
    if len(words) == 1 and words[0] in ORDINALS:
        return ORDINALS[words[0]]
    if (
        len(words) == 2 and words[0] in ("twenty", "thirty")
        and words[1] in ORDINALS and ORDINALS[words[1]] <= 9
    ):
        day = _TENS[words[0]] + ORDINALS[words[1]]
        return day if day <= 31 else None
    return None


PHONE_MIN_DIGITS = 7


# A count, not a year: "nineteen forty people", "twenty thirty one minute laps".
NOT_A_YEAR_AFTER = frozenset(
    "people persons hours hour minutes minute seconds second page pages times items things "
    "dollars dollar euros euro yen percent pounds miles feet meters kilometers points words "
    "users students copies units calories kids years days weeks months".split()
)


def fold_year(words: list[str]) -> Optional[str]:
    """A year read in pairs: "nineteen eighty four" -> "1984", "nineteen oh
    five" -> "1905", "twenty twenty six" -> "2026". A 20xx year needs all
    three words and stops at 2039 ("twenty twenty" vision, "twenty fifty
    people" stay words)."""
    words = [w.casefold() for w in words]
    if len(words) not in (2, 3) or words[0] not in ("nineteen", "twenty"):
        return None
    century = 19 if words[0] == "nineteen" else 20
    rest = words[1:]
    if rest[0] == "oh":
        if len(rest) != 2 or _DIGITS.get(rest[1], 0) == 0:
            return None
        year = _DIGITS[rest[1]]
    elif rest[0] in _TENS:
        year = _TENS[rest[0]]
        if len(rest) == 2:
            if _DIGITS.get(rest[1], 0) == 0:
                return None
            year += _DIGITS[rest[1]]
    else:
        return None
    if century == 20 and (len(words) != 3 or year > 39):
        return None
    return f"{century}{year:02d}"


def fold_digits(words: list[str]) -> Optional[str]:
    """Seven or more digits read one by one -> a number: "five five five
    one two three four" -> "555-1234", ten -> "555-123-4567", other
    lengths joined. Fewer stay words (counting, "one two three go")."""
    words = [w.casefold() for w in words]
    if len(words) < PHONE_MIN_DIGITS or words[0] == "oh":
        return None
    if any(w not in _DIGITS and w != "oh" for w in words):
        return None  # "oh" reads as zero inside a number: "five five five oh one"
    values = [_DIGITS.get(w, 0) for w in words]
    steps = {b - a for a, b in zip(values, values[1:])}
    if steps in ({1}, {-1}):
        return None  # counting, not a number: "one two three four five six seven"
    digits = "".join(str(v) for v in values)
    if len(digits) == 7:
        return f"{digits[:3]}-{digits[3:]}"
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits
