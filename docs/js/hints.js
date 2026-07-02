// ═══ HINTS — a five-stage tour of the instrument ══════════════════════════
import { hint, coarse, SR } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { Config } from "./config.js";
import { Demo } from "./demo-relay.js";
import { Dictation } from "./dictation.js";

export const Hints = (() => {
  function kbd(label) {
    return "<kbd>" + label.replaceAll("+", "</kbd>+<kbd>") + "</kbd>";
  }
  function set() {
    if (Dictation.recording) { listening(); return; }
    hint.replaceChildren();
    const add = (html) => hint.insertAdjacentHTML("beforeend", html);
    if (state.dictations === 0) {
      if (coarse) add("this terminal is the focused app — tap the mic up top, <b>speak</b>, " +
          "then hit the red <b>stop</b> button on the terminal");
      else add("this terminal is the focused app — tap the mic up top or press " +
          kbd(Config.cfg.keyLabel) + ", <b>speak</b>, then tap or press again to stop");
      if (SR) add(' · <span class="engine-live">it actually listens</span> — audio goes to your ' +
                  "browser's speech engine, the way the daemon sends audio to its provider");
      else add(" · no speech engine in this browser, so it will improvise");
    } else if (state.dictations === 1) {
      add("the strip above the terminal froze your sentence's <i>waveform</i> — drawn from the " +
          "signal, then let go");
      if (!coarse) add(". now <b>hold</b> the hotkey and release to stop; <code>hold_threshold_ms = " +
          Config.cfg.holdMs + "</code> below is real, like everything else in that config");
      if (!Demo.want) add(" · or type <code>real</code> — the mic switches to the actual " +
          "xai engine this product ships with (hosted demo)");
    } else if (state.dictations === 2) {
      if (coarse) add("select any text on this page and tap the <b>🔊 read aloud</b> chip " +
          "that appears — the other half of the product");
      else add("select any text on this page and press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd> — " +
          "the other half of the product");
    } else if (state.dictations === 3) {
      add("edit the <a href='#config-h'>live config</a>: change the hotkey, the <code>language</code>, " +
          "or pick a <code>voice_id</code> — the page re-binds itself");
    } else {
      add("type <code>voice-keyboard --help</code> into the terminal above · " +
          "this whole page is one file — view source");
    }
  }
  function listening() {
    hint.innerHTML = coarse
      ? '<span class="engine-live">listening</span> — speak, then hit the red <b>stop</b> button'
      : '<span class="engine-live">listening</span> — speak, then press ' +
        kbd(Config.cfg.keyLabel) + ", hit the <b>stop</b> button, or press <kbd>Esc</kbd>";
  }
  bus.on("cfg:change", set);
  bus.on("rec:start", listening);
  return { advance: set, set };
})();
