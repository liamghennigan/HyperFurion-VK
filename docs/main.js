// This page is a working instance of the product, scaled to one tab.
//
//   board     -> a 3D keyboard whose keys press themselves: every character
//                the voice types is a keycap going down, every repair a
//                backspace, every capital a held Shift, every caret command
//                a chord. The board never pushes Enter: it only lights it
//                (./js/keyboard.js)
//   window    -> the focused app: a real text field with a caret and a
//                selection that the keys type into (./js/buffer.js)
//   dictation -> words land in the field WHILE you speak, molten at first,
//                repairing in place as the transcript firms up, frozen once
//                the utterance closes — the same flow engine the daemon
//                runs (./js/flow.js is a port of voice_keyboard/flow/:
//                grammar, registers, code compilers, "spell that",
//                navigation, the pause rules; CI replays
//                tests/flow_corpus.json through both)
//   stt       -> an open-source speech model (Moonshine tiny) running in
//                this tab, downloaded from jsDelivr and Hugging Face once
//                you first tap the mic and allow it (./js/stt-local.js)
//   autopilot -> a scripted demo, only ever started by a click, under a
//                "scripted demo" badge for its whole run
//   relay     -> the hosted xAI engines, strictly opt-in behind a sheet
//                that says what leaves and where; nothing is sent until you
//                press a button in it
//   footer    -> counts every file the page asked another server for,
//                live, names the servers that answered, and says what you
//                did that caused it (./js/measure.js)
//
// Nothing on this page moves until you do something. Every file under
// ./js/ is readable and none is minified; the page's own files come from
// this site. Other servers are reached only after you ask: the model's
// hosts when you tap the mic and allow it, the relay from its sheet, and your
// browser's own speech service or an online voice only if you pick one.
import { $, reduced } from "./js/env.js";
import { bus } from "./js/bus.js";
import { state } from "./js/state.js";
import "./js/ticker.js";
import { Keyboard } from "./js/keyboard.js";
import { Window } from "./js/window.js";
import { Typist } from "./js/typist.js";
import { Dictation } from "./js/dictation.js";
import "./js/tts.js";
import "./js/hotkeys.js";
import { Autopilot } from "./js/autopilot.js";
import { LocalSTT } from "./js/stt-local.js";
import "./js/hints.js";
import "./js/demo-ui.js";
import "./js/install.js";
import "./js/measure.js";

// ═══ BOOT ════════════════════════════════════════════════════════════════
window.__vk = Object.assign(window.__vk || {}, {
  state, bus, keyboard: Keyboard, window: Window, typist: Typist, dictation: Dictation,
  autopilot: Autopilot, stt: LocalSTT, reduced, ready: true,
});
document.documentElement.classList.add("ready");
// the hero's "try it" button takes you to the mic and puts focus on it:
// the tap — and the download it starts — stays yours, next to the stop
// control and the note that says what the tap fetches
$("hero-try")?.addEventListener("click", (e) => {
  e.preventDefault();
  $("stage")?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  $("mic")?.focus({ preventScroll: true });
});
