// ═══ STATE — tiny shared mutable state ════════════════════════════════════
// Exists to break import cycles: the ticker parks on idle unless a recording
// is running, and the hints tour keys off the dictation count — but both of
// those instruments are imported *by* dictation.js.
export const state = {
  recording: false,   // mirrors Dictation.recording (written by dictation.js)
  dictations: 0,
};
