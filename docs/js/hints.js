// ═══ HINTS — a six-stage tour of the instrument ═══════════════════════════
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
      const focused = state.focusedApp || "editor";
      if (coarse) add("the <b>" + focused + "</b> above is live — tap the mic up top, " +
          "<b>speak</b>, then hit the red <b>stop</b> button. words land while you talk");
      else add("the <b>" + focused + "</b> above is live — tap the mic up top or press " +
          kbd(Config.cfg.keyLabel) + ", <b>speak</b>, and words land <i>while you talk</i>");
      if (SR) add(' · <span class="engine-live">it actually listens</span> — audio goes to your ' +
                  "browser's speech engine, the way the daemon sends audio to its provider");
      else add(' · no speech engine in this browser — hit <b>▶ watch it work</b> up top ' +
               "and the page will drive itself, honestly labeled");
    } else if (state.dictations === 1) {
      add("the <span class='molten-word'>amber</span> words were <b>molten</b> — still allowed to " +
          "repair themselves. after <code>stability_ms</code> they froze to ink, never to be " +
          "touched again. now say <b>“scratch that”</b> — spoken edits are commands, not text");
      if (!Demo.want) add(" · or type <code>real</code> in the terminal — the mic switches to " +
          "the actual xai engine this product ships with");
    } else if (state.dictations === 2) {
      add("end a sentence with <b>“" + (Config.cfg.wakeWord || "furion") +
          ", make that formal”</b> — the wake word routes what you just typed through an " +
          "LLM and repairs it on screen. try the second chip if your mic is shy");
    } else if (state.dictations === 3) {
      if (coarse) add("select any text on this page and tap the <b>🔊 read aloud</b> chip " +
          "that appears — the other half of the product");
      else add("select any text on this page and press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd> — " +
          "the other half of the product");
    } else if (state.dictations === 4) {
      add("edit the <a href='#config-h'>live config</a>: set <code>stability_ms = 4000</code> " +
          "and watch words stay molten longer · set <code>auto_stop_ms = 1200</code>, dictate, " +
          "and just stop talking");
    } else {
      add("type <code>voice-keyboard --help</code> into the terminal above — " +
          "<code>transform</code>, <code>history</code>, <code>recall</code> are new · " +
          "every line of this page is readable — view source");
    }
  }
  function listening() {
    hint.innerHTML = (coarse
      ? '<span class="engine-live">listening</span> — speak, then hit the red <b>stop</b> button'
      : '<span class="engine-live">listening</span> — speak, then press ' +
        kbd(Config.cfg.keyLabel) + ", hit the <b>stop</b> button, or press <kbd>Esc</kbd>") +
      ' · try saying <b>“period”</b>, <b>“new line”</b>, <b>“scratch that”</b>';
  }
  bus.on("cfg:change", set);
  bus.on("rec:start", listening);
  bus.on("desk:focus", () => { if (state.dictations === 0) set(); });
  return { advance: set, set };
})();
