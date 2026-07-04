"""Read-only local context for the brain: the current selection, and any
files the user explicitly names by path. Privacy modes control whether
file contents ever leave the machine; secret fields are excluded upstream.
"""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

from voice_keyboard import clipboard
from voice_keyboard.assistant.models import ContextChunk

_PATH_PATTERN = re.compile(
    r"(?P<path>(?:~|/|[A-Za-z]:\\)[^\s'\"<>`]+|(?:\.{1,2}/)[^\s'\"<>`]+)"
)
_MAX_FILE_BYTES = 1_000_000
_MAX_EXCERPT_CHARS = 3_000
_MAX_SELECTION_CHARS = 4_000


def _looks_textual(mime: str) -> bool:
    if mime.startswith("text/"):
        return True
    return mime in {
        "application/json", "application/javascript", "application/sql",
        "application/xml", "application/x-sh", "application/yaml",
    }


class ContextProvider:
    """Gathers selection + explicitly-mentioned files, confined to a home
    root. `privacy_mode="local"` never sends file contents."""

    def __init__(self, *, home_root: Path, privacy_mode: str = "local"):
        self.home_root = home_root.expanduser().resolve()
        self.privacy_mode = privacy_mode

    def selection_chunk(self) -> list[ContextChunk]:
        try:
            text = (clipboard.get_primary_text() or "").strip()
        except Exception:
            return []
        if not text:
            return []
        return [
            ContextChunk(
                kind="selection",
                title="Current selection",
                uri="selection:primary",
                text=text[:_MAX_SELECTION_CHARS],
            )
        ]

    def collect(self, query: str) -> tuple[list[ContextChunk], list[str]]:
        chunks: list[ContextChunk] = []
        warnings: list[str] = []
        if self.privacy_mode != "cloud":
            # local mode: never read file contents; note what was mentioned.
            for raw in self._mentioned_paths(query):
                warnings.append(
                    f"privacy=local: not reading {raw} (switch to cloud to send file contents)"
                )
            return chunks, warnings
        for raw in self._mentioned_paths(query):
            try:
                resolved = self._resolve_allowed(raw)
            except ValueError as exc:
                warnings.append(str(exc))
                continue
            if resolved.is_dir():
                chunks.append(self._directory_chunk(resolved))
            elif resolved.is_file():
                chunk, warning = self._file_chunk(resolved)
                if chunk is not None:
                    chunks.append(chunk)
                if warning:
                    warnings.append(warning)
            else:
                warnings.append(f"Local path does not exist: {resolved}")
        return chunks, warnings

    def _mentioned_paths(self, query: str) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for match in _PATH_PATTERN.finditer(query):
            raw = match.group("path").rstrip(".,:;)]}")
            if raw and raw not in seen:
                paths.append(raw)
                seen.add(raw)
        return paths

    def _resolve_allowed(self, raw_path: str) -> Path:
        path = Path(os.path.expandvars(raw_path)).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self.home_root)
        except ValueError as exc:
            raise ValueError(f"Refusing to read outside the home root: {resolved}") from exc
        return resolved

    def _directory_chunk(self, path: Path) -> ContextChunk:
        entries: list[str] = []
        try:
            children = sorted(
                path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
            )[:80]
            for child in children:
                entries.append(child.name + ("/" if child.is_dir() else ""))
        except OSError as exc:
            entries.append(f"Could not list directory: {exc}")
        return ContextChunk(
            kind="file",
            title=f"Directory: {path.name or str(path)}",
            uri=str(path),
            text="\n".join(entries),
        )

    def _file_chunk(self, path: Path) -> tuple[ContextChunk | None, str | None]:
        try:
            size = path.stat().st_size
        except OSError as exc:
            return None, f"Could not stat {path}: {exc}"
        if size > _MAX_FILE_BYTES:
            return None, f"Skipping large file (> {_MAX_FILE_BYTES} bytes): {path}"
        mime, _ = mimetypes.guess_type(path.name)
        if mime and not _looks_textual(mime):
            return None, f"Skipping non-text file: {path}"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return None, f"Could not read {path}: {exc}"
        excerpt = text[:_MAX_EXCERPT_CHARS]
        if len(text) > len(excerpt):
            excerpt += "\n[excerpt truncated]"
        return (
            ContextChunk(kind="file", title=path.name, uri=str(path), text=excerpt, line=1),
            None,
        )
