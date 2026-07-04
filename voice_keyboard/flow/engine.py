"""The molten dictation engine.

Consumes merged transcript snapshots from the STT stream and maintains the
single source of truth for what should be on screen:

    committed_render  — text frozen on screen; repairs never cross it
    molten items      — parsed but still revisable; rendered as preview
    pending tokens    — trailing tokens held back (incomplete command
                        phrase, growing number run, wake-word instruction)

A molten item commits when the provider finalizes it, when it survives the
stability horizon, or eagerly when it contains non-ASCII (never repair
across a clipboard-pasted run). Commits are monotonic: the only way
committed text shrinks is the user's own "scratch that", which rewinds to
a segment snapshot.

Pure logic — no IO, no clocks of its own. The daemon feeds transcripts,
tick timestamps, and reads `desired_text()`; the InjectionWorker converges
the screen toward it.
"""

import logging
import math
import unicodedata
from dataclasses import dataclass
from typing import Optional

from voice_keyboard.flow.grammar import Grammar, Item
from voice_keyboard.flow.registers import (
    Register,
    RenderState,
    initial_state,
    render_items,
)

logger = logging.getLogger(__name__)


@dataclass
class FlowConfig:
    live: bool = True
    stability_ms: int = 1500
    stability_updates: int = 2
    max_molten_chars: int = 160
    adaptive: bool = True


@dataclass(frozen=True)
class FinalResult:
    text: str          # full post-grammar text that should be on screen
    instruction: str   # wake-word instruction ("" if none)
    scratches: int     # segments discarded by "scratch that"


@dataclass
class _TokenMeta:
    core: str
    first_seen: float
    stable_count: int = 0


@dataclass(frozen=True)
class _Snapshot:
    render_len: int
    render_state: RenderState


def risky_backspace(text: str) -> bool:
    """True when char-counted backspacing over `text` may not match how the
    focused app groups grapheme clusters (astral plane, combining marks,
    ZWJ sequences)."""
    return any(
        ord(ch) > 0xFFFF or unicodedata.combining(ch) or ch in "‍️︎"
        for ch in text
    )


class FlowEngine:
    def __init__(
        self,
        config: FlowConfig,
        grammar: Grammar,
        register: Register,
    ):
        self._cfg = config
        self._grammar = grammar
        self._register = register

        self._tokens: list[str] = []
        self._meta: list[_TokenMeta] = []
        self._items: list[Item] = []
        self._pending_from: Optional[int] = None
        self._flush_pending = False

        self._committed_tokens = 0
        self._committed_items = 0
        self._committed_render = ""
        self._render_state: RenderState = initial_state(register)
        self._final_tokens = 0
        self._snapshots: list[_Snapshot] = [
            _Snapshot(render_len=0, render_state=self._render_state)
        ]
        self._instruction = ""
        self._scratches = 0
        self._rev_depth = 0.0  # adaptive: observed ASR revision depth, decaying

    # ------------------------------------------------------------- inputs

    def on_transcript(self, merged: str, *, is_final: bool, now: float) -> None:
        new_tokens = merged.split()
        if len(new_tokens) < self._committed_tokens:
            # The provider rewrote history below the committed floor; the
            # floor wins — treat the update as having no molten tail.
            new_tokens = self._tokens[:self._committed_tokens]

        old_molten = self._tokens[self._committed_tokens:]
        old_meta = self._meta[self._committed_tokens:]
        new_molten = new_tokens[self._committed_tokens:]

        prefix = 0
        while (
            prefix < len(old_molten)
            and prefix < len(new_molten)
            and old_molten[prefix].casefold() == new_molten[prefix].casefold()
        ):
            prefix += 1
        if old_molten:
            depth = len(old_molten) - prefix
            if depth > 0:
                self._rev_depth = max(self._rev_depth, float(depth))
        self._flush_pending = False

        merged_meta: list[_TokenMeta] = []
        for index, token in enumerate(new_molten):
            if index < prefix:
                meta = old_meta[index]
                meta.stable_count += 1
                merged_meta.append(meta)
            else:
                merged_meta.append(_TokenMeta(core=token.casefold(), first_seen=now))

        self._tokens = self._tokens[:self._committed_tokens] + new_molten
        self._meta = self._meta[:self._committed_tokens] + merged_meta
        if is_final:
            self._final_tokens = max(self._final_tokens, len(self._tokens))

        self._rev_depth *= 0.98
        self._reparse()
        self._commit_ready(now)
        if is_final:
            self._mark_segment_boundary()

    def on_tick(self, now: float) -> None:
        """Time-based commits between transcript updates, plus holdback
        expiry so a trailing half-phrase can't stall dictation forever."""
        if self._pending_from is not None and not self._pending_is_instruction():
            oldest = self._meta[self._pending_from].first_seen
            if now - oldest >= 2 * self._cfg.stability_ms / 1000.0:
                self._flush_pending = True
                self._reparse()
        self._rev_depth *= 0.995
        self._commit_ready(now)

    def finalize(self, merged: str, *, now: float) -> FinalResult:
        self.on_transcript(merged, is_final=True, now=now)
        self._flush_pending = True
        self._reparse()
        for item in list(self._items[self._committed_items:]):
            self._commit_item(item)
        return FinalResult(
            text=self._committed_render,
            instruction=self._instruction,
            scratches=self._scratches,
        )

    # ------------------------------------------------------------ outputs

    def desired_text(self) -> str:
        preview, _ = render_items(
            self._preview_items(), self._render_state, self._register
        )
        return self._committed_render + preview

    def caption(self) -> str:
        """The uncommitted tail for the overlay's live caption."""
        if self._pending_is_instruction():
            spoken = " ".join(self._tokens[self._pending_from + 1:])
            return f"⌁ {spoken}…" if spoken else "⌁ listening for instruction…"
        tail = " ".join(self._tokens[self._committed_tokens:])
        return tail

    @property
    def register(self) -> Register:
        return self._register

    @property
    def scratches(self) -> int:
        return self._scratches

    # ----------------------------------------------------------- internal

    def _preview_items(self) -> list[Item]:
        return [
            item
            for item in self._items[self._committed_items:]
            if item.kind in ("word", "punct", "break")
        ]

    def _pending_is_instruction(self) -> bool:
        if self._pending_from is None or self._pending_from >= len(self._tokens):
            return False
        return self._grammar.is_wake_word(self._tokens[self._pending_from])

    def _reparse(self) -> None:
        result = self._grammar.parse(
            self._tokens,
            flush=self._flush_pending,
            frozen=self._committed_tokens,
        )
        if result.items[:self._committed_items] != self._items[:self._committed_items]:
            # Deterministic parsing plus the frozen fence should make this
            # impossible; log loudly if an invariant slips.
            logger.warning("flow: committed items changed under reparse")
        self._items = result.items
        self._pending_from = result.pending_from

    def _effective_required_stability(self) -> int:
        if not self._cfg.adaptive:
            return self._cfg.stability_updates
        return max(self._cfg.stability_updates, min(6, math.ceil(self._rev_depth)))

    def _commit_ready(self, now: float) -> None:
        horizon_s = self._cfg.stability_ms / 1000.0
        required = self._effective_required_stability()

        while self._committed_items < len(self._items):
            item = self._items[self._committed_items]
            start, end = item.span
            if item.kind == "instruction":
                # Instructions are consumed at finalize, never mid-stream.
                break
            committable = end <= self._final_tokens
            if not committable:
                metas = self._meta[start:end]
                committable = all(
                    meta.stable_count >= required
                    and now - meta.first_seen >= horizon_s
                    for meta in metas
                )
                if (
                    not committable
                    and item.kind == "word"
                    and not item.text.isascii()
                ):
                    # Never repair across a clipboard-pasted run: commit as
                    # soon as the word survived one update.
                    committable = all(meta.stable_count >= 1 for meta in metas)
            if not committable:
                break
            self._commit_item(item)

        # Safety valve: an endlessly-revising provider must not grow the
        # repairable tail without bound.
        while (
            self._committed_items < len(self._items)
            and self._items[self._committed_items].kind != "instruction"
        ):
            preview, _ = render_items(
                self._preview_items(), self._render_state, self._register
            )
            if len(preview) <= self._cfg.max_molten_chars:
                break
            self._commit_item(self._items[self._committed_items])

    def _commit_item(self, item: Item) -> None:
        if item.kind == "scratch":
            self._apply_scratch()
        elif item.kind == "instruction":
            if item.text:
                self._instruction = item.text
        else:
            delta, self._render_state = render_items(
                [item], self._render_state, self._register
            )
            self._committed_render += delta
        self._committed_tokens = max(self._committed_tokens, item.span[1])
        self._committed_items += 1

    def _apply_scratch(self) -> None:
        target: Optional[_Snapshot] = None
        for snapshot in reversed(self._snapshots):
            if snapshot.render_len < len(self._committed_render):
                target = snapshot
                break
        if target is None:
            return
        removed = self._committed_render[target.render_len:]
        if risky_backspace(removed):
            logger.warning(
                "flow: refusing to scratch across complex Unicode (%d chars)",
                len(removed),
            )
            return
        self._committed_render = self._committed_render[:target.render_len]
        self._render_state = target.render_state
        while self._snapshots and self._snapshots[-1].render_len > target.render_len:
            self._snapshots.pop()
        self._scratches += 1

    def _mark_segment_boundary(self) -> None:
        if (
            self._snapshots
            and self._snapshots[-1].render_len == len(self._committed_render)
        ):
            return
        self._snapshots.append(
            _Snapshot(
                render_len=len(self._committed_render),
                render_state=self._render_state,
            )
        )
