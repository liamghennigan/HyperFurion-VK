// ═══ ENV — shared DOM handles and capability flags ═══════════════════════
// Every instrument reads these; nothing here has behavior of its own.
export const $ = (id) => document.getElementById(id);
export const desktop = $("desktop"), tbody = $("tbody"), pill = $("pill"), mic = $("mic"), hint = $("hint");
export const stopBtn = $("stopbtn");
export const cfgEl = $("config"), cfgStatus = $("cfgstatus");
export const scope = $("scope"), scopeLabel = $("scope-label");
export const heroCanvas = $("hero-canvas"), heroCap = $("hero-cap");
export const favicon = $("favicon");
export const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
export const coarse = matchMedia("(pointer: coarse)").matches;  // touch-first device
export const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
export const synth = window.speechSynthesis;
export const baseTitle = document.title;
export const FAV_IDLE = favicon.href;
export const FAV_REC = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ccircle cx='50' cy='50' r='34' fill='%23e5484d'/%3E%3C/svg%3E";
export const FAV_SPK = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='0.9em' font-size='90'%3E%F0%9F%94%8A%3C/text%3E%3C/svg%3E";

// reveal the interactive parts before any instrument measures its canvas
// (the page stays static without JS)
desktop.hidden = hint.hidden = false;
