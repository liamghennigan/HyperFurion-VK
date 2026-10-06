// ═══ HINTS — what to say, and proof that it worked ════════════════════════
// A visitor with a microphone does not know the grammar. This strip under
// the mic names five things to say. While you dictate, the one you just
// said lights up the moment the engine acts on it — a scratch, a spelled
// fix, a caret command, a rewrite. While nothing is recording, tapping a
// hint plays that one thing as a short scripted session in the focused
// window, labeled scripted like everything else that is not your voice.
import { $, reduced } from "./env.js";
import { bus } from "./bus.js";
import { settings } from "./settings.js";
import { Dictation } from "./dictation.js";

export const Hints = (() => {
  const strip = $("hints");
  if (!strip) return {};
  const HINTS = [
    { kind: "scratch", say: "scratch that", play: [
      { text: "the fix lands on thursday", pause: 700 }, { text: "scratch that", pause: 700 }, { text: "the fix lands on friday period" }] },
    { kind: "spell", say: "spell that n g i n x", play: [
      { text: "the proxy in front is engine-x", pause: 900 }, { text: "spell that n g i n x" }] },
    { kind: "select", say: "select previous word", play: [
      { text: "ship it on friday", pause: 900 }, { text: "select previous word", pause: 1100 }, { text: "monday period" }] },
    { kind: "delete", say: "delete previous word", play: [
      { text: "rerun the flaky tests", pause: 900 }, { text: "delete previous word", pause: 1100 }, { text: "suite" }] },
    { kind: "rewrite", say: settings.wakeWord.toUpperCase() + ", make that formal", play: [
      { text: "i think it works now", pause: 900 }, { text: settings.wakeWord + " make that formal" }] },
  ];
  const chips = new Map();
  const lead = document.createElement("span");
  lead.className = "hints-lead"; lead.textContent = "try saying";
  strip.appendChild(lead);
  for (const h of HINTS) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = "“" + h.say + "”";
    b.title = "play this as a scripted demo";
    b.addEventListener("click", () => {
      if (Dictation.recording) return;
      bus.emit("simulate:hint", { kind: h.kind });
      Dictation.simulate({ utterances: h.play });
    });
    strip.appendChild(b);
    chips.set(h.kind, b);
  }
  const lit = new Map();
  function light(kind) {
    const b = chips.get(kind);
    if (!b) return;
    clearTimeout(lit.get(kind));
    b.classList.add("lit");
    lit.set(kind, setTimeout(() => b.classList.remove("lit"), reduced ? 800 : 2400));
  }
  bus.on("flow:did", ({ kind }) => light(kind));
  bus.on("rec:start", () => strip.classList.add("live"));
  bus.on("rec:stop", () => strip.classList.remove("live"));
  return { light };
})();
