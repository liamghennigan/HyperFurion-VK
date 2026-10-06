// ═══ WINDOW — the focused app, above the board ════════════════════════════
// One window, four registers, picked the way the daemon's focus probe picks
// them: the editor renders prose (smart caps, spoken punctuation), the
// terminal renders digits and no caps, python and shell compile speech.
// Each register is its own app with its own document (buffer.js): a text
// field with a caret and a selection, which the Typist types into and
// navigation commands move around in. The window never changes height:
// one status line, replaced, never stacked.
import { $, fwin } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { settings } from "./settings.js";
import { REGISTERS, parse, render, initialState } from "./flow.js";
import { createBuffer } from "./buffer.js";

export const Window = (() => {
  const TITLES = {
    prose: ["notes.md", "editor"], terminal: ["~", "bash"],
    python: ["main.py", "editor"], shell: ["~", "zsh"],
  };
  const el = {
    title: $("fwin-title"), app: $("fwin-app"), doc: $("fwin-doc"), prompt: $("fwin-prompt"),
    instr: $("fwin-instr"), status: $("fwin-status"), tabs: $("regtabs"), panes: $("register-panes"),
    announce: $("fwin-announce"),
  };
  const docs = new Map();  // register -> its app's buffer
  let regName = "prose";
  let paintArgs = { moltenLen: 0, heat: "molten", moved: false };
  // the one status line: a transient note wins over the steady engine line
  const st = { engine: "", cls: "", latency: "", note: "", noteCls: "" };
  let noteT = 0;

  const bufferFor = (name) => {
    if (!docs.has(name)) docs.set(name, createBuffer({ terminal: !!REGISTERS[name].terminal }));
    return docs.get(name);
  };
  function setRegister(name, { silent = false } = {}) {
    if (!REGISTERS[name]) return;
    const changed = name !== regName;
    regName = name;
    state.register = name;
    fwin.dataset.register = name;
    const [file, app] = TITLES[name];
    if (el.title) el.title.textContent = file;
    if (el.app) el.app.textContent = app;
    if (el.tabs) for (const t of el.tabs.querySelectorAll("[role=tab]")) {
      const on = t.dataset.register === name;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
    }
    if (changed) { paintArgs = { moltenLen: 0, heat: "molten", moved: false }; paintDoc(); }
    if (changed && !silent) bus.emit("register:change", { name });
    if (changed && state.recording) bus.emit("focus:changed", { name });
  }
  const register = () => REGISTERS[regName];
  const focusedName = () => regName;
  const buffer = () => bufferFor(regName);

  // ── the document: frozen text, the molten tail, a caret, a selection ────
  function paintDoc() {
    if (!el.doc) return;
    const b = buffer();
    const text = b.text, caret = b.caret, sel = b.selection();
    const ms = Math.max(0, caret - paintArgs.moltenLen);
    const marks = new Set([0, text.length, caret, ms]);
    if (sel) { marks.add(sel[0]); marks.add(sel[1]); }
    const cuts = [...marks].sort((a, b) => a - b);
    const frag = document.createDocumentFragment();
    const promptSpan = document.createElement("span"); promptSpan.className = "prompt"; frag.appendChild(promptSpan);
    for (let i = 0; i < cuts.length - 1; i++) {
      const a = cuts[i], z = cuts[i + 1];
      if (a === caret) frag.appendChild(caretEl());
      if (z <= a) continue;
      let cls = "text";
      if (sel && a >= sel[0] && z <= sel[1]) cls = "sel";
      else if (a >= ms && z <= caret && paintArgs.moltenLen) cls = "molten" + (paintArgs.heat === "repair" ? " repair" : "");
      // a terminal shows a prompt on every line the human sent
      const parts = register().terminal ? text.slice(a, z).split("\n") : [text.slice(a, z)];
      parts.forEach((part, k) => {
        if (k > 0) {
          frag.appendChild(document.createTextNode("\n"));
          const pr = document.createElement("span"); pr.className = "prompt"; frag.appendChild(pr);
        }
        if (!part) return;
        const span = document.createElement("span");
        span.className = cls;
        span.textContent = part;
        frag.appendChild(span);
      });
    }
    if (caret === text.length) frag.appendChild(caretEl());
    const ph = document.createElement("span"); ph.className = "placeholder"; ph.textContent = "tap the mic and speak"; frag.appendChild(ph);
    el.doc.replaceChildren(frag);
    fwin.classList.toggle("has-text", text.length > 0);
    fwin.classList.toggle("typing", paintArgs.moltenLen > 0);
    paintStatus();
  }
  function caretEl() {
    const c = document.createElement("span");
    c.className = "caret" + (paintArgs.moved ? " moved" : "");
    return c;
  }
  // the Typist's view of its own typing: how much of the text left of the
  // caret is still molten, and whether the last change was a repair
  function renderLine(shown, frozenLen, { heat } = {}) {
    paintArgs = { moltenLen: Math.max(0, shown.length - frozenLen), heat, moved: false };
    paintDoc();
  }
  // the caret just moved under a navigation command
  function moved() { paintArgs = { ...paintArgs, moltenLen: 0, moved: true }; paintDoc(); }
  function instr(text) {
    if (!el.instr) return;
    el.instr.textContent = text ? "“" + settings.wakeWord + ", " + text + "” — listening for the instruction" : "";
    el.instr.hidden = !text;
  }
  function clearDoc() { buffer().clear(); paintArgs = { moltenLen: 0, heat: "molten", moved: false }; paintDoc(); bus.emit("doc:cleared", {}); }

  // ── status: one line ────────────────────────────────────────────────────
  function paintStatus() {
    if (!el.status) return;
    if (st.note) { el.status.textContent = st.note; el.status.className = "fwin-status " + st.noteCls; return; }
    el.status.textContent = [st.engine, st.latency].filter(Boolean).join(" · ");
    el.status.className = "fwin-status " + st.cls;
    if (register().terminal && buffer().text && !state.recording) {
      const y = document.createElement("span"); y.className = "yours"; y.textContent = "⏎ enter — yours";
      el.status.appendChild(y);
    }
  }
  function log(text, cls = "") {
    clearTimeout(noteT);
    st.note = text || ""; st.noteCls = cls;
    paintStatus();
    if (text) noteT = setTimeout(() => { st.note = ""; paintStatus(); }, 7000);
  }
  function setEngine(label, cls = "") { st.engine = label || ""; st.cls = cls; paintStatus(); }
  function setLatency(ms) {
    st.latency = ms == null ? "" : Math.round(ms) + " ms to first key";
    paintStatus();
  }
  function setLatencyNote(text) { st.latency = text || ""; paintStatus(); }

  // ── the registers section: the last phrase through every register ──────
  function panes() {
    if (!el.panes) return;
    const raw = state.lastRaw || "twenty three failed tests comma rerun the flaky ones";
    const tokens = raw.trim().split(/\s+/);
    for (const pane of el.panes.querySelectorAll("[data-register]")) {
      const reg = REGISTERS[pane.dataset.register];
      const { items } = parse(tokens, { flush: true, frozen: 0, register: reg, cfg: settings });
      const r = render(items.filter((it) => it.kind === "word" || it.kind === "punct" || it.kind === "break"), reg, initialState(reg));
      pane.querySelector(".pane-text").textContent = r.text;
    }
  }

  if (el.tabs) {
    const tabs = [...el.tabs.querySelectorAll("[role=tab]")];
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => setRegister(t.dataset.register));
      t.addEventListener("keydown", (e) => {
        const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!d) return;
        e.preventDefault();
        const n = tabs[(i + d + tabs.length) % tabs.length];
        n.focus(); setRegister(n.dataset.register);
      });
    });
  }
  // screen readers hear each dictation once, when it lands — not every
  // keystroke of the molten repair
  bus.on("type:text", ({ text }) => { if (el.announce) el.announce.textContent = "typed: " + text; });
  setRegister("prose", { silent: true });
  paintDoc();
  panes();

  return { setRegister, register, focusedName, buffer, renderLine, moved, paintDoc, instr, clearDoc,
           log, setEngine, setLatency, setLatencyNote, panes };
})();
