// ═══ WINDOW — the focused app, above the board ════════════════════════════
// One window, five registers. The tabs pick the register by hand; the
// daemon's focus probe picks prose or terminal by itself (python, shell
// and javascript are for apps you map to them in [registers.map]). The
// editor renders prose (smart caps, spoken punctuation), the terminal
// renders digits and no caps, python, shell and javascript compile speech.
// Each register is its own app with its own document (buffer.js): a text
// field with a caret and a selection, which the Typist types into and
// navigation commands move around in. The window never changes height:
// one status line, replaced, never stacked; while a scripted session
// plays, a badge over the text says so, from its first word to its last.
import { $, fwin, os } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { settings } from "./settings.js";
import { REGISTERS, moltenLine } from "./flow.js";
import { createBuffer } from "./buffer.js";
import { keymap, chordsFor } from "./nav.js";

export const Window = (() => {
  const TITLES = {
    prose: ["notes.md", "editor"], terminal: ["~", "bash"],
    python: ["main.py", "editor"], shell: ["~", "zsh"], javascript: ["app.ts", "editor"],
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
    if (badge) place(badge.el);   // the app name's width moved it
    if (el.tabs) for (const t of el.tabs.querySelectorAll("[role=tab]")) {
      const on = t.dataset.register === name;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
    }
    if (changed) { paintArgs = { moltenLen: 0, heat: "molten", moved: false }; paintDoc(); if (standin) clearStandin(); }
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
    // the rewrite types its result as a repair: that is when it has landed
    if (heat === "repair" && standinArmed && performance.now() - standinArmed < 4000) {
      standinArmed = 0; standinOn = true; paintStandin();
    }
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

  // ── status: one line, replaced ──────────────────────────────────────────
  // its text wraps to two lines at most, then is cut (the full text is the hover title); painted
  // only when it changes, so a screen reader hears each status once
  let painted = "";
  function paintStatus() {
    if (!el.status) return;
    const text = st.note || [st.engine, st.latency].filter(Boolean).join(" · ");
    const cls = st.note ? st.noteCls : st.cls;
    const yours = !st.note && register().terminal && buffer().text && !state.recording;
    const key = cls + "\n" + text + "\n" + yours;
    if (key === painted) return;
    painted = key;
    const msg = document.createElement("span");
    msg.className = "msg"; msg.textContent = text;
    el.status.replaceChildren(msg);
    el.status.className = "fwin-status " + cls;
    el.status.title = text;
    if (yours) {
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

  // ── the scripted badge: on for a scripted session's whole run ──────────
  // Owners ("autopilot", "hint") switch it on and off; it shows while any
  // of them is playing, with the words the script just "said". It floats
  // over the window, so the window keeps its height: at the top of the
  // text where the title bar is one row, beside the app name where the
  // tabs have wrapped under it (a phone).
  const scriptOwners = new Set();
  const PILL = "position:absolute;margin:0;z-index:2;pointer-events:none;white-space:nowrap;overflow:hidden;" +
    "text-overflow:ellipsis;font:500 11px/1.7 var(--mono);letter-spacing:.02em;background:var(--obsidian-2);" +
    "border:1px solid;border-radius:999px;padding:1px 10px;box-sizing:border-box";
  function pill(id, color, border) {
    const b = document.createElement("p");
    b.id = id; b.hidden = true;
    b.style.cssText = PILL + ";color:" + color + ";border-color:" + border;
    fwin.appendChild(b);
    return b;
  }
  function place(b) {
    if (!b || b.hidden) return;
    const f = fwin.getBoundingClientRect(), body = fwin.querySelector(".fwin-body");
    const name = el.app || el.title, wrapped = el.tabs && name && el.tabs.offsetTop > name.offsetTop + 4;
    if (wrapped) {
      const r = name.getBoundingClientRect();
      b.style.top = (r.top - f.top + r.height / 2) + "px"; b.style.transform = "translateY(-50%)";
      const room = f.right - r.right - 24;
      b.style.maxWidth = Math.max(80, room) + "px";
      if (badge && b === badge.el) badge.said.style.display = room < 240 ? "none" : "";   // no room to quote: the label alone
      if (b === standin) b.textContent = room < 190 ? "page stand-in" : "page stand-in, not an LLM";
    } else {
      if (badge && b === badge.el) badge.said.style.display = "";
      if (b === standin) b.textContent = "rewritten by the page's stand-in, not a language model: formal swaps, upper or title case";
      b.style.top = ((body ? body.getBoundingClientRect().top - f.top : 44) + 5) + "px"; b.style.transform = "";
      b.style.maxWidth = "calc(100% - 16px)";
    }
    b.style.right = "8px";
  }
  const badge = (() => {
    if (!fwin || !fwin.querySelector(".fwin-body")) return null;
    const b = pill("fwin-scripted", "var(--saffron)", "rgba(233,162,59,.5)");
    b.setAttribute("role", "status");
    const tag = document.createElement("strong"); tag.textContent = "scripted demo"; tag.style.fontWeight = "600";
    const said = document.createElement("span"); said.style.color = "var(--sand-100)";
    b.append(tag, said);
    return { el: b, said };
  })();
  // after the wake-word rewrite lands, a second label says whose rewrite
  // it was, until the next dictation: this page has no language model
  const standin = badge && (() => {
    const b = pill("fwin-standin", "var(--sand-100)", "var(--glass-line-2)");
    b.title = "This page has no language model. Its stand-in knows three rewrites: formal (a fixed list of word swaps), " +
      "upper case and title case. The app sends the instruction to your language model.";
    return b;
  })();
  let standinArmed = 0, standinOn = false;
  function paintStandin() { if (standin) { standin.hidden = !standinOn || isScripted(); place(standin); } }
  function clearStandin() { standinArmed = 0; standinOn = false; paintStandin(); }
  bus.on("flow:did", ({ kind }) => { if (kind === "rewrite") standinArmed = performance.now(); });
  addEventListener("resize", () => { if (badge) place(badge.el); place(standin); });
  function scriptSaid(text) {
    if (!badge) return;
    badge.said.textContent = text ? " · “" + text + "”" : " · not your voice · nothing is listening";
  }
  function scripted(owner, on) {
    if (on) scriptOwners.add(owner); else scriptOwners.delete(owner);
    const show = scriptOwners.size > 0;
    if (show && on && scriptOwners.size === 1) ended = true;   // a new scripted run: new panes
    if (!badge) return;
    if (show && badge.el.hidden) scriptSaid("");
    badge.el.hidden = !show;
    place(badge.el);
    if (show && on) clearStandin(); else paintStandin();
  }
  const isScripted = () => scriptOwners.size > 0;

  // ── the registers section: your last dictation through every register ──
  // Each pane replays the utterances of the last dictation through its own
  // register's engine and its own text field — scratches, spelled words,
  // caret commands and all — so the panes differ exactly where the
  // registers do. Until you dictate, they show a sample, labeled as one.
  const SAMPLE = ["total equals count times two"];
  let said = [], ended = true;
  function replay(utterances, name) {
    const reg = REGISTERS[name];
    const line = moltenLine({ register: reg, cfg: { ...settings, stabilityMs: Infinity } });
    const buf = createBuffer({ terminal: !!reg.terminal });
    const keys = keymap({ terminal: !!reg.terminal, platform: os });
    let shown = "";
    const type = (want) => {   // the Typist's diff: back up to where they differ, retype the rest
      let p = 0;
      while (p < shown.length && p < want.length && shown[p] === want[p]) p++;
      for (let i = shown.length; i > p; i--) buf.backspace();
      for (const ch of want.slice(p)) buf.insert(ch);
      shown = want;
    };
    const press = (r) => {     // the text lands, the chord moves the caret, typing resumes there
      for (let n = 0; r && r.action && n < 50; n++) {
        type(r.text !== undefined ? r.text : r.frozen);
        const chords = chordsFor(r.action.action, r.action.count, keys);
        if (chords) { for (const c of chords) buf.press(c); shown = ""; }
        r = line.completeAction(0, { pressed: !!chords }) || line.peek();
      }
      return r;
    };
    let raw = "";
    for (const u of utterances) {
      raw = (raw + " " + u).trim();
      const r = press(line.update(raw, 0, { final: true }));
      line.takeInstruction();   // "VK, …" is an instruction, not words to type
      if (r) type(r.frozen);
    }
    type(press(line.finalize(raw, 1)).text);
    return buf.text;
  }
  function paintPanes(utterances, sample) {
    if (!el.panes) return;
    const out = [];
    for (const pane of el.panes.querySelectorAll("[data-register]")) {
      const name = pane.dataset.register;
      if (!REGISTERS[name]) continue;
      let text = "";
      try { text = replay(utterances, name); } catch (e) { console.error("panes", e); }
      out.push([pane, text]);
    }
    // a dictation that typed nothing anywhere (an instruction alone) leaves the panes as they were
    if (!sample && out.every(([, t]) => !t)) return;
    for (const [pane, text] of out) { const t = pane.querySelector(".pane-text"); if (t) t.textContent = text; }
    let cap = $("register-said");
    if (!cap) {
      cap = document.createElement("figcaption");
      cap.id = "register-said"; cap.className = "sigcap";
      el.panes.appendChild(cap);
    }
    const q = utterances.map((u) => "“" + u + "”").join(" · ");
    cap.textContent = sample ? "said: " + q + " — a sample; dictate above and the panes show yours"
      : (isScripted() ? "the scripted demo said: " : "you said: ") + q;
  }
  // dictation.js calls this as each utterance closes (state.lastRaw is it)
  function panes() {
    if (ended) { said = []; ended = false; }
    const u = (state.lastRaw || "").trim();
    if (!u) return;
    said.push(u);
    if (isScripted()) scriptSaid(u);
    paintPanes(said, false);
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
  // keystroke of the molten repair — and they hear what is on screen: the
  // field's text from where this dictation changed it, not the engine's
  // record of what it typed (which still holds words a scratch, a spelled
  // fix or a caret command later replaced)
  const heard = new Map();   // register -> the field's text when it was last announced
  let focusMoved = false;
  bus.on("rec:start", () => { focusMoved = false; ended = true; clearStandin(); });
  bus.on("focus:changed", () => { focusMoved = true; });
  bus.on("doc:cleared", () => { heard.set(regName, ""); said = []; ended = true; clearStandin(); });
  bus.on("type:text", ({ text }) => {
    ended = true;            // the next utterance starts the next dictation's panes
    if (!el.announce) return;
    const who = isScripted() ? "scripted demo " : "";
    if (focusMoved) {
      el.announce.textContent = "focus changed while you spoke, so typing stopped there; the transcript: " + text;
      return;
    }
    const now = buffer().text, was = heard.get(regName) || "";
    heard.set(regName, now);
    let p = 0;
    while (p < was.length && p < now.length && was[p] === now[p]) p++;
    if (p < now.length && !/\s/.test(now[p])) while (p > 0 && !/\s/.test(now[p - 1])) p--;   // a changed word is read whole
    const spoken = (s) => s.trim().split(/\n+/).map((l) => l.trim()).filter(Boolean).join(" — new line — ");
    const added = spoken(now.slice(p));
    el.announce.textContent = added ? who + "typed: " + added
      : who + "dictation done; " + (now.trim() ? "the line now reads: " + spoken(now.slice(now.lastIndexOf("\n") + 1)) : "the field is empty");
  });
  setRegister("prose", { silent: true });
  paintDoc();
  paintPanes(SAMPLE, true);

  return { setRegister, register, focusedName, buffer, renderLine, moved, paintDoc, instr, clearDoc,
           log, setEngine, setLatency, setLatencyNote, panes, scripted, isScripted, replay };
})();
