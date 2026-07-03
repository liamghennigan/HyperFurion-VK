// ═══ HERO — the master gauge ══════════════════════════════════════════════
import { heroCanvas, heroCap, reduced } from "./env.js";
import { bus } from "./bus.js";
import { Ticker } from "./ticker.js";
import { Signal } from "./signal.js";
import { Dictation } from "./dictation.js";

export const Hero = (() => {
  const ctx = heroCanvas.getContext("2d");
  let W = 0, H = 0, dpr = 1, mode = "idle", phase = 0;

  function size() {
    dpr = Math.min(devicePixelRatio || 1, 1.5);
    W = heroCanvas.width = Math.round(heroCanvas.clientWidth * dpr);
    H = heroCanvas.height = Math.round(heroCanvas.clientHeight * dpr);
  }
  function hot() {
    return getComputedStyle(document.documentElement).getPropertyValue("--hot").trim() || "#eafcff";
  }
  function waveColor() {
    return getComputedStyle(document.documentElement).getPropertyValue("--wave").trim() || "#22d3ee";
  }
  // trails decay toward transparent so the CSS backdrop (paper grid /
  // phosphor glow) stays visible beneath the signal
  function fade(alpha) {
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = "rgba(0,0,0," + alpha + ")";
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = "source-over";
  }
  function trace(t) {
    fade(.09);
    const mid = H * .46, amp = H * .11;
    const passes = [[6 * dpr, .05, waveColor()], [2.4 * dpr, .22, waveColor()], [1 * dpr, .85, hot()]];
    for (const [lw, al, col] of passes) {
      ctx.beginPath();
      for (let x = 0; x <= W; x += 4 * dpr) {
        const u = x / W * Math.PI * 2;
        const y = mid +
          Math.sin(u * 3 + t * .7 + phase) * amp * .55 +
          Math.sin(u * 7 - t * .43) * amp * .3 +
          Math.sin(u * 13 + t * .21) * amp * .12;
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.strokeStyle = col;
      ctx.globalAlpha = al;
      ctx.lineWidth = lw;
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
  function waterfall() {
    const f = Signal.frame();
    fade(.02);                      // phosphor decay: old rows dissolve as they rise
    ctx.drawImage(heroCanvas, 0, -2 * dpr);
    ctx.globalCompositeOperation = "destination-out";
    ctx.globalAlpha = 1;
    ctx.fillRect(0, H - 2 * dpr, W, 2 * dpr);
    ctx.globalCompositeOperation = "source-over";
    const wv = waveColor(), ht = hot(), bins = f.fft.length;
    for (let x = 0; x < W; x += 3 * dpr) {
      const bin = Math.floor(Math.pow(x / W, 1.7) * (bins - 1));
      const v = f.fft[bin] / 255;
      if (v < .05) continue;
      ctx.fillStyle = v > .85 ? ht : wv;
      ctx.globalAlpha = Math.min(.95, v * .9);
      ctx.fillRect(x, H - 2 * dpr, 2 * dpr, 2 * dpr);
    }
    ctx.globalAlpha = 1;
  }
  function caption() {
    heroCap.hidden = false;
    // mode is only ever "rec" after boot, so Dictation exists by then
    const relayTag = mode === "rec" && Dictation.engine === "relay"
      ? " · engine: xai grok stt (hosted demo)" : "";
    // when the field has taken over, this canvas is hidden and the signal
    // paints the particles instead of a waterfall — say so
    const gauge = heroCanvas.style.display === "none" ? "driving the field" : "spectrogram";
    heroCap.textContent = mode === "rec"
      ? (Signal.isLive() ? "signal: live microphone — " + gauge + relayTag
                         : "signal: synthetic (mic not granted)")
      : "signal: test pattern";
  }
  // reduced-motion recording readout: a state change, not motion
  let meterInt = 0;
  function meterOn() {
    meterInt = setInterval(() => {
      const f = Signal.frame();
      const bars = "▮".repeat(1 + Math.round(f.peak * 5)) + "▯".repeat(5 - Math.round(f.peak * 5));
      heroCap.textContent = (Signal.isLive() ? "signal: live microphone " : "signal: synthetic ") + bars;
    }, 500);
  }
  function meterOff() { clearInterval(meterInt); caption(); }

  size();
  caption();
  if (reduced) { trace(2.2); }
  else {
    Ticker.add({ el: heroCanvas, fps: 30, fn: (dt, t) => { mode === "rec" ? waterfall() : trace(t); } });
  }
  let rz = 0;
  addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { size(); if (reduced) trace(2.2); }, 150); });
  bus.on("rec:start", () => {
    mode = "rec"; phase += 1.7;
    ctx.clearRect(0, 0, W, H);   // clean slate for the spectrogram
    caption();
    if (reduced) meterOn();
  });
  bus.on("rec:stop", () => { mode = "idle"; caption(); if (reduced) meterOff(); });
  return { caption };
})();
