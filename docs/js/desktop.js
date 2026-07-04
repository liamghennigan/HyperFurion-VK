// ═══ DESKTOP — one window up front, the real terminal behind a toggle ════
// The product's pitch is "it types into whatever app has focus", so the
// demo is one focused window: an editor your dictation lands in. Behind a
// single toggle sits the demo's real terminal — the full CLI (status,
// real, ask, subscribe) — and dictation follows whichever window is open.
//
// Only the terminal executes anything; the editor is an honest stand-in
// that shows where the keystrokes land.
import { $, desktop } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { Scope } from "./scope.js";
import { Terminal } from "./terminal.js";
import { Dictation } from "./dictation.js";

export const Desktop = (() => {
  const wins = {
    editor: $("win-editor"),
    terminal: $("win-term"),
  };
  const scopeWrap = desktop.querySelector(".scope-wrap");
  const demoLive = $("demo-live");
  const toggle = $("dtoggle");
  const chipsEl = $("saychips");
  const SAY = {
    editor: "fixed the race condition in the audio thread",
    terminal: "git status",
  };
  let focusedName = "editor";
  let recording = false, ready = false;

  // ── the editor: a buffer with line numbers ───────────────────────────────
  function makeEditor() {
    const body = $("ebody");
    const lines = ["# release notes — v1.1", "drafted by keyboard, finished by voice", ""];
    let committed = "", interim = "";
    function render() {
      body.replaceChildren();
      const all = [...lines];
      for (let i = 0; i < all.length; i++) {
        const d = document.createElement("div");
        d.className = "eline" + (i === all.length - 1 ? " ecur" : "");
        const span = document.createElement("span");
        span.textContent = all[i];
        d.appendChild(span);
        if (i === all.length - 1) {
          if (committed || interim) {
            span.textContent = committed;
            const im = document.createElement("span");
            im.className = "interim";
            im.textContent = interim;
            d.appendChild(im);
          }
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
    return {
      setLine(c, i) { committed = c; interim = i; render(); },
      commit() {
        const text = (committed + interim).trim();
        committed = interim = "";
        if (text) { lines[lines.length - 1] = text; lines.push(""); }
        render();
        return text;
      },
      recMode() { render(); },
    };
  }

  const targets = {
    editor: makeEditor(),
    terminal: { setLine: Terminal.setLine, commit: Terminal.commitCurrent, recMode: Terminal.recMode },
  };

  // ── which window is open ─────────────────────────────────────────────────
  function focus(name) {
    if (!wins[name] || name === focusedName && wins[name].classList.contains("focused")) return;
    const prev = focusedName;
    focusedName = name;
    state.focusedApp = name;
    for (const [n, el] of Object.entries(wins)) el.classList.toggle("focused", n === name);
    // dock the scope strip and the recording controls into the open window
    wins[name].querySelector(".dwin-scope").appendChild(scopeWrap);
    wins[name].querySelector(".dwin-dockbar").appendChild(demoLive);
    requestAnimationFrame(() => Scope.resize());
    if (recording && targets[prev] !== targets[name]) {
      targets[prev].recMode(false);
      targets[name].recMode(true);
    }
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
    label.textContent = "no mic handy? try saying";
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "saychip";
    chip.textContent = "“" + SAY[focusedName] + "”";
    chip.title = "types itself into the window, the way dictation would";
    chip.addEventListener("click", () => Dictation.simulate(SAY[focusedName]));
    chipsEl.append(label, chip);
  }

  toggle.hidden = false;
  toggle.addEventListener("click", () => focus(focusedName === "editor" ? "terminal" : "editor"));

  focus("editor");
  ready = true;

  return {
    focus,
    focused: () => targets[focusedName],
    focusedName: () => focusedName,
    // the FocusTarget lane dictation writes through
    setLine: (c, i) => targets[focusedName].setLine(c, i),
    commit: () => targets[focusedName].commit(),
    recMode(on) {
      recording = on;
      if (on) targets[focusedName].recMode(true);
      else for (const t of Object.values(targets)) t.recMode(false);
    },
  };
})();
