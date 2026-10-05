// ═══ BUS — the only wire between instruments ═════════════════════════════
export const bus = (() => {
  const m = new Map();
  return {
    on: (t, f) => { (m.get(t) || m.set(t, []).get(t)).push(f); },
    // one failing listener never silences the rest
    emit: (t, d) => { for (const f of m.get(t) || []) { try { f(d); } catch (e) { console.error("bus:" + t, e); } } },
  };
})();
