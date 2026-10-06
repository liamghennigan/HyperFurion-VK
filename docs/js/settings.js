// ═══ SETTINGS — the page's config.toml, as constants ══════════════════════
// The daemon reads these from ~/.config/voice-keyboard/config.toml; the
// page ships the defaults. Only the tts voice is resolved at runtime.
export const settings = {
  // [hotkey] control+alt+v, auto mode: tap toggles, hold talks
  code: "KeyV", mods: { ctrl: true, alt: true, shift: false, meta: false },
  mode: "auto", holdMs: 280,
  // [flow]
  flowLive: true, interim: true, stabilityMs: 1500, stabilityUpdates: 2, autoStopMs: 0,
  numbers: "auto", wakeWord: "vk",
  // [flow] spelling = true; [nav] enabled — off by default in the daemon,
  // on here so the page can show it
  spelling: true, nav: true,
  // [flow] rejoin = true: a recording started within 30 s continues the last
  rejoin: true, adaptive: true,
  // [flow] pause_review: the daemon's default "auto" is rules plus an [llm]
  // review of the unclear pauses; the page has no model, so: rules
  pauseReview: "rules",
  lang: (navigator.language || "en-US"),
  // [tts]
  tts: { rate: 1, pitch: 1, voice: null },
  sound: false,
};
