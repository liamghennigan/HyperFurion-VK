// ═══ STORY — scroll drives the board through six scenes ═══════════════════
// One number, 0..1 across the sticky span, is the source of truth. CSS
// interpolates the board's camera pose from it (scroll-driven animation
// where supported, a paused animation scrubbed by a negative delay where
// not, and a JS transform when neither moves); scene index toggles the
// states that cannot be interpolated. Nothing here hijacks scrolling.
import { $, story, board, reduced, coarse } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";

export const Story = (() => {
  const N = 6;
  const dots = $("dots");
  // [rotateX deg, rotateZ deg, translateX %, translateY %, scale] — mirrors @keyframes board-story
  const POSES = [
    [50, -7, 32, 24, .86], [60, -14, 4, 18, 1.02], [32, 0, 0, 34, .78],
    [50, -6, -45, 6, 1.45], [70, 0, 8, 26, .7], [50, 7, -30, 24, .86],
  ];
  let p = 0, idx = -1, raf = 0, mode = "css";
  const S = { progress: () => p, scene: () => ({ index: Math.max(0, idx), t: p * (N - 1) - Math.max(0, idx) }) };

  function measure() {
    if (!story) return 0;
    const r = story.getBoundingClientRect();
    const span = story.offsetHeight - innerHeight;
    return span > 0 ? Math.min(1, Math.max(0, -r.top / span)) : 0;
  }
  function pose(t) {
    const narrow = coarse || innerWidth < 760;
    const i = Math.min(N - 2, Math.floor(t)), f = t - i;
    const a = POSES[i], b = POSES[i + 1];
    const v = a.map((x, k) => x + (b[k] - x) * f);
    if (narrow) { v[0] = Math.min(v[0], 28); v[2] = 0; v[4] = Math.min(v[4], 1.3); }
    return `translate3d(${v[2]}%, ${v[3]}%, 0) rotateX(${v[0]}deg) rotateZ(${v[1]}deg) scale(${v[4]})`;
  }
  function update() {
    raf = 0;
    p = measure();
    story.style.setProperty("--story-p", p.toFixed(4));
    if (mode === "js" && board) board.style.transform = pose(p * (N - 1));
    const i = Math.round(p * (N - 1));
    if (i !== idx) {
      idx = i;
      state.scene = i;
      document.body.dataset.scene = String(i);
      if (dots) for (const d of dots.children) d.setAttribute("aria-current", d.dataset.scene == i ? "true" : "false");
      bus.emit("story:scene", { index: i });
    }
    bus.emit("story:progress", { p, index: i, t: p * (N - 1) - i });
  }
  function schedule() { if (!raf) raf = requestAnimationFrame(update); }
  function goto(i) {
    if (!story) return;
    const top = story.getBoundingClientRect().top + scrollY + i * innerHeight;
    scrollTo({ top, behavior: reduced ? "auto" : "smooth" });
  }

  // which path moves the board? decided once, after first layout
  function probe() {
    if (reduced || !board) { mode = "static"; return; }
    if (CSS.supports && CSS.supports("animation-timeline: scroll()")) { mode = "css"; return; }
    try {
      const anims = board.getAnimations ? board.getAnimations() : [];
      if (!anims.length) { mode = "js"; return; }
      story.style.setProperty("--story-p", "0.5");
      const t0 = anims[0].currentTime;
      story.style.setProperty("--story-p", "0.25");
      const t1 = board.getAnimations()[0].currentTime;
      mode = t0 !== t1 ? "scrub" : "js";
    } catch { mode = "js"; }
    if (mode === "js") board.classList.add("js-pose");
  }

  if (story) {
    probe();
    update();
    addEventListener("scroll", schedule, { passive: true });
    addEventListener("resize", schedule);
    if (dots) {
      dots.replaceChildren();
      for (let i = 0; i < N; i++) {
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.scene = i;
        b.setAttribute("aria-label", "scene " + (i + 1));
        b.addEventListener("click", () => goto(i));
        dots.appendChild(b);
      }
    }
  }
  S.goto = goto; S.mode = () => mode; S.update = update;
  return S;
})();
