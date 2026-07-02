// ═══ TICKER — one rAF loop owns every animation ═══════════════════════════
import { reduced } from "./env.js";
import { state } from "./state.js";

export const Ticker = (() => {
  const subs = new Set();
  const seen = new Map();  // el -> intersecting?
  const io = "IntersectionObserver" in window
    ? new IntersectionObserver((es) => { for (const e of es) seen.set(e.target, e.isIntersecting); })
    : null;
  let rafId = 0, last = 0, parked = false, idleT = 0;
  window.__vk = { frames: 0 };

  function loop(t) {
    rafId = 0;
    if (parked || reduced) return;
    window.__vk.frames++;
    const dt = Math.min(.1, (t - last) / 1000 || 0);
    last = t;
    if (!document.hidden) {
      for (const s of subs) {
        if (s.el && seen.get(s.el) === false) continue;
        s.acc = (s.acc || 0) + dt;
        if (s.fps && s.acc < 1 / s.fps) continue;
        s.fn(s.acc, t / 1000);
        s.acc = 0;
      }
    }
    rafId = requestAnimationFrame(loop);
  }
  function wake() {
    clearTimeout(idleT);
    idleT = setTimeout(() => { if (!state.recording) { parked = true; } }, 60000);
    if (parked) parked = false;
    if (!rafId && !reduced && subs.size) rafId = requestAnimationFrame(loop);
  }
  for (const ev of ["pointermove", "pointerdown", "keydown", "scroll", "touchstart"])
    addEventListener(ev, wake, { passive: true });
  document.addEventListener("visibilitychange", wake);
  return {
    add(sub) { subs.add(sub); if (io && sub.el) { io.observe(sub.el); seen.set(sub.el, true); } wake(); return sub; },
    wake,
  };
})();
