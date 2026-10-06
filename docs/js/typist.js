// ═══ TYPIST — flow snapshots become key presses ════════════════════════════
// Holds the text it has typed into the focused window since it last took
// its bearings, and diffs every molten snapshot against it: backspace to
// the divergence point, retype the tail — the daemon's InjectionWorker,
// made visible on the board. Each press lands in the window's buffer at
// the caret, exactly as a keystroke would. The queue is replaced, never
// appended, so the board can never race ahead of the truth. After a
// navigation command moves the caret, release() forgets the typed text:
// what is behind the caret is no longer ours to repair.
import { bus } from "./bus.js";
import { state } from "./state.js";
import { Keyboard } from "./keyboard.js";
import { Window } from "./window.js";
import { toPresses } from "./layout.js";

export const Typist = (() => {
  let shown = "", frozenLen = 0, target = { frozen: "", molten: "" }, heat = "molten";
  let lastSet = 0, pendingSnap = null, snapT = 0;
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
    const buf = Window.buffer();
    if (p.kind === "bs") { shown = shown.slice(0, -1); buf.backspace(); }
    else { shown += p.ch; buf.insert(p.ch); }
    state.typedChars++;
    bus.emit("type:char", { ch: p.ch, kind: p.kind });
    if (state.mark) {
      // the first keystroke painted after a speech result: that is the latency
      const m = state.mark; state.mark = 0;
      requestAnimationFrame((ts) => {
        const ms = Math.max(0, ts - m);
        state.latency.push(ms);
        if (state.latency.length > 8) state.latency.shift();
        Window.setLatency(ms);
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

  // resolves when the queue has drained (or the deadline passes)
  function settled(maxMs = 4000) {
    if (!Keyboard.queued()) return Promise.resolve();
    return new Promise((res) => {
      const t = setTimeout(() => { settlers = settlers.filter((s) => s !== fin); res(); }, maxMs);
      const fin = () => { clearTimeout(t); res(); };
      settlers.push(fin);
    });
  }
  bus.on("queue:empty", () => {
    const s = settlers; settlers = [];
    for (const f of s) f();
  });

  // the caret moved (a navigation command, a new app): the text typed so
  // far stays on screen, but it is not ours any more
  function release() {
    if (snapT) { clearTimeout(snapT); snapT = 0; }
    pendingSnap = null;
    shown = ""; frozenLen = 0; target = { frozen: "", molten: "" };
    Window.instr("");
  }
  // a new dictation begins: nothing typed yet, nothing queued
  function reset() {
    Keyboard.clearQueue();
    release();
    paint();
  }
  return { setTarget, release, reset, settled, shown: () => shown, flushPending };
})();
