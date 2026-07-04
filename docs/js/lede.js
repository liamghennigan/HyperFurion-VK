// ═══ LEDE — the headline dictates itself ══════════════════════════════════
// One typographic performance, once, on load: the lede arrives the way the
// product types — molten amber, one word mis-heard and repaired in place,
// then frozen to ink. It ends as the exact static text the no-JS page has,
// and it never runs again. Under reduced motion it never runs at all.
import { $, reduced } from "./env.js";

const lede = $("lede");
if (lede && !reduced) {
  const FINAL = lede.textContent;          // "Speak. It types."
  lede.setAttribute("aria-label", FINAL);  // the performance is presentational
  const f = document.createElement("span");
  const m = document.createElement("span");
  m.className = "molten";
  f.setAttribute("aria-hidden", "true");
  m.setAttribute("aria-hidden", "true");
  const STEPS = [
    { t: 1150, f: "", m: "Speak." },
    { t: 1600, f: "Speak.", m: " It" },
    { t: 2050, f: "Speak.", m: " It tydes." },
    { t: 2700, f: "Speak. It", m: " types.", repair: true },
    { t: 3900, f: FINAL, m: "" },
  ];
  lede.replaceChildren(f, m);
  f.textContent = ""; m.textContent = " ";  // hold the line height
  let done = false;
  for (const s of STEPS) {
    setTimeout(() => {
      if (done) return;
      f.textContent = s.f;
      m.textContent = s.m || (s.f === FINAL ? "" : " ");
      m.classList.toggle("repair", !!s.repair);
      if (s.f === FINAL) { lede.textContent = FINAL; done = true; }
    }, s.t);
  }
}
