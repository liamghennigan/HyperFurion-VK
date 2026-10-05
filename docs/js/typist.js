// ═══ TYPIST — flow snapshots become key presses ════════════════════════════
// Holds the text the window actually shows and diffs every molten snapshot
// against it: backspace to the divergence point, retype the tail — the
// daemon's repair, made visible on the board. The queue is replaced, never
// appended, so the board can never race ahead of the truth.
import { bus } from "./bus.js";
import { state } from "./state.js";
import { Keyboard } from "./keyboard.js";
import { Window } from "./window.js";
import { toPresses } from "./layout.js";

export const Typist = (() => {
  let shown = "", frozenLen = 0, target = { frozen: "", molten: "" }, heat = "molten";
  let pendingCommit = null, lastSet = 0, pendingSnap = null, snapT = 0;
  let settlers = [];

  const prefix = (a, b) => {
    const n = Math.min(a.length, b.length);
    let i = 0;
    while (i < n && a[i] === b[i]) i++;
    return i;
  };

  function setTarget(snap) {
    const now = performance.now();
    // snapshots within 80 ms collapse to the latest — only the newest is diffed
    if (now - lastSet < 80) {
      pendingSnap = snap;
      if (!snapT) snapT = setTimeout(() => { snapT = 0; flushPending(); }, 80 - (now - lastSet));
      return;
    }
    applySnap(snap);
  }
  function flushPending() {
    if (snapT) { clearTimeout(snapT); snapT = 0; }
    const s = pendingSnap; pendingSnap = null;
    if (s) applySnap(s);
  }
  function applySnap(snap) {
    lastSet = performance.now();
    target = { frozen: snap.frozen || "", molten: snap.molten || "" };
    heat = snap.repair ? "repair" : "molten";
    const full = target.frozen + target.molten;
    const p = prefix(shown, full);
    const ops = [];
    for (let i = shown.length; i > p; i--) ops.push({ kind: "bs" });
    for (const ch of full.slice(p)) ops.push({ kind: "ch", ch });
    const presses = toPresses(ops, { heat });
    for (const pr of presses) if (pr.kind === "ch" || pr.kind === "bs") pr.onDown = apply;
    Keyboard.replaceQueue(presses);
    Window.instr(snap.instr || "");
    checkFreeze();
    paint();
  }
  function apply(p) {
    if (p.kind === "bs") shown = shown.slice(0, -1);
    else shown += p.ch;
    state.typedChars++;
    bus.emit("type:char", { ch: p.ch, kind: p.kind });
    if (state.mark) {
      // the first keystroke painted after a speech result: that is the latency
      const m = state.mark; state.mark = 0;
      requestAnimationFrame((ts) => {
        const ms = Math.max(0, ts - m);
        state.latency.push(ms);
        if (state.latency.length > 8) state.latency.shift();
        const sorted = [...state.latency].sort((a, b) => a - b);
        Window.setLatency(ms, sorted[sorted.length >> 1]);
      });
    }
    checkFreeze();
    paint();
  }
  function checkFreeze() {
    const nf = prefix(shown, target.frozen);
    if (nf > frozenLen) bus.emit("flow:freeze", { text: shown.slice(frozenLen, nf) });
    frozenLen = nf;
  }
  function paint() { Window.renderLine(shown, frozenLen, { heat }); }

  // ── commit: the line lands once the board has finished typing it ───────
  function commit(text) {
    flushPending();              // a deferred snapshot must never retype a committed line
    pendingCommit = text;
    tryFinish();
    return text;
  }
  function tryFinish() {
    if (pendingCommit === null || Keyboard.queued()) return;
    const text = pendingCommit; pendingCommit = null;
    Window.commitLine(shown || text);
    shown = ""; frozenLen = 0; target = { frozen: "", molten: "" };
    Window.instr("");
    paint();
    bus.emit("type:commit", { text });
  }
  function settled(maxMs = 4000) {
    // resolves when the queue has drained (or the deadline passes)
    if (!Keyboard.queued()) return Promise.resolve();
    return new Promise((res) => {
      const t = setTimeout(() => { settlers = settlers.filter((s) => s !== fin); res(); }, maxMs);
      const fin = () => { clearTimeout(t); res(); };
      settlers.push(fin);
    });
  }
  bus.on("queue:empty", () => {
    tryFinish();
    const s = settlers; settlers = [];
    for (const f of s) f();
  });

  function retract() {
    Window.retract();
    Keyboard.burst("Backspace", 24);
  }
  function reset() {
    if (snapT) { clearTimeout(snapT); snapT = 0; }
    pendingSnap = null;
    Keyboard.clearQueue();
    shown = ""; frozenLen = 0; target = { frozen: "", molten: "" }; pendingCommit = null;
    Window.instr("");
    paint();
  }
  return { setTarget, commit, retract, reset, settled, shown: () => shown };
})();
