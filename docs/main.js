// This page is a working instance of the product, scaled to one tab.
// One signal feeds many gauges: a single microphone analyser drives the
// full-viewport hero (idle: a synthetic test pattern; recording: a real
// spectrogram of your voice), the scope strip, the VU meter, the signal-
// path diagram, and the scope's frozen waveform — simultaneously.
//
//   dictation  -> the browser's speech engine types into the terminal,
//                 the pipeline lights up, and the sentence's loudness
//                 envelope freezes onto the scope
//   tts        -> select text, Ctrl+Alt+T speaks it; where the browser
//                 supports the CSS Custom Highlight API, each word
//                 lights up as it is read; the reverse lane glows
//   config     -> the config.toml on the page is parsed live and
//                 re-binds hotkey, mode, hold threshold, language,
//                 interim rendering, and the tts voice/rate/pitch
//   terminal   -> the prompt is real: voice-keyboard toggle | start |
//                 stop | status | tts | version | --help
//   hosted demo-> `real` reroutes the mic to actual xai grok stt through
//                 a rate-limited relay; `say` plays the real eve voice;
//                 `ask` answers questions via grok — all opt-in, all
//                 labeled, all budget-capped server-side
//
// When the browser has no speech engine or denies the mic, dictation
// falls back to canned transcripts and a synthesized signal — and every
// instrument says so in its caption. Honesty is part of the UI.
//
// The source is split by instrument under ./js/ — every file is readable,
// none is minified. Honesty is part of the build, too.

import { $, reduced, cfgEl } from "./js/env.js";
import "./js/bus.js";
import "./js/ticker.js";
import "./js/signal.js";
import "./js/hero2d.js";
import "./js/scope.js";
import "./js/config.js";
import { Demo } from "./js/demo-relay.js";
import "./js/terminal.js";
import "./js/dictation.js";
import "./js/pipeline.js";
import "./js/tts.js";
import "./js/hotkeys.js";
import { Hints } from "./js/hints.js";
import { Field } from "./js/field.js";
import "./js/scrollfx.js";

// ═══ BOOT ════════════════════════════════════════════════════════════════
window.__vk.demo = Demo;         // the proof harness steers the demo layer
window.__vk.field = Field;
// the field succeeds the 2D trace; hiding the hero canvas also parks its
// draw (the ticker skips subscribers whose element left the viewport).
// Without WebGL2 — or with reduced motion — the old instrument stays on.
if (Field.active) $("hero-canvas").style.display = "none";
$("hero-live").hidden = false;   // the claim is only true with JS running
$("cfg-live-note").hidden = $("page-note").hidden = false;
cfgEl.contentEditable = "plaintext-only";
if (cfgEl.contentEditable !== "plaintext-only") cfgEl.contentEditable = "true";
Hints.set();
// colophon: the real size, measured, not claimed — and counted up like
// a meter settling, when motion is welcome
try {
  const nav = performance.getEntriesByType("navigation")[0];
  const bytes = (nav && nav.decodedBodySize) ||
    ("<!doctype html>" + document.documentElement.outerHTML).length;
  const kb = Math.round(bytes / 1024);
  const bytesEl = $("bytes");
  const label = (n) =>
    "one file · zero dependencies · " + n + " KB · zero requests after load";
  if (kb) {
    if (reduced || !("IntersectionObserver" in window)) {
      bytesEl.textContent = label(kb);
    } else {
      bytesEl.textContent = label(0);
      const mio = new IntersectionObserver((es) => {
        if (!es.some((e) => e.isIntersecting)) return;
        mio.disconnect();
        const t0 = performance.now();
        const step = (t) => {
          const k = Math.min(1, (t - t0) / 900);
          bytesEl.textContent = label(Math.round(kb * k * (2 - k)));
          if (k < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      });
      mio.observe(bytesEl);
    }
  }
} catch {}
