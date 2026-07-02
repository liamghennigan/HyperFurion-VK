// ═══ CONFIG — the live config.toml, expanded ══════════════════════════════
import { cfgEl, cfgStatus, synth } from "./env.js";
import { bus } from "./bus.js";

export const Config = (() => {
  const cfg = {
    mods: { ctrl: true, alt: true, shift: false, meta: false }, code: "KeyV",
    keyLabel: "control+alt+v", mode: "auto", holdMs: 280,
    lang: "en", interim: true, voiceId: "eve", voice: null, rate: 1, pitch: 1,
  };
  const CODE_MAP = { space: "Space", enter: "Enter", return: "Enter", tab: "Tab" };
  function parseKeyCombo(str) {
    const parts = str.toLowerCase().split("+").map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return null;
    const mods = { ctrl: false, alt: false, shift: false, meta: false };
    let code = null;
    for (const p of parts) {
      if (p === "control" || p === "ctrl") mods.ctrl = true;
      else if (p === "alt") mods.alt = true;
      else if (p === "shift") mods.shift = true;
      else if (p === "super" || p === "meta") mods.meta = true;
      else if (CODE_MAP[p]) code = CODE_MAP[p];
      else if (/^[a-z]$/.test(p)) code = "Key" + p.toUpperCase();
      else if (/^[0-9]$/.test(p)) code = "Digit" + p;
      else return null;
    }
    return code ? { mods, code } : null;
  }
  function resolveVoice() {
    if (!synth) return "no speech synthesis";
    const vs = synth.getVoices();
    if (!vs.length) return "voices pending…";
    const id = cfg.voiceId.toLowerCase();
    cfg.voice = vs.find((v) => v.name.toLowerCase() === id) ||
                vs.find((v) => v.name.toLowerCase().includes(id)) ||
                vs.find((v) => v.lang.toLowerCase().startsWith(id)) || null;
    return cfg.voice ? cfg.voice.name : "no match → browser default";
  }
  function apply() {
    const text = cfgEl.textContent;
    const grab = (re) => { const m = text.match(re); return m ? m[1] : null; };
    const errs = [];

    const key = grab(/^\s*key\s*=\s*"([^"]*)"/m);
    if (key !== null) {
      const parsed = parseKeyCombo(key);
      if (parsed) { cfg.mods = parsed.mods; cfg.code = parsed.code; cfg.keyLabel = key; }
      else errs.push("key");
    }
    const mode = grab(/^\s*mode\s*=\s*"([^"]*)"/m);
    if (mode !== null) {
      if (["auto", "toggle", "hold"].includes(mode)) cfg.mode = mode;
      else errs.push("mode");
    }
    const hold = grab(/^\s*hold_threshold_ms\s*=\s*(\d+)/m);
    if (hold !== null) cfg.holdMs = Math.min(5000, Math.max(0, +hold));
    const lang = grab(/^\s*language\s*=\s*"([^"]*)"/m);
    if (lang !== null) {
      if (/^[a-z]{2,3}(-[A-Za-z]{2,4})?$/.test(lang)) cfg.lang = lang;
      else errs.push("language");
    }
    const interim = grab(/^\s*interim_results\s*=\s*(true|false)/m);
    if (interim !== null) cfg.interim = interim === "true";
    const vid = grab(/^\s*voice_id\s*=\s*"([^"]*)"/m);
    if (vid !== null && vid.trim()) cfg.voiceId = vid.trim();
    const rate = grab(/^\s*rate\s*=\s*([\d.]+)/m);
    if (rate !== null && !isNaN(+rate)) cfg.rate = Math.min(4, Math.max(.25, +rate));
    const pitch = grab(/^\s*pitch\s*=\s*([\d.]+)/m);
    if (pitch !== null && !isNaN(+pitch)) cfg.pitch = Math.min(2, Math.max(0, +pitch));

    const voiceName = resolveVoice();
    cfgStatus.hidden = false;
    if (errs.length) {
      cfgStatus.className = "cfgstatus err";
      cfgStatus.textContent = "✗ could not parse: " + errs.join(", ") + " — keeping last good value";
    } else {
      cfgStatus.className = "cfgstatus";
      cfgStatus.textContent = "✓ applied — hotkey " + cfg.keyLabel + " · mode " + cfg.mode +
        " · hold " + cfg.holdMs + " ms · lang " + cfg.lang +
        " · interim " + (cfg.interim ? "on" : "off") +
        " · voice \"" + cfg.voiceId + "\" → " + voiceName;
    }
    // the terminal re-renders on this event (it draws interim per cfg.interim)
    bus.emit("cfg:change", cfg);
  }
  cfgEl.addEventListener("input", apply);
  if (synth && "onvoiceschanged" in synth) {
    synth.addEventListener("voiceschanged", () => { if (!cfgStatus.hidden) apply(); }, { once: true });
  }
  return { cfg, apply };
})();
