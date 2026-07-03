// ═══ SCOPE + VU — the terminal's strip chart ══════════════════════════════
import { scope, scopeLabel, pill, reduced } from "./env.js";
import { bus } from "./bus.js";
import { Ticker } from "./ticker.js";
import { Signal } from "./signal.js";
import { Dictation } from "./dictation.js";
import { paintEnvelope, waveColor } from "./paint.js";

export const Scope = (() => {
  const sctx = scope.getContext("2d");
  let W = 0, H = 0;
  const hist = [];
  let frozen = null;

  function size() {
    const dpr = devicePixelRatio || 1;
    W = scope.clientWidth; H = scope.clientHeight;
    scope.width = Math.round(W * dpr);
    scope.height = Math.round(H * dpr);
    sctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function drawBars(values, alpha) {
    const step = 3, mid = H / 2;
    sctx.fillStyle = waveColor();
    sctx.globalAlpha = alpha;
    for (let i = 0; i < values.length; i++) {
      const v = Math.max(values[i], .02);
      const h = Math.max(1.5, v * (H - 8));
      sctx.fillRect(W - (values.length - i) * step, mid - h / 2, 2, h);
    }
    sctx.globalAlpha = 1;
  }
  function drawIdle() {
    sctx.clearRect(0, 0, W, H);
    if (frozen) { paintEnvelope(sctx, W, H, frozen, .45, waveColor()); return; }
    sctx.fillStyle = waveColor();
    sctx.globalAlpha = .35;
    sctx.fillRect(0, H / 2 - .5, W, 1);
    sctx.globalAlpha = 1;
  }
  // data capture is never viewport-gated; only drawing is
  function sample() {
    if (!Dictation.recording) return;
    const f = Signal.frame();
    hist.push(f.peak);
    if (f.live) {
      const bands = [2, 8, 24, 64];
      pill.querySelectorAll("i").forEach((bar, i) => {
        const v = f.fft[bands[i]] / 255;
        bar.style.transform = "scaleY(" + Math.max(.2, v).toFixed(2) + ")";
      });
    }
  }
  function tick() {
    if (!Dictation.recording) return;
    sctx.clearRect(0, 0, W, H);
    if (frozen) paintEnvelope(sctx, W, H, frozen, .10, waveColor());
    drawBars(hist.slice(-Math.floor(W / 3)), .9);
  }
  function freeze() {
    if (!hist.length) return;
    const n = Math.max(24, Math.floor(W / 3));
    const print = new Array(n).fill(0);
    for (let i = 0; i < hist.length; i++) {
      const b = Math.floor(i * n / hist.length);
      print[b] = Math.max(print[b], hist[i]);
    }
    frozen = print;
    hist.length = 0;
    drawIdle();
    scopeLabel.textContent = "waveform";
  }
  size();
  drawIdle();
  addEventListener("resize", () => { size(); drawIdle(); });
  if (!reduced) {
    Ticker.add({ fn: sample });            // ungated: the data
    Ticker.add({ el: scope, fn: tick });   // gated: the drawing
  }
  bus.on("rec:start", () => { scopeLabel.textContent = "signal"; hist.length = 0; if (reduced) drawIdle(); });
  return {
    freeze,
    push(peak) { hist.push(peak); },
    // the strip docks into whichever window has focus; remeasure after a move
    resize() { size(); if (!Dictation.recording) drawIdle(); },
  };
})();
