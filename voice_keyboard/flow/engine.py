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

A navigation command ("select previous word") is a barrier: it fires only
when it is a whole final segment of its own — said with a pause before
and after — and then everything before it must be on screen before its
keys are pressed. The engine stops committing at the barrier; the daemon
converges the screen, presses the keys, and calls `complete_action()`,
which starts a fresh segment: the caret has moved, so nothing before the
command can be repaired or scratched any more.

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
from dataclasses import dataclass, replace
from typing import Optional

from voice_keyboard.flow import pauses
from voice_keyboard.flow.grammar import Grammar, Item
from voice_keyboard.flow.nav import FRESH_FIELD, GLUED
from voice_keyboard.flow.registers import (
    Register,
    RenderState,
    render_items,
)
from voice_keyboard.flow.registers import initial_state as _fresh_state

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
    text: str          # post-grammar text that should be on screen (since
    # the last navigation command, if any: see typed_before)
    instruction: str   # wake-word instruction ("" if none)
    scratches: int     # segments discarded by "scratch that"
    # (heard, meant) for each "spell that ..." that replaced a word.
    corrections: tuple[tuple[str, str], ...] = ()
    # A navigation command waiting at a barrier: `text` is what must be on
    # screen before its keys; call complete_action() after pressing them.
    action: Optional["NavAction"] = None
    # Text typed in earlier segments, before navigation moved the caret.
    typed_before: str = ""
    # "scratch that" said with nothing in this recording to take back: it
    # reaches for the previous dictation (the daemon decides if it can).
    reach_back: int = 0


_PRONOUN_I = re.compile(r"i(?:['’](?:m|ll|d|ve))?", re.IGNORECASE)


def _phrase_pattern(phrase: str) -> "re.Pattern[str]":
    """The spoken words of `phrase` as typed text: case-insensitive, whole
    words, any punctuation or spacing between them."""
    words = [re.escape(word) for word in phrase.split()]
    # never inside a word ("don’t"), never across a line break or list marker
    return re.compile(
        r"(?<![\w'’])" + r"(?:[^\w\n]|_)+".join(words) + r"(?![\w'’])", re.IGNORECASE
    )


def recase(text: str, mode: str) -> str:
    """"title": each word's first character a capital; "upper"; "lower"."""
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    return re.sub(r"(^|\s)(\S)", lambda m: m.group(1) + m.group(2).upper(), text)


@dataclass(frozen=True)
class NavAction:
    action: str   # e.g. "select:word:left", "press:tab"
    count: int = 1


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
_FINAL_ONLY = ("respell", "key", "recase", "correct")


def risky_backspace(text: str) -> bool:
    """True when char-counted backspacing over `text` may not match how the
    focused app groups grapheme clusters (astral plane, combining marks,
    ZWJ sequences)."""
    return any(
        ord(ch) > 0xFFFF or unicodedata.category(ch).startswith("M") or ch in "‍️︎"
        for ch in text
    )


class FlowEngine:
    def __init__(
        self,
        config: FlowConfig,
        grammar: Grammar,
        register: Register,
        *,
        initial_state: Optional[RenderState] = None,
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
        # A recording that continues text already at the caret starts with
        # that text's spacing and capitalization carried in.
        self._render_state: RenderState = initial_state or _fresh_state(register)
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
        self._reach_back = 0
        self._corrections: list[tuple[str, str]] = []
        # Token counts at each final: segment boundaries for navigation.
        self._segment_bounds: set[int] = {0}
        self._barrier: Optional[NavAction] = None
        self._last_action: Optional[NavAction] = None  # last command pressed
        # The fold state before the utterance "select that" covers: the
        # words that replace it continue from there.
        self._select_state: Optional[RenderState] = None
        self._typed_before = ""
        self._finalizing = False
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
            self._segment_bounds.add(len(self._tokens))
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
        if (
            self._pending_from is not None
            and not self._pending_is_instruction()
            and not self._pending_is_quote()
        ):
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
        self._finalizing = True
        return self._commit_rest()

    def _commit_rest(self) -> FinalResult:
        """At finalize: commit everything, stopping at a navigation
        barrier (the daemon resumes with complete_action)."""
        while self._barrier is None and self._committed_items < len(self._items):
            self._commit_item(self._items[self._committed_items])
        return FinalResult(
            text=self._committed_render,
            instruction=self._instruction if self._barrier is None else "",
            scratches=self._scratches,
            reach_back=self._reach_back,
            corrections=tuple(self._corrections),
            action=self._barrier,
            typed_before=self._typed_before,
        )

    def pending_action(self) -> Optional[NavAction]:
        """The navigation command waiting for the screen to catch up."""
        return self._barrier

    def complete_action(self, now: float, *, pressed: bool = True) -> Optional[FinalResult]:
        """The daemon pressed the barrier's keys — or refused them
        (pressed=False: the caret never moved, so nothing changes). Either
        way dictation resumes; after a press it starts a fresh segment.
        When finalizing, returns the next result (which may stop at
        another barrier)."""
        action = self._barrier
        if action is None:
            return self._commit_rest() if self._finalizing else None
        self._barrier = None
        if not pressed:
            if self._finalizing:
                return self._commit_rest()
            self._commit_ready(now)
            return None
        self._last_action = action
        self._typed_before += self._committed_render
        self._committed_render = ""
        state = self._render_state
        if action.action in FRESH_FIELD:
            # Tab / Escape / a page away: likely a different field.
            state = _fresh_state(self._register)
        elif action.action == "select:that" and self._select_state is not None:
            # The selection is the whole utterance, its leading space or
            # line break included: what replaces it picks up from before.
            state = self._select_state
        elif action.action.startswith(GLUED) or action.action.endswith(":start"):
            # The next word fills a selection or a gap, or starts a line:
            # no leading space.
            state = replace(state, glue_next=True)
        self._render_state = state
        self._snapshots = [_Snapshot(render_len=0, render_state=state)]
        if self._finalizing:
            return self._commit_rest()
        self._commit_ready(now)
        return None

    # ------------------------------------------------------------ outputs

    def desired_text(self) -> str:
        if self._barrier is not None:
            return self._committed_render  # converge, then press the keys
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

    def _pending_is_quote(self) -> bool:
        """A "quote" waiting for its "unquote": it waits for the utterance to
        close, never for the holdback expiry — committed as a word, it could
        not become an opening quotation mark when the closer came."""
        if self._pending_from is None or self._pending_from >= len(self._tokens):
            return False
        return self._tokens[self._pending_from].casefold().strip(".,!?;:") == "quote"

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
        if item is not None and item.kind == "filler":
            # "project. Um, and how": the word after the hesitation decides.
            nxt = index + 1
            while nxt < len(self._tokens):
                following = self._item_at(nxt)
                if following is None or following.kind != "filler":
                    break
                nxt += 1
            if nxt >= len(self._tokens) or self._item_at(nxt) is None:
                return pauses.keep(right), False
            right, item = self._tokens[nxt], self._item_at(nxt)
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
            settled=self._final_tokens,
            bounds=tuple(sorted(self._segment_bounds)),
            commits=tuple(item.span[1] for item in self._items[:self._committed_items]),
        )
        if result.items[:self._committed_items] != self._items[:self._committed_items]:
            # Deterministic parsing plus the frozen fence should make this
            # impossible; log loudly if an invariant slips.
            logger.warning("flow: committed items changed under reparse")
        self._items = result.items
        self._pending_from = result.pending_from
        # "Undo that." / "Cap that." / "Scratch that.": a recognizer's period
        # on a command is not a sentence pause to review — never hold it.
        for index in [i for i, pause in self._pauses.items() if pause.decision is None]:
            item = self._item_at(index - 1)
            if item is not None and item.kind in ("key", "recase", "scratch"):
                del self._pauses[index]

    def _effective_required_stability(self) -> int:
        if not self._cfg.adaptive:
            return self._cfg.stability_updates
        return max(self._cfg.stability_updates, min(6, math.ceil(self._rev_depth)))

    def _commit_ready(self, now: float) -> None:
        horizon_s = self._cfg.stability_ms / 1000.0
        required = self._effective_required_stability()

        while self._barrier is None and self._committed_items < len(self._items):
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
            self._barrier is None
            and self._committed_items < len(self._items)
            and self._items[self._committed_items].kind not in ("instruction",) + _FINAL_ONLY
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
        elif item.kind == "key":
            self._commit_key(item)
        elif item.kind == "recase":
            self._commit_recase(item)
        elif item.kind == "correct":
            self._commit_correct(item)
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
            if not self._committed_render and not self._typed_before:
                self._reach_back += 1
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

    def _commit_key(self, item: Item) -> None:
        """A navigation command fires only as a whole final segment; said
        mid-sentence it was dictation after all, and types as words."""
        start, end = item.span
        if start in self._segment_bounds and end in self._segment_bounds:
            count = self._last_utterance_length() if item.text == "select:that" else item.count
            self._barrier = NavAction(action=item.text, count=count)
            return
        words = [
            Item(kind="word", text=token, span=(index, index + 1))
            for index, token in enumerate(self._tokens[start:end], start)
        ]
        delta, self._render_state = render_items(words, self._render_state, self._register)
        self._committed_render += delta

    def _type_as_words(self, item: Item) -> None:
        """A command that turned out to be dictation: its tokens as words."""
        start, end = item.span
        words = [
            Item(kind="word", text=token, span=(index, index + 1))
            for index, token in enumerate(self._tokens[start:end], start)
        ]
        delta, self._render_state = render_items(words, self._render_state, self._register)
        self._committed_render += delta

    def _commit_correct(self, item: Item) -> None:
        """"correct monday to friday", said as a whole segment: the last
        "monday" typed in this recording becomes "friday" (its capitals
        kept), and the pair is a correction the learner can mine. Said
        mid-sentence, or with nothing to correct, it was dictation."""
        start, end = item.span
        whole = start in self._segment_bounds and end in self._segment_bounds
        matches = list(_phrase_pattern(item.mode).finditer(self._committed_render)) if whole else []
        if not matches:
            self._type_as_words(item)
            return
        match = matches[-1]
        heard = match.group(0)
        if risky_backspace(self._committed_render[match.start():]):
            logger.info("flow: not correcting across complex Unicode")
            return
        # Y as dictation: spoken punctuation, vocabulary, numbers
        spoken = self._grammar.parse(item.text.split(), flush=True).items
        meant, _ = render_items(
            spoken, replace(_fresh_state(self._register), capitalize_next=False), self._register
        )
        meant = meant.strip()
        if not meant:
            return
        if len(heard) > 1 and heard.isupper():
            meant = meant.upper()
        elif heard[:1].isupper() and not _PRONOUN_I.fullmatch(heard):
            meant = meant[:1].upper() + meant[1:]
        at = match.start()
        self._committed_render = self._committed_render[:at] + meant + self._committed_render[match.end():]
        shift = len(meant) - len(heard)
        self._snapshots = [
            snap if snap.render_len <= at
            # a boundary inside the replaced words falls back to its start
            else _Snapshot(render_len=len(self._committed_render[:at].rstrip()), render_state=snap.render_state)
            if snap.render_len < at + len(heard)
            else _Snapshot(render_len=snap.render_len + shift, render_state=snap.render_state)
            for snap in self._snapshots
        ]
        if heard != meant:
            self._corrections.append((heard, meant))

    def _commit_recase(self, item: Item) -> None:
        """"cap that" / "uppercase that" / "lowercase that", said as a
        whole segment, recases the last utterance in place; said
        mid-sentence it was dictation, and types as words."""
        start, end = item.span
        if not (start in self._segment_bounds and end in self._segment_bounds):
            self._type_as_words(item)
            return
        target = None
        for snapshot in reversed(self._snapshots):
            if snapshot.render_len < len(self._committed_render):
                target = snapshot
                break
        if target is None:
            logger.info("flow: nothing to recase")
            return
        said = self._committed_render[target.render_len:]
        recased = recase(said, item.mode)
        if risky_backspace(said) or len(recased) != len(said):
            # ("straße" -> "STRASSE" would leave the segment snapshots
            # pointing into the middle of a word.)
            logger.info("flow: not recasing: complex Unicode or a change of length")
            return
        self._committed_render = self._committed_render[:target.render_len] + recased

    def _last_utterance_length(self) -> int:
        """Characters "select that" must cover: the last segment typed in
        this recording, without its leading space. 0 (refused) when there
        is none, or when shift+left can't count it (complex Unicode)."""
        target = None
        for snapshot in reversed(self._snapshots):
            if snapshot.render_len < len(self._committed_render):
                target = snapshot
                break
        if target is None:
            return 0
        said = self._committed_render[target.render_len:]
        if risky_backspace(said):
            return 0
        self._select_state = target.render_state
        return len(said)

    def _apply_respell(self, spelled: str) -> None:
        """"spell that ...": swap the last committed word for the spelled
        one, keeping its trailing punctuation and its capital."""
        match = _LAST_WORD.search(self._committed_render)
        if match is None:
            last = self._last_action
            if last is not None and last.action.startswith("select:"):
                # Right after "select previous word": the spelled word is
                # typed over the selection.
                delta, self._render_state = render_items(
                    [Item(kind="word", text=spelled)], self._render_state, self._register
                )
                self._committed_render += delta
                return
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
