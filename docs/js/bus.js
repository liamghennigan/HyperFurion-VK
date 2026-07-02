// ═══ BUS — the only wire between instruments ═════════════════════════════
export const bus = (() => {
  const m = new Map();
  return {
    on: (t, f) => { (m.get(t) || m.set(t, []).get(t)).push(f); },
    emit: (t, d) => { for (const f of m.get(t) || []) f(d); },
  };
})();
