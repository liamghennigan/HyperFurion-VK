// ═══ SETTINGS — the page's config.toml, as constants ══════════════════════
// The daemon reads these from ~/.config/voice-keyboard/config.toml; the
// page ships the defaults. Only the tts voice is resolved at runtime.
export const settings = {
  // [hotkey] control+alt+v, auto mode: tap toggles, hold talks
  code: "KeyV", mods: { ctrl: true, alt: true, shift: false, meta: false },
  mode: "auto", holdMs: 280,
  // [flow]
  flowLive: true, interim: true, stabilityMs: 1500, autoStopMs: 0,
  numbers: "auto", wakeWord: "vk",
  lang: (navigator.language || "en-US"),
  // [tts]
  tts: { rate: 1, pitch: 1, voice: null },
  sound: false,
};
