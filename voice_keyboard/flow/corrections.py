"""Spoken self-corrections: "send it Tuesday, no wait, Wednesday".

People correct themselves mid-sentence. A grammar can't tell which words
a correction replaces ("I mean, it's fine" is no correction at all), so
the daemon asks [llm] to tidy the sentence ([flow] corrections) — but
only under a rule it can check: the tidy-up may DELETE words and nothing
else. Every word of the answer must appear in what was said, in order;
punctuation and capitals may change, words may not be added, swapped or
respelled. An answer that breaks the rule is thrown away and the text
stays exactly as dictated.

Pure logic: cue detection decides whether a dictation is worth a call at
all, `deletion_only` checks the answer. The daemon owns the network.
"""

import re

# Phrases people use to correct themselves. A dictation without one (or
# without a stuttered repeat, "the the") is never sent anywhere.
CUES = (
    "no wait", "wait no", "no no", "i mean", "sorry", "actually",
    "or rather", "let me rephrase", "correction", "oops",
)
# A correction comes after something to correct: "Sorry for the delay"
# opens a sentence; "Anna, sorry, Hannah" corrects one.
_CUE_RE = re.compile(r"\S\s+(?:" + "|".join(re.escape(cue) for cue in CUES) + r")\b")
_WORD_RE = re.compile(r"[\w'’-]+")

# Longest dictation worth tidying: beyond this, the stop would wait on the
# model for too long and a repair would backspace too far.
MAX_CHARS = 600
# The tidy-up removes a false start, not the message: keep at least this
# share of the words.
MIN_KEPT = 0.4


def words(text: str) -> list[str]:
    return [w.strip("'’-") for w in _WORD_RE.findall(text.casefold()) if w.strip("'’-")]


def needs_cleanup(text: str) -> bool:
    """A correction cue, or a stuttered repeat ("the the"), is present."""
    if not text or len(text) > MAX_CHARS or "\n" in text.strip():
        return False  # line structure is never sent for tidying
    lowered = " ".join(words(text))
    if _CUE_RE.search(lowered):
        return True
    said = words(text)
    return any(a == b for a, b in zip(said, said[1:]))


def deletion_only(original: str, cleaned: str) -> bool:
    """True when `cleaned` keeps some of `original`'s words, in order, and
    adds or changes none: a subsequence, word for word."""
    said, kept = words(original), words(cleaned)
    if not kept or len(kept) >= len(said):
        return False
    if len(kept) < MIN_KEPT * len(said):
        return False
    position = 0
    for word in kept:
        while position < len(said) and said[position] != word:
            position += 1
        if position == len(said):
            return False
        position += 1
    return True


SYSTEM_PROMPT = (
    "You tidy dictated text in which the speaker corrected themselves. "
    "Remove false starts, self-corrections (keep only the corrected "
    "version), and stuttered repeated words. Delete words ONLY: never add, "
    "reorder, rephrase or respell a word. Keep everything else exactly as "
    "dictated. Fix punctuation and capitals only where your deletions "
    "require it. If nothing needs deleting, return the text unchanged. "
    "Output only the text.\n\n"
    "text: Send it Tuesday, no wait, Wednesday.\n"
    "tidy: Send it Wednesday.\n"
    "text: The meeting is at 3, I mean 4.\n"
    "tidy: The meeting is at 4.\n"
    "text: I I think we should, sorry, we must ship it.\n"
    "tidy: I think we must ship it.\n"
    "text: I mean, it's fine as it is.\n"
    "tidy: I mean, it's fine as it is."
)
