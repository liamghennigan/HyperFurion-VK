"""Preedit is molten: the input-method mapping layer.

Input-method frameworks have carried spoken input's missing primitive for
decades: the preedit string — composition text shown at the caret,
styled, pending, replaceable — and commit, the moment text becomes real.
That is exactly molten/freeze. This module translates flow-engine state
into IM operations; a host (an ibus engine today, a zwp_input_method_v2
client on compositors that expose it) applies them. The mapping and the
staged plan live in SPOKEN-INPUT-PROTOCOL.md.

v0 ships the mapper, fully tested, with host integration deliberately
unwired: registering an input method touches the entire desktop's typing
stack, so it lands behind an explicit opt-in — never as a side effect of
a daemon upgrade.
"""

from voice_keyboard.flow.worker import common_prefix_len


class PreeditMapper:
    """Translate (committed, molten) flow state into IM operations.

    Feed it the engine's committed render and molten tail after every
    update; it returns the minimal operation list:

      ("commit", text)   — append text as committed input
      ("delete", n)      — delete n committed chars (a "scratch that"
                           rewind; hosts use delete_surrounding_text)
      ("preedit", text)  — replace the pending composition string

    Stateful and minimal: unchanged state produces no operations, molten
    repairs touch only the preedit, and committed text only ever changes
    by append or explicit rewind — the same contract the keystroke
    injector's diff-converge worker honors.
    """

    def __init__(self):
        self._committed = ""
        self._preedit = ""

    def update(self, committed: str, molten: str) -> list[tuple[str, object]]:
        ops: list[tuple[str, object]] = []
        if committed != self._committed:
            if committed.startswith(self._committed):
                delta = committed[len(self._committed):]
                if delta:
                    ops.append(("commit", delta))
            else:
                keep = common_prefix_len(self._committed, committed)
                ops.append(("delete", len(self._committed) - keep))
                remainder = committed[keep:]
                if remainder:
                    ops.append(("commit", remainder))
            self._committed = committed
        if molten != self._preedit:
            ops.append(("preedit", molten))
            self._preedit = molten
        return ops

    def finalize(self) -> list[tuple[str, object]]:
        """Session end: whatever is still molten evaporates — pending text
        never commits by accident. The engine commits what deserves to
        survive via a final update() before this."""
        ops: list[tuple[str, object]] = []
        if self._preedit:
            ops.append(("preedit", ""))
            self._preedit = ""
        return ops
