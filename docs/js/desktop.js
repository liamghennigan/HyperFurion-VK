// ═══ DESKTOP — two windows, two registers, one focus ══════════════════════
// The product's pitch is "it types into whatever app has focus, the way
// that app wants it typed." So the demo is two windows with different
// registers, the way the daemon's focus probe assigns them: the editor
// renders prose (smart caps, spoken punctuation), the terminal renders the
// terminal register (no auto-caps, numbers as digits). Dictate the same
// words into both and the register is the difference you see.
//
// Only the terminal executes anything; the editor is an honest stand-in
// that shows where the keystrokes land. If focus moves mid-dictation,
// typing freezes and the transcript lands on the clipboard — the daemon
// never types into the wrong window, and neither does this page.
import { $, desktop, reduced } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { Scope } from "./scope.js";
import { Config } from "./config.js";
import { Terminal } from "./terminal.js";
import { Dictation } from "./dictation.js";
import { REGISTERS } from "./flow.js";

export const Desktop = (() => {
  const wins = {
    editor: $("win-editor"),
    terminal: $("win-term"),
  };
  const scopeWrap = desktop.querySelector(".scope-wrap");
  const demoLive = $("demo-live");
  const toggle = $("dtoggle");
  const chipsEl = $("saychips");
  // the try-saying chips: compiled molten scripts, one register lesson each
  const SAY = {
    editor: [
      { label: "fixed the race condition … period",
        script: { text: "fixed the race condition in the audio thread period",
                  revise: { at: 3, wrong: "addition" } },
        title: "watch the third word arrive wrong, repair itself, then freeze" },
      { label: "… it works now VK, make that formal",
        script: { text: "we fixed a bunch of bugs and it works now VK, make that formal" },
        title: "the wake word rewrites what you just dictated, in place" },
    ],
    terminal: [
      { label: "twenty three failed tests comma rerun …",
        script: { text: "twenty three failed tests comma rerun the flaky ones" },
        title: "terminal register: no auto-caps, spoken numbers become digits" },
    ],
  };
  let focusedName = "editor";
  let recording = false, ready = false;
  let probeT = 0;

  // ── the editor: a buffer with line numbers and a molten tail ────────────
  function makeEditor() {
    const body = $("ebody");
    const lines = [
      "# release notes — v1.2",
      "words land molten, freeze when sure — dictated, not typed",
      "",
    ];
    const utterances = [];   // committed line-counts, for "scratch that"
    let frozen = "", molten = "", instr = "", flash = 0;
    function render() {
      body.replaceChildren();
      for (let i = 0; i < lines.length; i++) {
        const d = document.createElement("div");
        d.className = "eline" + (i === lines.length - 1 ? " ecur" : "");
        const span = document.createElement("span");
        span.textContent = lines[i];
        d.appendChild(span);
        if (i === lines.length - 1) {
          if (frozen || molten || instr) {
            span.textContent = frozen;
            if (molten) {
              const m = document.createElement("span");
              m.className = "molten" + (flash ? " repair" : "");
              m.textContent = molten;
              d.appendChild(m);
            }
            if (instr) {
              const s = document.createElement("span");
              s.className = "instr";
              s.textContent = " ✦ " + instr;
              d.appendChild(s);
            }
          } else if (flash) span.className = "repair";
          const caret = document.createElement("span");
          caret.className = "caret";
          caret.hidden = !(recording && focusedName === "editor");
          d.appendChild(caret);
        }
        body.appendChild(d);
      }
      body.scrollTop = body.scrollHeight;
    }
    render();
    function setFlash() {
      if (reduced) return;
      flash = 1;
      setTimeout(() => { flash = 0; render(); }, 480);
    }
    return {
      setLine(f, m, fx = {}) {
        frozen = f; molten = m; instr = fx.instr || "";
        if (fx.repair) setFlash();
        render();
      },
      commit() {
        const text = (frozen + molten).trim();
        frozen = molten = instr = "";
        if (text) {
          const seg = text.split("\n");
          lines.splice(lines.length - 1, 1, ...seg, "");
          utterances.push(seg.length);
        }
        render();
        return text;
      },
      retract() {
        const n = utterances.pop();
        if (n) { lines.splice(lines.length - 1 - n, n); setFlash(); }
        render();
      },
      replaceLast(text) {
        const n = utterances.pop();
        if (!n) return;
        const seg = text.split("\n");
        lines.splice(lines.length - 1 - n, n, ...seg);
        utterances.push(seg.length);
        setFlash();
        render();
      },
      recMode() { render(); },
    };
  }

  const targets = {
    editor: makeEditor(),
    terminal: {
      setLine: Terminal.setLine, commit: Terminal.commitCurrent,
      retract: Terminal.retract, replaceLast: Terminal.replaceLast,
      recMode: Terminal.recMode,
    },
  };

  // the register the daemon's focus probe would assign each window
  function registerFor(name) {
    if (name === "terminal") return REGISTERS.terminal;
    return REGISTERS[Config.cfg.regDefault] || REGISTERS.prose;
  }

  // ── which window is open ─────────────────────────────────────────────────
  function focus(name) {
    if (!wins[name] || name === focusedName && wins[name].classList.contains("focused")) return;
    focusedName = name;
    state.focusedApp = name;
    for (const [n, el] of Object.entries(wins)) el.classList.toggle("focused", n === name);
    // dock the scope strip and the recording controls into the open window
    wins[name].querySelector(".dwin-scope").appendChild(scopeWrap);
    wins[name].querySelector(".dwin-dockbar").appendChild(demoLive);
    requestAnimationFrame(() => Scope.resize());
    // mid-dictation focus change: the daemon freezes typing rather than
    // follow focus into the wrong window — so does the page
    if (recording) Dictation.guard();
    toggle.textContent = name === "editor"
      ? "the demo has a real terminal too →"
      : "← back to the editor";
    renderChips();
    // the boot-time focus happens while sibling modules are still
    // initializing — only broadcast once everyone is wired up
    if (ready) bus.emit("desk:focus", { name });
  }
  function renderChips() {
    chipsEl.hidden = false;
    chipsEl.replaceChildren();
    const label = document.createElement("span");
    label.className = "sigcap";
    label.textContent = "no mic handy? say";
    chipsEl.append(label);
    for (const say of SAY[focusedName]) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "saychip";
      chip.textContent = "“" + say.label + "”";
      chip.title = say.title;
      chip.addEventListener("click", () => Dictation.simulate(say.script));
      chipsEl.append(chip);
    }
  }
  // the probe chip: the focus probe, made visible at recording start
  function probe() {
    const bar = wins[focusedName].querySelector(".probechip");
    if (!bar) return;
    bar.textContent = "probe → " + registerFor(focusedName).name;
    bar.hidden = false;
    clearTimeout(probeT);
    probeT = setTimeout(() => { bar.hidden = true; }, 3200);
  }

  toggle.hidden = false;
  toggle.addEventListener("click", () => focus(focusedName === "editor" ? "terminal" : "editor"));

  focus("editor");
  ready = true;

  return {
    focus,
    focused: () => targets[focusedName],
    focusedName: () => focusedName,
    register: () => registerFor(focusedName),
    probe,
    // the FocusTarget lane dictation writes through
    setLine: (f, m, fx) => targets[focusedName].setLine(f, m, fx || {}),
    commit: () => targets[focusedName].commit(),
    retract: () => targets[focusedName].retract(),
    replaceLast: (app, text) => (targets[app] || targets[focusedName]).replaceLast(text),
    recMode(on) {
      recording = on;
      if (on) targets[focusedName].recMode(true);
      else for (const t of Object.values(targets)) t.recMode(false);
    },
  };
})();
