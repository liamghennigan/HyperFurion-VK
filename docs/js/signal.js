// ═══ SIGNAL — the one source every gauge reads ════════════════════════════
import { pill } from "./env.js";
import { Hero } from "./hero2d.js";

export const Signal = (() => {
  const a = { ctx: null, analyser: null, stream: null, buf: null, fbuf: null };
  let simT = 0, frameId = -1e9, cached = null;
  const simFft = new Uint8Array(128);

  async function start() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
    try {
      a.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!a.ctx) a.ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (a.ctx.state === "suspended") await a.ctx.resume();
      const src = a.ctx.createMediaStreamSource(a.stream);
      a.analyser = a.ctx.createAnalyser();
      a.analyser.fftSize = 1024;
      a.buf = new Uint8Array(a.analyser.fftSize);
      a.fbuf = new Uint8Array(a.analyser.frequencyBinCount);
      src.connect(a.analyser);
      pill.classList.add("real");
      Hero.caption();
    } catch { /* every gauge falls back to the synthesized signal */ }
  }
  function stop() {
    if (a.stream) a.stream.getTracks().forEach((t) => t.stop());
    a.stream = null;
    a.analyser = null;
    pill.classList.remove("real");
    pill.querySelectorAll("i").forEach((b) => { b.style.transform = ""; });
  }
  function frame() {
    const now = performance.now();
    if (cached && now - frameId < 8) return cached;
    frameId = now;
    if (a.analyser) {
      a.analyser.getByteTimeDomainData(a.buf);
      let peak = 0;
      for (let i = 0; i < a.buf.length; i++) peak = Math.max(peak, Math.abs(a.buf[i] - 128) / 128);
      a.analyser.getByteFrequencyData(a.fbuf);
      cached = { peak: Math.min(1, peak * 1.6), fft: a.fbuf, live: true };
      return cached;
    }
    // synthesized signal: speech-like bursts + a harmonic spectrum
    simT += 1 / 60;
    const talking = Math.sin(simT * 1.9) > -0.3 ? 1 : 0.08;
    const peak = Math.min(1, (Math.abs(Math.sin(simT * 9)) * .5 +
      Math.abs(Math.sin(simT * 23)) * .25 + Math.random() * .18) * talking * .8);
    const spread = 9 + 5 * Math.sin(simT * 1.3);
    for (let i = 0; i < simFft.length; i++) {
      let v = peak * 235 * Math.exp(-i / spread);
      v += (i % 7 === 2 ? 60 : 0) * peak * Math.abs(Math.sin(simT * 4 + i));
      v += Math.random() * 14;
      simFft[i] = Math.min(255, v);
    }
    cached = { peak, fft: simFft, live: false };
    return cached;
  }
  return {
    start, stop, frame,
    isLive: () => !!a.analyser,
    // the relay dictation path taps the same mic acquisition
    audio: () => ({ ctx: a.ctx, stream: a.stream }),
  };
})();
