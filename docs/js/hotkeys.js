// ═══ HOTKEYS — ctrl+alt+v, auto mode: tap toggles, hold talks ═════════════
import { synth, reduced } from "./env.js";
import { bus } from "./bus.js";
import { settings } from "./settings.js";
import { Dictation } from "./dictation.js";
import { TTS } from "./tts.js";
import { Keyboard } from "./keyboard.js";

(() => {
  const cfg = settings;
  function comboDown(e) {
    return e.ctrlKey === cfg.mods.ctrl && e.altKey === cfg.mods.alt &&
           e.shiftKey === cfg.mods.shift && e.metaKey === cfg.mods.meta && e.code === cfg.code;
  }
  // text fields block the combo (AltGr layouts type through ctrl+alt)
  const inField = (e) => e.target && (e.target.isContentEditable ||
    /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName));

  let keyDown = false, holdStarted = false, holdTimer = 0;
  addEventListener("keydown", (e) => {
    // tts hotkey: ctrl+alt+r on a selection — the app's read-aloud key
    // on Windows (on Linux you bind one to `voice-keyboard tts`). Not in a
    // text field, where AltGr layouts type through ctrl+alt.
    if (e.ctrlKey && e.altKey && !e.shiftKey && !e.metaKey && e.code === "KeyR" && !inField(e)) {
      e.preventDefault();
      TTS.speakSelection();
      return;
    }
    if (e.code === "Escape") {
      if (Dictation.recording) Dictation.stop();
      if (synth) { synth.cancel(); TTS.clearHighlight(); bus.emit("tts:end"); }
      return;
    }
    if (!comboDown(e) || inField(e)) return;
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

// ═══ your real fingers press the board on screen ══════════════════════════
// A small, honest delight: whatever you type on your physical keyboard
// presses the same key on the 3D board (sand-colored, nothing is inserted).
// Enter still never goes down — its ring flashes: yours.
(() => {
  if (reduced) return;
  const MODS = new Set(["ControlLeft","ControlRight","AltLeft","AltRight","ShiftLeft","ShiftRight","MetaLeft","MetaRight"]);
  addEventListener("keydown", (e) => {
    if (e.repeat) return;
    if (MODS.has(e.code)) Keyboard.press(e.code, { heat: "user", hold: true });
    else Keyboard.press(e.code, { heat: "user" });
  }, true);
  addEventListener("keyup", (e) => { if (MODS.has(e.code)) Keyboard.release(e.code); }, true);
  addEventListener("blur", () => Keyboard.releaseAll());
})();
