// ═══ WINDOW — the focused app, floating above the board ═══════════════════
// One window, four registers, picked the way the daemon's focus probe picks
// them: the editor renders prose (smart caps, spoken punctuation), the
// terminal renders digits and no caps, python and shell compile speech.
// Committed lines are announced once; the molten tail is decoration.
import { $, fwin, reduced } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { settings } from "./settings.js";
import { REGISTERS, parse, render, initialState } from "./flow.js";

export const Window = (() => {
  const TITLES = {
    prose: ["notes.md", "editor"], terminal: ["~", "bash"],
    python: ["main.py", "editor"], shell: ["~", "zsh"],
  };
  const el = {
    title: $("fwin-title"), app: $("fwin-app"), frozen: $("fwin-frozen"), molten: $("fwin-molten"),
    instr: $("fwin-instr"), lines: $("fwin-lines"), log: $("fwin-log"), engine: $("fwin-engine"),
    latency: $("fwin-latency"), probe: $("fwin-probe"), tabs: $("regtabs"), split: $("fwin-split"),
    chips: $("chips"),
  };
  // the try-saying chips: one register lesson each, compiled through the
  // same molten engine a live session uses
  const SAY = {
    prose: [
      { label: "“fixed the race condition … period”",
        script: { text: "fixed the race condition in the audio thread period", revise: { at: 3, wrong: "addition" } },
        title: "watch the third word arrive wrong, repair itself, then freeze" },
      { label: "“… it works now VK, make that formal”",
        script: { text: "we fixed a bunch of bugs and it works now VK, make that formal" },
        title: "the wake word rewrites what you just dictated, in place" },
    ],
    terminal: [
      { label: "“twenty three failed tests comma rerun …”",
        script: { text: "twenty three failed tests comma rerun the flaky ones" },
        title: "terminal register: no auto-caps, spoken numbers become digits" },
    ],
    python: [
      { label: "“for i in range ten colon”", script: { text: "for i in range ten colon" },
        title: "python register: speech compiles into code" },
    ],
    shell: [
      { label: "“grep dash i error star dot log”", script: { text: "grep dash i error star dot log" },
        title: "shell register: dashes become flags, star globs" },
    ],
  };
  let regName = "prose", probeT = 0, logT = 0;

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
    chips();
    if (changed && !silent) bus.emit("register:change", { name });
    if (changed && state.recording) bus.emit("focus:changed", { name });
  }
  const register = () => REGISTERS[regName];
  const focusedName = () => regName;

  // ── the live line ───────────────────────────────────────────────────────
  function renderLine(shown, frozenLen, { heat } = {}) {
    if (!el.frozen) return;
    el.frozen.textContent = shown.slice(0, frozenLen);
    el.molten.textContent = shown.slice(frozenLen);
    el.molten.classList.toggle("repair", heat === "repair");
    fwin.classList.toggle("typing", shown.length > 0);
  }
  function instr(text) {
    if (!el.instr) return;
    el.instr.textContent = text ? "“" + (settings.wakeWord) + ", " + text + "” — listening for the instruction" : "";
    el.instr.hidden = !text;
  }
  function commitLine(text) {
    if (!text || !el.lines) return;
    const p = document.createElement("p");
    p.className = "line";
    p.dataset.register = regName;
    p.textContent = text;
    el.lines.appendChild(p);
    while (el.lines.children.length > 5) el.lines.firstChild.remove();
    if (register().compiler || regName === "terminal") log("drafted at the prompt — Enter is yours", "consent");
    splitView();
  }
  function retract() { if (el.lines && el.lines.lastElementChild) el.lines.lastElementChild.remove(); }
  function replaceLast(text) { if (el.lines && el.lines.lastElementChild) el.lines.lastElementChild.textContent = text; }
  function clearLines() { if (el.lines) el.lines.replaceChildren(); }

  // ── status ──────────────────────────────────────────────────────────────
  function log(text, cls = "") {
    if (!el.log) return;
    el.log.textContent = text;
    el.log.className = "fwin-log " + cls;
    clearTimeout(logT);
    if (text) logT = setTimeout(() => { if (el.log.textContent === text) { el.log.textContent = ""; } }, 9000);
  }
  function setEngine(label, cls = "") {
    if (!el.engine) return;
    el.engine.textContent = label;
    el.engine.className = "engine " + cls;
  }
  function setLatency(ms, median) {
    if (!el.latency) return;
    if (ms == null) { el.latency.textContent = ""; return; }
    el.latency.textContent = "speech → first keystroke " + Math.round(ms) + " ms · median " + Math.round(median) + " ms (this page, your browser)";
  }
  function setLatencyNote(text) { if (el.latency) el.latency.textContent = text; }
  function probe() {
    if (!el.probe) return;
    el.probe.textContent = "probe → " + regName;
    el.probe.hidden = false;
    clearTimeout(probeT);
    probeT = setTimeout(() => { el.probe.hidden = true; }, 3000);
  }

  // ── scene 2: the same words through every register ─────────────────────
  function splitView() {
    if (!el.split) return;
    const raw = state.lastRaw || "twenty three failed tests comma rerun the flaky ones";
    const tokens = raw.trim().split(/\s+/);
    for (const pane of el.split.querySelectorAll("[data-register]")) {
      const reg = REGISTERS[pane.dataset.register];
      const { items } = parse(tokens, { flush: true, frozen: 0, register: reg, cfg: settings });
      const r = render(items.filter((it) => it.kind !== "instruction"), reg, initialState(reg));
      pane.querySelector(".pane-text").textContent = r.text;
      pane.classList.toggle("active", pane.dataset.register === regName);
    }
  }

  // ── chips: scripted lines for the no-mic case ──────────────────────────
  function chips() {
    if (!el.chips) return;
    el.chips.replaceChildren();
    for (const s of SAY[regName] || []) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = s.label;
      b.title = s.title;
      b.addEventListener("click", () => bus.emit("simulate", { script: s.script }));
      el.chips.appendChild(b);
    }
  }

  // tabs
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
  // the focus probe follows the story: the consent scene is a terminal,
  // the registers scene shows every register, the rest is prose
  bus.on("story:scene", ({ index }) => {
    if (index === 2) splitView();
    if (state.recording || document.body.classList.contains("autopilot")) return;
    if (index === 3) setRegister("terminal", { silent: true });
    else if (index === 0 || index === 1 || index === 5) setRegister("prose", { silent: true });
  });
  setRegister("prose", { silent: true });
  splitView();

  return { setRegister, register, focusedName, renderLine, instr, commitLine, retract, replaceLast,
           clearLines, log, setEngine, setLatency, setLatencyNote, probe, splitView };
})();
