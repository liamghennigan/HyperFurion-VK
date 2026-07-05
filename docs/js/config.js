// ═══ CONFIG — the live config.toml, expanded ══════════════════════════════
// The page parses the same keys the daemon hot-reloads ([flow], [registers],
// [llm] apply at the next recording there; here they apply on every edit).
import { cfgEl, cfgStatus, synth } from "./env.js";
import { bus } from "./bus.js";

export const Config = (() => {
  const cfg = {
    mods: { ctrl: true, alt: true, shift: false, meta: false }, code: "KeyV",
    keyLabel: "control+alt+v", mode: "auto", holdMs: 280,
    lang: "en", interim: true, voiceId: "eve", voice: null, rate: 1, pitch: 1,
    // [flow] — the molten dictation knobs, all live on this page
    flowLive: true, stabilityMs: 1500, autoStopMs: 0,
    numbers: "auto", wakeWord: "vk",
    // [registers]
    regDefault: "prose",
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
    if (!cfgEl) return;              // live-config panel may be absent on a streamlined page
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

    // [flow] — molten dictation
    const liveKey = grab(/^\s*live\s*=\s*(true|false)/m);
    if (liveKey !== null) cfg.flowLive = liveKey === "true";
    const stab = grab(/^\s*stability_ms\s*=\s*(\d+)/m);
    if (stab !== null) cfg.stabilityMs = Math.min(8000, Math.max(200, +stab));
    const auto = grab(/^\s*auto_stop_ms\s*=\s*(\d+)/m);
    if (auto !== null) cfg.autoStopMs = +auto === 0 ? 0 : Math.min(8000, Math.max(400, +auto));
    const numbers = grab(/^\s*numbers\s*=\s*"([^"]*)"/m);
    if (numbers !== null) {
      if (["auto", "always", "off"].includes(numbers)) cfg.numbers = numbers;
      else errs.push("numbers");
    }
    const wake = grab(/^\s*wake_word\s*=\s*"([^"]*)"/m);
    if (wake !== null) {
      if (/^[a-z]{2,24}$/i.test(wake.trim())) cfg.wakeWord = wake.trim().toLowerCase();
      else errs.push("wake_word");
    }
    // [registers]
    const regDef = grab(/^\s*default\s*=\s*"([^"]*)"/m);
    if (regDef !== null) {
      if (["prose", "terminal", "verbatim"].includes(regDef)) cfg.regDefault = regDef;
      else errs.push("default");
    }

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
        " · flow " + (cfg.flowLive ? "live" : "batch") + " · stability " + cfg.stabilityMs + " ms" +
        (cfg.autoStopMs ? " · auto-stop " + cfg.autoStopMs + " ms" : "") +
        " · wake \"" + cfg.wakeWord + "\" · numbers " + cfg.numbers +
        " · register " + cfg.regDefault +
        " · voice \"" + cfg.voiceId + "\" → " + voiceName;
    }
    // the demo re-renders on this event (registers, molten window, wake word)
    bus.emit("cfg:change", cfg);
  }
  if (cfgEl) {
    cfgEl.addEventListener("input", apply);
    if (synth && "onvoiceschanged" in synth) {
      synth.addEventListener("voiceschanged", () => { if (cfgStatus && !cfgStatus.hidden) apply(); }, { once: true });
    }
  }
  return { cfg, apply };
})();
