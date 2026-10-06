// ═══ KEYBOARD — the 3D board: 82 keycaps that press themselves ═══════════
// DOM keycaps under one CSS 3D transform (crisp legends; a press is a
// transform, nothing repaints). The board is still; only presses move.
// The Typist hands this module a queue of presses; one Ticker subscriber
// drains it at a human cadence. A navigation command appends a chord —
// modifiers held, one key tapped — the way the daemon's press_combo does.
// The Enter key is guarded in code: the page can light it, never press it
// — the daemon's suppress_enter, on screen.
import { board, reduced } from "./env.js";
import { bus } from "./bus.js";
import { Ticker } from "./ticker.js";
import { LAYOUT_75, chordToPresses } from "./layout.js";

export const Keyboard = (() => {
  const keys = new Map();          // code -> element
  let queue = [], nextAt = 0, downUntil = new Map(), lastDrainEmpty = true;
  const CH_MS = 46, BS_MS = 22, HOLD_LEAD = 30, KEY_MS = 150, DWELL = reduced ? 0 : 42;

  function mount() {
    if (!board) return;
    board.replaceChildren();
    const plate = document.createElement("div");
    plate.className = "plate";
    board.appendChild(plate);
    for (const row of LAYOUT_75) {
      const r = document.createElement("div");
      r.className = "row";
      for (const k of row) {
        if (k.gap) {
          const g = document.createElement("div");
          g.className = "gap";
          g.style.setProperty("--w", k.gap);
          r.appendChild(g);
          continue;
        }
        const el = document.createElement("div");
        el.className = "key" + (k.r ? " " + k.r : "") + (k.c === "Enter" ? " locked" : "") + (k.c === "Space" ? " space" : "");
        el.dataset.code = k.c;
        if (k.opt) el.dataset.opt = "1";
        el.style.setProperty("--w", k.w || 1);
        const leg = document.createElement("span");
        leg.className = "legend" + (k.l.length === 1 && !/[a-z0-9]/i.test(k.l) ? " sym" : "");
        leg.textContent = k.l;
        el.appendChild(leg);
        if (k.sub) {
          const sub = document.createElement("span");
          sub.className = "sub";
          sub.textContent = k.sub;
          el.appendChild(sub);
        }
        r.appendChild(el);
        keys.set(k.c, el);
      }
      board.appendChild(r);
    }
  }

  // the one guarded press: Enter is lit, never pushed
  function ring() {
    const el = keys.get("Enter");
    if (!el) return;
    el.classList.remove("ring"); void el.offsetWidth; el.classList.add("ring");
  }
  function down(code, heat, dur) {
    const el = keys.get(code);
    if (!el) return;
    if (code === "Enter") { ring(); bus.emit("enter:refused", {}); return; }
    el.classList.add("down");
    el.classList.toggle("repair", heat === "repair");
    el.classList.toggle("navkey", heat === "nav");
    downUntil.set(code, performance.now() + Math.max(DWELL, dur || 0));
    bus.emit("key:down", { code, heat });
  }
  function up(code) {
    const el = keys.get(code);
    if (el) el.classList.remove("down");
    downUntil.delete(code);
  }

  // ── the queue ──────────────────────────────────────────────────────────
  // The Typist replaces its typing; a chord is appended, so the two never
  // interleave — the Typist waits for the queue to drain before a chord.
  function replaceQueue(presses) {
    const chords = queue.filter((p) => p.chord);
    queue = chords.concat(presses);
    if (!nextAt) nextAt = performance.now();
    Ticker.wake();
  }
  function appendQueue(presses) {
    queue = queue.concat(presses);
    if (!nextAt) nextAt = performance.now();
    Ticker.wake();
  }
  function clearQueue() { queue = queue.filter((p) => p.chord); }
  function tick() {
    const now = performance.now();
    for (const [code, t] of downUntil) if (now >= t && !held.has(code)) up(code);
    if (!queue.length) {
      if (!lastDrainEmpty) { lastDrainEmpty = true; bus.emit("queue:empty", {}); }
      nextAt = 0;
      return;
    }
    lastDrainEmpty = false;
    if (now < nextAt) return;
    const p = queue.shift();
    // adaptive catch-up: a long backlog types faster, never slower than 24 ms
    const base = p.kind === "bs" ? BS_MS : p.kind === "hold" || p.kind === "release" ? HOLD_LEAD : p.kind === "key" ? KEY_MS : CH_MS;
    const gap = queue.length > 14 && p.kind !== "key" ? Math.max(24, base - queue.length) : base;
    nextAt = now + (reduced ? 0 : gap);
    perform(p);
  }
  const held = new Set();
  function perform(p) {
    if (p.kind === "hold") { held.add(p.code); down(p.code, p.heat || "molten", 1e9); return; }
    if (p.kind === "release") { held.delete(p.code); up(p.code); return; }
    if (p.code) down(p.code, p.heat, p.kind === "key" ? 120 : 60);
    if (p.ghost === "newline" || p.ghost === "enter") ring();
    if (p.onDown) p.onDown(p);
  }

  // external presses: the visitor's own fingers, the autopilot's ghost
  function press(code, { heat = "user", hold = false } = {}) {
    if (!keys.has(code)) return false;
    if (hold) { held.add(code); down(code, heat, 1e9); return true; }
    down(code, heat, 90);
    return true;
  }
  function release(code) { held.delete(code); up(code); }
  function releaseAll() { for (const c of [...held]) release(c); for (const c of [...downUntil.keys()]) up(c); }

  // a navigation chord, e.g. ["shift", "ctrl", "left"]: resolves once the
  // key itself has gone down, so the window can move its caret in step
  function chord(names) {
    return new Promise((resolve) => {
      const presses = chordToPresses(names, { onDown: () => resolve(true) });
      for (const p of presses) { p.chord = true; if (p.kind === "hold") p.heat = "nav"; }
      appendQueue(presses);
    });
  }

  function burst(code, n) {
    // a fast run of one key (a long retract) — shown as repeat, not n presses
    const presses = [];
    for (let i = 0; i < Math.min(n, 24); i++) presses.push({ code, kind: "bs", heat: "repair" });
    replaceQueue(presses);
  }
  const rectOf = (code) => { const el = keys.get(code); return el ? el.getBoundingClientRect() : null; };
  const boardRect = () => board.getBoundingClientRect();

  mount();
  Ticker.add({ fn: tick });
  return { mount, replaceQueue, appendQueue, clearQueue, press, release, releaseAll, chord, burst, rectOf, boardRect,
           has: (c) => keys.has(c), queued: () => queue.length };
})();
