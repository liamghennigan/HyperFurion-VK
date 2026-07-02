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
import { Desktop } from "./js/desktop.js";
import { Autopilot } from "./js/autopilot.js";
import { Field } from "./js/field.js";
import "./js/scrollfx.js";
import "./js/cta.js";

// ═══ BOOT ════════════════════════════════════════════════════════════════
window.__vk.demo = Demo;         // the proof harness steers the demo layer
window.__vk.field = Field;
window.__vk.desktop = Desktop;
window.__vk.autopilot = Autopilot;
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
// a meter settling, when motion is welcome. If you opted into the hosted
// demo before scrolling here, its requests are counted and said out loud.
try {
  const nav = performance.getEntriesByType("navigation")[0];
  const htmlBytes = (nav && (nav.transferSize || nav.decodedBodySize)) ||
    ("<!doctype html>" + document.documentElement.outerHTML).length;
  const bytesEl = $("bytes");
  const measure = () => {
    const res = performance.getEntriesByType("resource");
    let own = 0, ownBytes = 0, foreign = 0;
    for (const r of res) {
      try {
        if (new URL(r.name).origin === location.origin) {
          own++; ownBytes += r.transferSize || r.decodedBodySize || 0;
        } else foreign++;
      } catch {}
    }
    return {
      files: own + 1,
      kb: Math.round((htmlBytes + ownBytes) / 1024),
      foreign,
    };
  };
  const label = (m, kb) =>
    m.files + " files · " + kb + " KB · all self-hosted · " +
    (m.foreign ? m.foreign + " opt-in request" + (m.foreign > 1 ? "s" : "") + " (you asked)"
               : "zero third-party requests") +
    " · zero analytics";
  if (reduced || !("IntersectionObserver" in window)) {
    const m = measure();
    bytesEl.textContent = label(m, m.kb);
  } else {
    const mio = new IntersectionObserver((es) => {
      if (!es.some((e) => e.isIntersecting)) return;
      mio.disconnect();
      const m = measure();
      const t0 = performance.now();
      const step = (t) => {
        const k = Math.min(1, (t - t0) / 900);
        bytesEl.textContent = label(m, Math.round(m.kb * k * (2 - k)));
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
    mio.observe(bytesEl);
  }
} catch {}
