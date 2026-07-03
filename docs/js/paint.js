// ═══ shared painting helpers ══════════════════════════════════════════════
// envelope renderer (the scope's frozen waveform)
export function paintEnvelope(ctx2, w, h, values, alpha, color) {
  const step = 3, n = Math.floor(w / step), mid = h / 2;
  ctx2.fillStyle = color;
  ctx2.globalAlpha = alpha;
  for (let i = 0; i < n; i++) {
    const v = values[Math.floor(i * values.length / n)] || 0;
    const bh = Math.max(1.5, v * (h - 8));
    ctx2.fillRect(i * step, mid - bh / 2, 2, bh);
  }
  ctx2.globalAlpha = 1;
}
export function waveColor() {
  return getComputedStyle(document.documentElement).getPropertyValue("--wave").trim() || "#22d3ee";
}
