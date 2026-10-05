// ═══ SOUND — a synthesized key click, off by default ══════════════════════
// No samples: a 10 ms noise burst through a bandpass and a short low
// "thock". Created on the toggle's gesture, muted while the tab is hidden.
import { $ } from "./env.js";
import { bus } from "./bus.js";
import { settings } from "./settings.js";

(() => {
  const btn = $("sound");
  if (!btn) return;
  let ctx = null, noise = null;
  function ensure() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    if (!ctx) {
      ctx = new AC();
      const len = ctx.sampleRate * 0.05;
      noise = ctx.createBuffer(1, len, ctx.sampleRate);
      const d = noise.getChannelData(0);
      for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return true;
  }
  function click({ code, heat }) {
    if (!settings.sound || !ctx || document.hidden) return;
    const t = ctx.currentTime;
    const det = 1 + (Math.random() - 0.5) * 0.14;
    const quiet = /Shift|Control|Alt|Meta/.test(code || "") ? 0.65 : 1;
    const low = code === "Backspace" ? 0.8 : 1;
    const src = ctx.createBufferSource(); src.buffer = noise;
    const bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 2300 * det * low; bp.Q.value = 1.1;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.09 * quiet, t + 0.004); g.gain.exponentialRampToValueAtTime(0.0005, t + 0.03);
    src.connect(bp); bp.connect(g); g.connect(ctx.destination);
    src.start(t); src.stop(t + 0.035);
    const o = ctx.createOscillator(); o.frequency.value = 170 * det * low;
    const g2 = ctx.createGain();
    g2.gain.setValueAtTime(0.05 * quiet, t); g2.gain.exponentialRampToValueAtTime(0.0005, t + 0.022);
    o.connect(g2); g2.connect(ctx.destination);
    o.start(t); o.stop(t + 0.025);
  }
  btn.addEventListener("click", () => {
    if (!settings.sound && !ensure()) return;
    settings.sound = !settings.sound;
    btn.setAttribute("aria-pressed", String(settings.sound));
    btn.textContent = settings.sound ? "key clicks on" : "key clicks off";
    if (settings.sound) click({ code: "KeyA" });
  });
  bus.on("key:down", click);
})();
