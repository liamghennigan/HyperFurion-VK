// ═══ MEASURE — the colophon: real size, measured, not claimed ═════════════
// Counts the page's own files and bytes, and every request that left the
// origin. The only way that number is not zero is the relay sheet — and
// then the footer says so, out loud.
import { $, reduced } from "./env.js";
import { bus } from "./bus.js";

(() => {
  const bytesEl = $("bytes");
  if (!bytesEl) return;
  let optIn = 0, htmlBytes = 0;
  try {
    const nav = performance.getEntriesByType("navigation")[0];
    htmlBytes = (nav && (nav.transferSize || nav.decodedBodySize)) ||
      ("<!doctype html>" + document.documentElement.outerHTML).length;
  } catch {}
  function measure() {
    let own = 0, ownBytes = 0, foreign = 0;
    try {
      for (const r of performance.getEntriesByType("resource")) {
        try {
          if (new URL(r.name).origin === location.origin) { own++; ownBytes += r.transferSize || r.decodedBodySize || 0; }
          else foreign++;
        } catch {}
      }
    } catch {}
    return { files: own + 1, kb: Math.round((htmlBytes + ownBytes) / 1024), foreign: Math.max(foreign, optIn) };
  }
  function paint(m, kb) {
    bytesEl.textContent = m.files + " files · " + kb + " KB · all self-hosted · " +
      (m.foreign ? m.foreign + " opt-in request" + (m.foreign > 1 ? "s" : "") + " (you asked)" : "zero third-party requests") +
      " · zero analytics";
  }
  bus.on("relay:request", () => { optIn++; const m = measure(); paint(m, m.kb); });
  if (reduced || !("IntersectionObserver" in window)) { const m = measure(); paint(m, m.kb); return; }
  const io = new IntersectionObserver((es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    io.disconnect();
    const m = measure(), t0 = performance.now();
    const step = (t) => {
      const k = Math.min(1, (t - t0) / 900);
      paint(m, Math.round(m.kb * k * (2 - k)));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
  io.observe(bytesEl);
})();
