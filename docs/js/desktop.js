// ═══ DESKTOP — three windows, one focus ═══════════════════════════════════
// The product's whole pitch is "it types into whatever app has focus", so
// the demo is a desktop: an editor, a chat, and the real terminal. Dictation
// routes to the focused window — click, Alt+Tab, or the tab strip to switch,
// exactly like the desktop it stands in for. The scope strip and recording
// controls dock into whichever window holds focus.
//
// Only the terminal executes anything; the editor and chat are honest
// stand-ins that show where the keystrokes land.
import { $, desktop, reduced } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { Scope } from "./scope.js";
import { Terminal } from "./terminal.js";
import { Dictation } from "./dictation.js";

export const Desktop = (() => {
  const wins = {
    editor: $("win-editor"),
    chat: $("win-chat"),
    terminal: $("win-term"),
  };
  const scopeWrap = desktop.querySelector(".scope-wrap");
  const demoLive = $("demo-live");
  const dswitch = $("dswitch");
  const chipsEl = $("saychips");
  const ORDER = ["editor", "chat", "terminal"];
  const SAY = {
    editor: "fixed the race condition in the audio thread",
    chat: "running five minutes late, start without me",
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

  // ── the chat: a thread and a composer ────────────────────────────────────
  function makeChat() {
    const body = $("cbody");
    const thread = [
      { who: "sam", text: "standup moved to 9:15 — that ok?" },
      { who: "sam", text: "also, did the audio-thread fix land?" },
    ];
    let committed = "", interim = "";
    function render() {
      body.replaceChildren();
      for (const m of thread) {
        const d = document.createElement("div");
        d.className = "cmsg " + (m.who === "you" ? "me" : "them");
        const w = document.createElement("b");
        w.textContent = m.who;
        const t = document.createElement("span");
        t.textContent = m.text;
        d.append(w, t);
        body.appendChild(d);
      }
      const comp = document.createElement("div");
      comp.className = "ccomp";
      const dim = document.createElement("span");
      dim.className = "dim";
      dim.textContent = "› ";
      const c = document.createElement("span");
      c.textContent = committed;
      const im = document.createElement("span");
      im.className = "interim";
      im.textContent = interim;
      const caret = document.createElement("span");
      caret.className = "caret";
      caret.hidden = !(recording && focusedName === "chat");
      comp.append(dim, c, im, caret);
      body.appendChild(comp);
      body.scrollTop = body.scrollHeight;
    }
    render();
    return {
      setLine(c, i) { committed = c; interim = i; render(); },
      commit() {
        const text = (committed + interim).trim();
        committed = interim = "";
        if (text) thread.push({ who: "you", text });
        render();
        return text;
      },
      recMode() { render(); },
    };
  }

  const targets = {
    editor: makeEditor(),
    chat: makeChat(),
    terminal: { setLine: Terminal.setLine, commit: Terminal.commitCurrent, recMode: Terminal.recMode },
  };

  // ── focus management ─────────────────────────────────────────────────────
  function focus(name) {
    if (!wins[name] || name === focusedName && wins[name].classList.contains("focused")) return;
    const prev = focusedName;
    focusedName = name;
    state.focusedApp = name;
    for (const [n, el] of Object.entries(wins)) {
      el.classList.toggle("focused", n === name);
      el.setAttribute("aria-hidden", "false");
    }
    // dock the scope strip and the recording controls into the focused window
    wins[name].querySelector(".dwin-scope").appendChild(scopeWrap);
    wins[name].querySelector(".dwin-dockbar").appendChild(demoLive);
    requestAnimationFrame(() => Scope.resize());
    if (recording) {
      if (targets[prev] !== targets[name]) targets[prev].recMode(false);
      targets[name].recMode(true);
    }
    renderSwitch();
    renderChips();
    // the boot-time focus happens while sibling modules are still
    // initializing — only broadcast once everyone is wired up
    if (ready) bus.emit("desk:focus", { name });
  }
  function renderSwitch() {
    dswitch.replaceChildren();
    for (const n of ORDER) {
      const b = document.createElement("button");
      b.type = "button";
      b.role = "tab";
      b.className = "dtab" + (n === focusedName ? " on" : "");
      b.setAttribute("aria-selected", String(n === focusedName));
      b.textContent = n;
      b.addEventListener("click", () => focus(n));
      dswitch.appendChild(b);
    }
    const hintEl = document.createElement("span");
    hintEl.className = "sigcap dswitch-cap";
    hintEl.textContent = "focus follows you — alt+tab works too";
    dswitch.appendChild(hintEl);
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
    chip.title = "types itself into the focused window, the way dictation would";
    chip.addEventListener("click", () => Dictation.simulate(SAY[focusedName]));
    chipsEl.append(label, chip);
  }

  for (const [n, el] of Object.entries(wins)) {
    el.addEventListener("pointerdown", () => focus(n), true);
  }
  addEventListener("keydown", (e) => {
    if (e.altKey && e.code === "Tab") {
      e.preventDefault();
      const i = ORDER.indexOf(focusedName);
      focus(ORDER[(i + (e.shiftKey ? ORDER.length - 1 : 1)) % ORDER.length]);
    }
  });

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
