"""Visual citations: sources are shown, never spoken."""

from __future__ import annotations

from typing import Iterable

from voice_keyboard.assistant.models import Citation, ContextChunk

_PREFIX_BY_KIND = {"file": "F", "memory": "M", "selection": "S", "interaction": "I"}


def make_citations(chunks: Iterable[ContextChunk]) -> list[Citation]:
    counters: dict[str, int] = {}
    citations: list[Citation] = []
    seen: set[tuple[str, str, object]] = set()
    for chunk in chunks:
        identity = (chunk.kind, chunk.uri, chunk.line)
        if identity in seen:
            continue
        seen.add(identity)
        prefix = _PREFIX_BY_KIND.get(chunk.kind, "S")
        counters[prefix] = counters.get(prefix, 0) + 1
        citations.append(
            Citation(
                key=f"{prefix}{counters[prefix]}",
                kind=chunk.kind,
                title=chunk.title,
                uri=chunk.uri,
                snippet=chunk.text,
                line=chunk.line,
            )
        )
    return citations


def format_visual_citations(citations: list[Citation]) -> str:
    if not citations:
        return ""
    lines = ["Sources:"]
    for citation in citations:
        lines.append(f"  {citation.compact()}")
    return "\n".join(lines)
