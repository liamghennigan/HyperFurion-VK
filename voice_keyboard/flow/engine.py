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

One exception to "final commits at once": the period a provider puts where
the speaker paused (flow/pauses.py). That word waits, molten, until the
words after the pause decide whether the sentence really ended there.

Pure logic — no IO, no clocks of its own. The daemon feeds transcripts,
tick timestamps, and reads `desired_text()`; the InjectionWorker converges
the screen toward it.
"""

import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from voice_keyboard.flow import pauses
from voice_keyboard.flow.grammar import Grammar, Item
from voice_keyboard.flow.registers import (
    Register,
    RenderState,
    initial_state,
    render_items,
)

logger = logging.getLogger(__name__)

# A pause's next word counts as settled after surviving an update or this
# long; a review is asked once this many words follow the pause (or the
# speaker stopped again, or this long passed).
PAUSE_SETTLE_S = 0.6
PAUSE_REVIEW_WORDS = 3
PAUSE_REVIEW_WAIT_S = 1.2
# A review that never answered stops holding the text back after this.
PAUSE_REVIEW_TIMEOUT_S = 4.0


@dataclass
class FlowConfig:
    live: bool = True
    stability_ms: int = 1500
    stability_updates: int = 2
    max_molten_chars: int = 160
    adaptive: bool = True
    # Punctuation where the speaker paused: "off" keeps the provider's,
    # "rules" settles the clear cases, "llm" also asks the daemon's
    # reviewer about the rest (review_requests / resolve_pause).
    pause_review: str = "off"


@dataclass(frozen=True)
class FinalResult:
    text: str          # full post-grammar text that should be on screen
    instruction: str   # wake-word instruction ("" if none)
    scratches: int     # segments discarded by "scratch that"
    # (heard, meant) for each "spell that ..." that replaced a word.
    corrections: tuple[tuple[str, str], ...] = ()


@dataclass
class _TokenMeta:
    core: str
    first_seen: float
    stable_count: int = 0


@dataclass(frozen=True)
class _Snapshot:
    render_len: int
    render_state: RenderState


@dataclass
class _Pause:
    """A provider sentence end at a pause; keyed by the index of the first
    token after the pause."""
    decision: Optional[pauses.PauseDecision] = None     # settled
    provisional: Optional[pauses.PauseDecision] = None  # shown meanwhile
    requested_at: Optional[float] = None                # review asked


@dataclass(frozen=True)
class PauseQuery:
    """A pause for the reviewer: the text before it (ending with the
    provider's period) and the words after it."""
    index: int
    before: str
    after: str


# The last word of the committed render, its trailing punctuation, and
# trailing whitespace: "... hello wrold." -> ("wrold", ".", "").
_LAST_WORD = re.compile(r"(\S+?)([.,!?;:)\]}\"'»”’]*)(\s*)\Z")

# Actions that rewrite committed text: they wait for a final transcript,
# never a stability guess, so a misheard partial can't fire them.
_FINAL_ONLY = ("respell",)


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
        # Segment ends (token indexes) whose snapshot waits for their last
        # word to commit — a pause can hold it back.
        self._segment_marks: list[int] = []
        self._pauses: dict[int, _Pause] = {}
        self._lower_seen: set[str] = set()
        self._instruction = ""
        self._scratches = 0
        self._corrections: list[tuple[str, str]] = []
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
        for index in range(max(1, self._committed_tokens + prefix), len(self._tokens)):
            if self._tokens[index][:1].islower():
                self._lower_seen.add(pauses.core(self._tokens[index]))
        if is_final:
            self._final_tokens = max(self._final_tokens, len(self._tokens))
            self._note_pause()

        self._rev_depth *= 0.98
        self._reparse()
        self._settle_pauses(now)
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
        self._settle_pauses(now)
        self._commit_ready(now)

    def finalize(self, merged: str, *, now: float) -> FinalResult:
        self.on_transcript(merged, is_final=True, now=now)
        self._flush_pending = True
        self._reparse()
        # Every pause gets its answer now: a review still out, or a pause
        # nobody asked about, falls back to the rules.
        for index, pause in sorted(self._pauses.items()):
            if pause.decision is None:
                pause.decision = self._rule_decision(index)[0]
        self._reparse()
        for item in list(self._items[self._committed_items:]):
            self._commit_item(item)
        return FinalResult(
            text=self._committed_render,
            instruction=self._instruction,
            scratches=self._scratches,
            corrections=tuple(self._corrections),
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
        tail = " ".join(self._view_tokens()[self._committed_tokens:])
        return tail

    @property
    def register(self) -> Register:
        return self._register

    @property
    def scratches(self) -> int:
        return self._scratches

    def pauses_pending(self) -> bool:
        return any(pause.decision is None for pause in self._pauses.values())

    def review_requests(self, now: float, *, final: bool = False) -> list[PauseQuery]:
        """Pauses ready for the reviewer (enough words after them, or the
        dictation is ending); each is handed out once."""
        if self._cfg.pause_review != "llm":
            return []
        queries: list[PauseQuery] = []
        for index, pause in sorted(self._pauses.items()):
            if pause.decision is not None or pause.requested_at is not None:
                continue
            if index >= len(self._tokens) or self._item_at(index) is None:
                continue
            if self._rule_decision(index)[1]:
                continue  # the rules are sure; settled without a review
            ready = (
                final
                or len(self._tokens) - index >= PAUSE_REVIEW_WORDS
                or index < self._final_tokens
                or now - self._meta[index].first_seen >= PAUSE_REVIEW_WAIT_S
            )
            if not ready:
                continue
            pause.requested_at = now
            view = self._view_tokens()
            queries.append(
                PauseQuery(
                    index=index,
                    before=" ".join(view[max(0, index - 30):index - 1] + [self._tokens[index - 1]]),
                    after=" ".join(self._tokens[index:index + 12]),
                )
            )
        return queries

    def resolve_pause(self, index: int, answer: Optional[str], *, now: float) -> bool:
        """The reviewer's reply for the pause before token `index` (None if
        it failed): its call if it is a clean one, else the rules'."""
        pause = self._pauses.get(index)
        if pause is None or pause.decision is not None:
            return False
        if index >= len(self._tokens):
            pause.requested_at = None  # the words after it were retracted
            return False
        decision = pauses.parse_answer(
            answer if isinstance(answer, str) else "",
            self._tokens[index - 1],
            self._tokens[index],
        )
        pause.decision = decision or self._rule_decision(index)[0]
        self._reparse()
        self._commit_ready(now)
        return True

    # ----------------------------------------------------------- internal

    def _preview_items(self) -> list[Item]:
        preview: list[Item] = []
        for item in self._items[self._committed_items:]:
            if item.kind in ("word", "punct", "break"):
                preview.append(item)
            elif item.kind == "respell" and item.mode == "insert":
                preview.append(Item(kind="word", text=item.text, span=item.span))
        return preview

    def _pending_is_instruction(self) -> bool:
        if self._pending_from is None or self._pending_from >= len(self._tokens):
            return False
        return self._grammar.is_wake_word(self._tokens[self._pending_from])

    def _view_tokens(self) -> list[str]:
        """The tokens as they should read: each pause's punctuation (and the
        capital after it) as decided, or as provisionally ruled."""
        if not self._pauses:
            return self._tokens
        view = list(self._tokens)
        for index, pause in sorted(self._pauses.items()):
            decision = pause.decision or pause.provisional
            if decision is not None and 0 < index < len(view):
                view[index - 1], view[index] = pauses.apply(
                    view[index - 1], view[index], decision
                )
        return view

    def _note_pause(self) -> None:
        """A final segment ending in a provider period: remember the pause."""
        index = len(self._tokens)
        if (
            self._cfg.pause_review not in ("rules", "llm")
            or not self._register.smart_caps
            or not self._grammar.enabled
            or index in self._pauses
            or index - 1 < self._committed_tokens
            or not pauses.reviewable(self._tokens[index - 1])
        ):
            return
        self._pauses[index] = _Pause()

    def _item_at(self, index: int) -> Optional[Item]:
        for item in self._items:
            if item.span[0] <= index < item.span[1]:
                return item
        return None

    def _rule_decision(self, index: int) -> tuple[pauses.PauseDecision, bool]:
        """The rules' call for the pause before token `index`."""
        if index >= len(self._tokens):
            return pauses.PauseDecision(".", False, ""), True  # nothing followed
        right = self._tokens[index]
        item = self._item_at(index)
        if item is not None and item.span[0] < index:
            # One phrase spans the pause ("hyper. Furion" = "HyperFurion").
            return pauses.PauseDecision("", False, pauses.core(right)), True
        return pauses.rule_decision(
            self._tokens[index - 1],
            right,
            right_kind=item.kind if item is not None else "word",
            lower_seen=frozenset(self._lower_seen),
        )

    def _settle_pauses(self, now: float) -> None:
        """Show each open pause per the rules, and settle it once the word
        after it has settled: the rules decide clear cases (all cases
        without a reviewer); unclear ones wait for the reviewer."""
        changed = False
        for index, pause in sorted(self._pauses.items()):
            if pause.decision is not None:
                continue
            if index >= len(self._tokens):
                if pause.provisional is not None:
                    pause.provisional = None
                    changed = True
                continue
            if self._item_at(index) is None:
                continue  # a phrase still forming after the pause
            decision, confident = self._rule_decision(index)
            if decision != pause.provisional:
                pause.provisional = decision
                changed = True
            meta = self._meta[index]
            settled = (
                index < self._final_tokens
                or meta.stable_count >= 1
                or now - meta.first_seen >= PAUSE_SETTLE_S
            )
            asked = pause.requested_at
            timed_out = (
                now - asked >= PAUSE_REVIEW_TIMEOUT_S
                if asked is not None
                else now - meta.first_seen >= PAUSE_REVIEW_WAIT_S + PAUSE_REVIEW_TIMEOUT_S
            )
            if settled and (confident or self._cfg.pause_review != "llm" or timed_out):
                pause.decision = decision
        if changed:
            self._reparse()

    def _holds_pause(self, start: int, end: int) -> bool:
        """True while an undecided pause sits right after this span."""
        return any(
            pause.decision is None and start < index <= end
            for index, pause in self._pauses.items()
        )

    def _reparse(self) -> None:
        result = self._grammar.parse(
            self._view_tokens(),
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
            if self._holds_pause(start, end):
                break  # the words after the pause will decide its period
            committable = end <= self._final_tokens
            if not committable and item.kind in _FINAL_ONLY:
                break
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
        start, end = item.span
        for index, pause in self._pauses.items():
            if pause.decision is None and start < index <= end:
                # Forced out by the molten-length valve: what shows, stays.
                pause.decision = pause.provisional or pauses.keep(
                    self._tokens[index] if index < len(self._tokens) else ""
                )
        if item.kind == "scratch":
            self._apply_scratch()
        elif item.kind == "respell" and item.mode == "replace":
            self._apply_respell(item.text)
        elif item.kind == "respell":
            delta, self._render_state = render_items(
                [Item(kind="word", text=item.text, span=item.span)],
                self._render_state,
                self._register,
            )
            self._committed_render += delta
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
        self._take_snapshots()

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

    def _apply_respell(self, spelled: str) -> None:
        """"spell that ...": swap the last committed word for the spelled
        one, keeping its trailing punctuation and its capital."""
        match = _LAST_WORD.search(self._committed_render)
        if match is None:
            logger.info("flow: nothing to respell")
            return
        token = match.group(1)
        heard = token.lstrip("\"'([{«“‘")  # an opening quote stays put
        start = match.start(1) + len(token) - len(heard)
        if not heard:
            return
        if risky_backspace(self._committed_render[start:]):
            logger.warning("flow: refusing to respell across complex Unicode")
            return
        if heard[:1].isupper() and spelled.islower():
            spelled = spelled[:1].upper() + spelled[1:]
        self._committed_render = (
            self._committed_render[:start] + spelled + match.group(2) + match.group(3)
        )
        # Segment snapshots after the word shift with it, so "scratch that"
        # still rewinds to the same boundaries.
        shift = len(spelled) - len(heard)
        self._snapshots = [
            snap
            if snap.render_len <= start
            else _Snapshot(
                render_len=snap.render_len + shift, render_state=snap.render_state
            )
            for snap in self._snapshots
        ]
        if heard != spelled:
            self._corrections.append((heard, spelled))

    def _mark_segment_boundary(self) -> None:
        mark = len(self._tokens)
        if not self._segment_marks or self._segment_marks[-1] != mark:
            self._segment_marks.append(mark)
        self._take_snapshots()

    def _take_snapshots(self) -> None:
        """Snapshot each segment end once all of its words are committed."""
        while self._segment_marks and self._segment_marks[0] <= self._committed_tokens:
            self._segment_marks.pop(0)
            if (
                self._snapshots
                and self._snapshots[-1].render_len == len(self._committed_render)
            ):
                continue
            self._snapshots.append(
                _Snapshot(
                    render_len=len(self._committed_render),
                    render_state=self._render_state,
                )
            )
