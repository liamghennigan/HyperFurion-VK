// ═══ STATE — tiny shared mutable state ════════════════════════════════════
// Exists to break import cycles: the ticker parks on idle unless a recording
// is running, and several instruments are imported *by* dictation.js.
export const state = {
  recording: false,   // mirrors Dictation.recording
  dictations: 0,
  register: "prose",  // the focused window's register (written by window.js)
  scene: 0,           // story scene index (written by story.js)
  typedChars: 0,      // keystrokes the board has pressed this session
  lastRaw: "",        // raw transcript of the last committed utterance
  lastError: "",
  mark: 0,            // performance.now() of the latest speech result awaiting paint
  latency: [],        // speech result -> first keystroke paint, ms (last 8)
  ledger: [],         // page-session dictation history — dies on reload
};
