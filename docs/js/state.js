// ═══ STATE — tiny shared mutable state ════════════════════════════════════
// Exists to break import cycles: the ticker parks on idle unless a recording
// is running, and the hints tour keys off the dictation count — but both of
// those instruments are imported *by* dictation.js.
export const state = {
  recording: false,   // mirrors Dictation.recording (written by dictation.js)
  dictations: 0,
  focusedApp: "",     // which demo window has focus (written by desktop.js)
  lastError: "",      // the daemon's `status` reports this; so does the page's
  ledger: [],         // page-session dictation history — dies on reload, like
                      // the daemon's ledger would if you never opted in
};
