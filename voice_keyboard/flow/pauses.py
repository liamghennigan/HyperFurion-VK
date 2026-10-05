"""Pause punctuation: a recognizer's sentence end at a pause is a guess.

Streaming recognizers such as xAI's grok-voice-transcribe-2.0 cut the audio
into chunks wherever the speaker pauses and punctuate each chunk as if it
were a whole sentence. Thinking out loud — "so I was thinking about the
project … and how we could make it simpler" — comes back as "So I was
thinking about the project. And how we could make it simpler." The engine
keeps the period that ends a chunk (and the capital that starts the next
one) revisable until the words after the pause show whether the sentence
goes on. These rules settle the clear cases; a language model reviews the
rest when one is configured ([llm]), and anything it can't answer keeps
the recognizer's punctuation.

Pure logic: the engine owns the state, the daemon owns the network.
"""

import re
from dataclasses import dataclass
from typing import Optional

# What a reviewed pause may end with: nothing, or one mark.
ALLOWED_PUNCT = ("", ",", ".", "?", "!", ";", ":")
_SENTENCE_END = (".", "?", "!")
_STRIP = ".,!?;:\"'()[]{}“”‘’…*`"

# A plain word with exactly one trailing period: "project." but not
# "U.S.", "3.5.", "..." or "etc." — those periods aren't the pause's.
_WORD_PERIOD = re.compile(r"^[^\W_][\w'’-]*\.$")
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "etc", "inc", "ltd",
    "prof", "approx", "dept", "est", "fig", "vol", "co", "corp",
})
_FIRST_PERSON = frozenset({"i", "i'm", "i'll", "i've", "i'd", "i’m", "i’ll", "i’ve", "i’d"})

# The next word continues the sentence: no mark before it...
_JOIN_NEXT = frozenset({"and", "or", "nor", "because", "than", "whereas"})
# ...or a comma before it.
_COMMA_NEXT = frozenset({"but"})
# Words a sentence (practically) never ends on: a pause after one is the
# speaker thinking, not a full stop.
_OPEN_ENDINGS = frozenset({
    "a", "an", "the", "my", "your", "our", "their", "its", "every",
    "and", "or", "but", "nor", "because", "although", "whereas", "whether",
    "if", "than", "of", "to", "for", "with", "from", "at", "about", "into",
    "onto", "between", "very", "i", "we", "they", "he", "she",
})
# Everyday words that are lowercase mid-sentence — used by the rules to
# un-capitalize the word after a pause they join. (A language model's
# review decides capitalization itself.) Names, days and months are
# deliberately absent.
COMMON_LOWER = frozenset("""
a about above actually after again against all almost also although always
am an and another any anyone anything are around as ask at away back bad
basically be because been before being below best better between big bit
both bring build but by call came can could did do does doing done down
during each easy either else enough even ever every everyone everything
fast feel few find first fix for found from get gets getting give go goes
going gone good got great had has have having he her here him his how
however i if in instead into is it its just keep kind know last later least
less let like little long look lot made make makes making many may maybe me
might more most much must my need needs never new next no not nothing now of
off often old on once one only or other our out over own part people
perhaps please point pretty probably put quite rather really right run said
same say see seem seems set she should show since so some someone something
sometimes soon start still stuff such sure take than that the their them
then there these they thing things think this those though through time to
today together too tomorrow try trying two under until up us use used using
very want wanted wants was way we well went were what whatever when where
whether which while who whole why will with within without work working
would yeah yes yet you your
""".split())


@dataclass(frozen=True)
class PauseDecision:
    punct: str        # what follows the word before the pause ("" = nothing)
    lower_next: bool  # write the word after the pause in lowercase
    next_core: str    # that word, casefolded, when this was decided


def core(token: str) -> str:
    return token.strip(_STRIP).casefold()


def reviewable(token: str) -> bool:
    """A pause after `token` gets a second look: a plain word whose single
    trailing period the recognizer added (not an abbreviation's)."""
    return bool(_WORD_PERIOD.match(token)) and core(token) not in _ABBREVIATIONS


def lowerable(token: str) -> bool:
    """Capitalized only as a sentence start: "And", not "I", "API" or
    "McKinsey"."""
    letters = [ch for ch in token if ch.isalpha()]
    if not letters or not letters[0].isupper() or core(token) in _FIRST_PERSON:
        return False
    return all(ch.islower() for ch in letters[1:])


def apply(left: str, right: str, decision: PauseDecision) -> tuple[str, str]:
    """The two tokens around a pause, rewritten per `decision`."""
    if left.endswith("."):
        left = left[:-1] + decision.punct
    if (
        decision.lower_next
        and decision.punct not in _SENTENCE_END
        and core(right) == decision.next_core
        and lowerable(right)
    ):
        right = right[0].lower() + right[1:]
    return left, right


def keep(right: str) -> PauseDecision:
    """The recognizer's call: a sentence ends at the pause."""
    return PauseDecision(".", False, core(right))


def rule_decision(
    left: str,
    right: str,
    *,
    right_kind: str = "word",
    lower_seen: frozenset = frozenset(),
) -> tuple[PauseDecision, bool]:
    """Decide a pause from the words around it. Returns (decision,
    confident): an unconfident call is the recognizer's own period, to be
    reviewed by a language model when one is available."""
    nxt, prev = core(right), core(left)
    if right_kind == "punct":
        # Punctuation the speaker dictated replaces the recognizer's guess.
        return PauseDecision("", False, nxt), True
    if right_kind != "word":
        # "scratch that", "new line", a "vk, ..." instruction: the
        # sentence before it ended where the speaker paused.
        return keep(right), True
    if nxt in _COMMA_NEXT:
        return PauseDecision(",", lowerable(right), nxt), True
    if nxt in _JOIN_NEXT:
        return PauseDecision("", lowerable(right), nxt), True
    if prev in _OPEN_ENDINGS:
        lower = lowerable(right) and (nxt in COMMON_LOWER or nxt in lower_seen)
        return PauseDecision("", lower, nxt), True
    return keep(right), False


def parse_answer(answer: str, left: str, right: str) -> Optional[PauseDecision]:
    """Read a reviewer's reply — the two words around the pause, written as
    they should be ("project and", "today, but", "Friday. Let's"). None
    unless it is exactly that: the same two words, one known mark."""
    lines = (answer or "").replace("‖", " ").strip().strip("`").strip().splitlines()
    words = [word.strip("\"'“”‘’`*") for word in (lines[0] if lines else "").split()]
    want_left, want_right = core(left), core(right)
    for first, second in zip(words, words[1:]):
        if core(first) != want_left or core(second) != want_right:
            continue
        punct = first[len(first.rstrip(".,!?;:")):]
        if punct not in ALLOWED_PUNCT:
            return None
        lower = (
            punct not in _SENTENCE_END
            and second[:1].islower()
            and lowerable(right)
        )
        return PauseDecision(punct, lower, want_right)
    return None
