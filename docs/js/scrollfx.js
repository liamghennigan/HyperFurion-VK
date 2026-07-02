// ═══ SCROLLFX — the page develops as you move through it ══════════════════
// Two mechanisms, both inert under reduced motion and without JS:
//   1. reveal: sections develop as they enter the viewport (class added by
//      script only, so the no-JS document is complete and visible)
//   2. progress: every section in view gets --p, 0→1 as it crosses the
//      viewport; the CSS spends it on ghost-numeral parallax, waveform-rule
//      draw-in, and the hero's exhale. No scroll-jacking, no pinning — the
//      field is the continuity, content just scrolls.
import { reduced } from "./env.js";

if (!reduced && "IntersectionObserver" in window) {
  const ro = new IntersectionObserver((es) => {
    for (const e of es)
      if (e.isIntersecting) { e.target.classList.add("in"); ro.unobserve(e.target); }
  }, { rootMargin: "0px 0px -8% 0px" });
  for (const s of document.querySelectorAll("main > section:not(.demo), footer")) {
    if (s.getBoundingClientRect().top > innerHeight * .85) {
      s.classList.add("reveal");
      ro.observe(s);
    }
  }
}

if (!reduced) {
  const tracked = [...document.querySelectorAll("header.hero, main > section")];
  let queued = false;
  function update() {
    queued = false;
    const vh = innerHeight;
    for (const s of tracked) {
      const r = s.getBoundingClientRect();
      if (r.bottom < -80 || r.top > vh + 80) continue;   // out of view: leave --p be
      const p = Math.min(1, Math.max(0, (vh - r.top) / (vh + r.height)));
      s.style.setProperty("--p", p.toFixed(4));
    }
  }
  addEventListener("scroll", () => {
    if (!queued) { queued = true; requestAnimationFrame(update); }
  }, { passive: true });
  addEventListener("resize", () => {
    if (!queued) { queued = true; requestAnimationFrame(update); }
  });
  update();
}
