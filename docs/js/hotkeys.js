// ═══ HOTKEYS — honors the live config ═════════════════════════════════════
import { cfgEl, synth, reduced } from "./env.js";
import { bus } from "./bus.js";
import { Config } from "./config.js";
import { Dictation } from "./dictation.js";
import { TTS } from "./tts.js";

(() => {
  const cfg = Config.cfg;
  function comboDown(e) {
    return e.ctrlKey === cfg.mods.ctrl && e.altKey === cfg.mods.alt &&
           e.shiftKey === cfg.mods.shift && e.metaKey === cfg.mods.meta && e.code === cfg.code;
  }
  // the config editor blocks the combo (AltGr layouts type through ctrl+alt);
  // the terminal input does not — modifier chords never insert text there
  const inEditor = (e) => e.target === cfgEl || (e.target.isContentEditable && e.target !== cfgEl);

  let keyDown = false, holdStarted = false, holdTimer = 0;
  addEventListener("keydown", (e) => {
    // tts hotkey: ctrl+alt+t on a selection
    if (e.ctrlKey && e.altKey && !e.shiftKey && !e.metaKey && e.code === "KeyT") {
      e.preventDefault();
      TTS.speakSelection();
      return;
    }
    if (e.code === "Escape") {
      if (Dictation.recording) Dictation.stop();
      if (synth) { synth.cancel(); TTS.clearHighlight(); bus.emit("tts:end"); }
      return;
    }
    if (!comboDown(e) || inEditor(e)) return;
    e.preventDefault();
    if (e.repeat || keyDown) return;
    keyDown = true;
    holdStarted = false;
    if (cfg.mode === "toggle") { Dictation.toggle(); return; }
    if (cfg.mode === "hold") { if (!Dictation.recording) { holdStarted = true; Dictation.start(); } return; }
    holdTimer = setTimeout(() => {           // auto
      if (keyDown && !Dictation.recording) { holdStarted = true; Dictation.start(); }
    }, cfg.holdMs);
  });
  addEventListener("keyup", (e) => {
    if (!keyDown) return;
    if (e.code !== cfg.code && !["ControlLeft","ControlRight","AltLeft","AltRight",
        "ShiftLeft","ShiftRight","MetaLeft","MetaRight"].includes(e.code)) return;
    keyDown = false;
    clearTimeout(holdTimer);
    if (cfg.mode === "toggle") return;
    if (holdStarted) { Dictation.stop(); return; }
    if (cfg.mode === "auto") Dictation.toggle();
  });
})();

// ═══ the kbd glyphs on the page light up under your real fingers ══════════
// A small, honest delight: hold Ctrl and every <kbd>Ctrl</kbd> on screen
// presses itself — the page teaching its own hotkey.
(() => {
  if (reduced) return;
  const NAME = {
    Control: ["ctrl", "control"], Alt: ["alt"], Shift: ["shift"],
    Meta: ["meta", "super"], Escape: ["esc", "escape"],
  };
  const pressed = new Set();
  let kbds = null;
  function labels(e, on) {
    const l = NAME[e.key] || (/^Key[A-Z]$/.test(e.code) ? [e.code.slice(3).toLowerCase()] : null);
    if (!l) return false;
    for (const n of l) on ? pressed.add(n) : pressed.delete(n);
    return true;
  }
  function paint() {
    kbds = kbds || document.querySelectorAll("kbd");
    for (const k of kbds) k.classList.toggle("down", pressed.has(k.textContent.trim().toLowerCase()));
  }
  addEventListener("keydown", (e) => { if (labels(e, true)) { kbds = null; paint(); } }, true);
  addEventListener("keyup", (e) => { if (labels(e, false)) paint(); }, true);
  addEventListener("blur", () => { pressed.clear(); if (kbds) paint(); });
})();
