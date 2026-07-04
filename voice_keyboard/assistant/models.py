"""Value types shared across the assistant package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ContextChunk:
    """A piece of context handed to the brain: a memory, a file, a
    selection. Read-only; the brain never sees more than these."""

    kind: str  # memory | file | selection | interaction
    title: str
    uri: str
    text: str
    score: float = 0.0
    line: Optional[int] = None


@dataclass(frozen=True)
class Citation:
    """A visual-only source reference — shown, never spoken."""

    key: str
    kind: str
    title: str
    uri: str
    snippet: str = ""
    line: Optional[int] = None

    def compact(self) -> str:
        location = self.uri
        if self.line is not None:
            location = f"{location}:{self.line}"
        return f"[{self.key}] {self.title} - {location}"


@dataclass
class ConverseResult:
    """The brain's answer to one turn.

    `action` is a proposed command line the brain wants typed; it is only
    ever TYPED (never executed) and only when [assistant] can_act is on —
    the injector refuses Enter regardless.
    """

    text: str
    citations: list[Citation] = field(default_factory=list)
    audio: bytes = b""
    # `audio` is RAW s16le mono PCM at this rate (the realtime agent's
    # output format) — play it with play_pcm, never the MP3 path.
    audio_sample_rate: int = 24000
    action: str = ""
    brain: str = ""  # which brain answered: realtime | local
    warnings: list[str] = field(default_factory=list)
