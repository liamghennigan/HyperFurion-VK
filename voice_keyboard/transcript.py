"""Transcript reconciliation helpers shared by the daemon and the flow engine.

Streaming STT providers deliver overlapping partial transcripts; these
helpers stitch them into one coherent text without doubling words.
"""

import re

_TRANSCRIPT_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _transcript_words(text: str) -> list[tuple[str, int, int]]:
    return [
        (match.group(0).casefold(), match.start(), match.end())
        for match in _TRANSCRIPT_WORD_RE.finditer(text)
    ]


def _word_sequence_startswith(
    words: list[tuple[str, int, int]],
    prefix: list[tuple[str, int, int]],
) -> bool:
    return len(words) >= len(prefix) and [
        word for word, _, _ in words[:len(prefix)]
    ] == [word for word, _, _ in prefix]


def _word_sequence_endswith(
    words: list[tuple[str, int, int]],
    suffix: list[tuple[str, int, int]],
) -> bool:
    return len(words) >= len(suffix) and [
        word for word, _, _ in words[-len(suffix):]
    ] == [word for word, _, _ in suffix]


def _word_sequence_contains(
    words: list[tuple[str, int, int]],
    needle: list[tuple[str, int, int]],
) -> bool:
    if not needle:
        return True
    if len(needle) > len(words):
        return False
    needle_values = [word for word, _, _ in needle]
    for index in range(len(words) - len(needle) + 1):
        if [word for word, _, _ in words[index:index + len(needle)]] == needle_values:
            return True
    return False


def _word_prefix_overlap(
    current: list[tuple[str, int, int]],
    update: list[tuple[str, int, int]],
) -> int:
    max_overlap = min(len(current), len(update))
    for overlap in range(max_overlap, 0, -1):
        if [word for word, _, _ in current[-overlap:]] == [
            word for word, _, _ in update[:overlap]
        ]:
            return overlap
    return 0


def _dedupe_repeated_transcript_text(text: str, *, min_words: int = 4) -> str:
    """Collapse a transcript that is one whole phrase repeated twice."""
    words = _transcript_words(text)
    if len(words) < min_words * 2:
        return text

    word_values = [word for word, _, _ in words]
    for block_size in range(len(words) // 2, min_words - 1, -1):
        if block_size * 2 != len(words):
            continue
        if word_values[:block_size] == word_values[block_size:block_size * 2]:
            second_copy_start = words[block_size][1]
            return text[:second_copy_start].rstrip()

    return text


def _join_transcript_text(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if left[-1].isspace() or right[0].isspace():
        return f"{left}{right}"
    return f"{left} {right}"


def _merge_transcript_text(current: str, update: str) -> str:
    """Merge STT updates that may be full transcripts or finalized segments."""
    update = update or ""
    if not update:
        return current
    if not current:
        return update
    if update == current or update.startswith(current):
        return update
    if current.endswith(update):
        return current

    current_words = _transcript_words(current)
    update_words = _transcript_words(update)
    if current_words and update_words:
        if _word_sequence_startswith(update_words, current_words):
            return update
        if _word_sequence_endswith(current_words, update_words):
            return current
        if len(current_words) >= 3 and _word_sequence_contains(update_words, current_words):
            return update
        if len(update_words) >= 3 and _word_sequence_contains(current_words, update_words):
            return current

        word_overlap = _word_prefix_overlap(current_words, update_words)
        if word_overlap:
            prefix_end = current_words[-word_overlap][1]
            return _join_transcript_text(current[:prefix_end].rstrip(), update)

    max_overlap = min(len(current), len(update))
    for overlap in range(max_overlap, 0, -1):
        if current[-overlap:] == update[:overlap]:
            return f"{current}{update[overlap:]}"

    return _join_transcript_text(current, update)
