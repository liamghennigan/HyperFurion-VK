// This page is a working instance of the product, scaled to one tab.
//
//   board     -> a 3D keyboard whose keys press themselves: every character
//                the voice types is a keycap going down, every repair a
//                backspace, every capital a held Shift. Enter is lit and
//                guarded — the page can never press it (./js/keyboard.js)
//   dictation -> words land in the focused window WHILE you speak, molten
//                at first, repairing in place as the transcript firms up,
//                frozen once they survive the stability window — the same
//                flow engine the daemon runs (./js/flow.js is a port of
//                voice_keyboard/flow/, code registers included)
//   story     -> scrolling carries the board through six scenes; the
//                window changes with it (./js/story.js)
//   light     -> one fragment shader under the board, fed by the same
//                microphone analyser as everything else (./js/light.js)
//   autopilot -> a scripted ghost user for browsers without a speech engine,
//                labeled as scripted every second it runs
//   relay     -> the hosted xAI engines, strictly opt-in behind a sheet
//                that says what leaves and where; the footer counts it
//
// Every file under ./js/ is readable, none is minified, nothing is fetched
// from anyone else's server. Honesty is part of the build.

import { $, reduced, webgl2 } from "./js/env.js";
import { bus } from "./js/bus.js";
import { state } from "./js/state.js";
import "./js/ticker.js";
import { Keyboard } from "./js/keyboard.js";
import { Window } from "./js/window.js";
import { Typist } from "./js/typist.js";
import { Dictation } from "./js/dictation.js";
import "./js/tts.js";
import "./js/hotkeys.js";
import { Story } from "./js/story.js";
import { Light } from "./js/light.js";
import { Autopilot } from "./js/autopilot.js";
import "./js/demo-ui.js";
import "./js/install.js";
import "./js/sound.js";
import "./js/measure.js";

// ═══ BOOT ════════════════════════════════════════════════════════════════
window.__vk = Object.assign(window.__vk || {}, {
  state, bus, keyboard: Keyboard, window: Window, typist: Typist, dictation: Dictation,
  story: Story, light: Light, autopilot: Autopilot, reduced, webgl2, ready: true,
});
document.documentElement.classList.add("ready");
// the hero's "try it" button is the mic, from further away
$("hero-try")?.addEventListener("click", (e) => { e.preventDefault(); Dictation.toggle(); });
