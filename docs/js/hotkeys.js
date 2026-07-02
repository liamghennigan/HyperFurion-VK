// ═══ HOTKEYS — honors the live config ═════════════════════════════════════
import { cfgEl, synth } from "./env.js";
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
