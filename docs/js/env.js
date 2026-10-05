// ═══ ENV — shared DOM handles and capability flags ═══════════════════════
// Every instrument reads these; nothing here has behavior of its own.
export const $ = (id) => document.getElementById(id);
export const board = $("board"), stage = $("stage"), story = $("story");
export const fwin = $("fwin"), mic = $("mic"), micCap = $("mic-cap"), stopBtn = $("stop");
export const favicon = $("favicon");
export const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
export const coarse = matchMedia("(pointer: coarse)").matches;  // touch-first device
export const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
export const synth = window.speechSynthesis;
export const baseTitle = document.title;
export const FAV_IDLE = favicon.href;
export const FAV_REC = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ccircle cx='50' cy='50' r='34' fill='%23e0542e'/%3E%3C/svg%3E";
export const FAV_SPK = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ccircle cx='50' cy='50' r='34' fill='%23e9a23b'/%3E%3C/svg%3E";
export const webgl2 = (() => {
  try { return !!document.createElement("canvas").getContext("webgl2"); } catch { return false; }
})();
export function detectOS() {
  const p = ((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "").toLowerCase();
  const ua = navigator.userAgent.toLowerCase();
  if (/android|iphone|ipad|ipod/.test(ua) || (navigator.maxTouchPoints > 1 && /mac/.test(p) && !/macintosh/.test(ua))) return "mobile";
  if (p.includes("win")) return "windows";
  if (p.includes("mac")) return "mac";
  if (p.includes("linux") || /x11|cros/.test(ua)) return "linux";
  return "other";
}
export const os = detectOS();
document.documentElement.dataset.os = os;
document.documentElement.classList.add("js");
