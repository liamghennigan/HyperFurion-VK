// ═══ HINTS — what to say, and proof that it worked ════════════════════════
// A visitor with a microphone does not know the grammar. This strip under
// the mic names seven things to say. While you dictate, the one you just
// said lights up the moment the engine acts on it — a scratch, a spelled
// fix, a caret command, a rewrite. While nothing is recording, tapping a
// hint plays that one thing as a short scripted session in the focused
// window, on an empty field (the scripted demo clears it the same way),
// under the window's "scripted demo" badge for its whole run.
// Each script is written the way a recognizer hands words over (a name it
// mishears is one wrong word; "to do" is two), so when a recognizer hears
// you say the same thing, the engine does the same thing.
import { $, reduced } from "./env.js";
import { bus } from "./bus.js";
import { settings } from "./settings.js";
import { Window } from "./window.js";
import { Dictation } from "./dictation.js";

export const Hints = (() => {
  const strip = $("hints");
  if (!strip) return {};
  const wake = settings.wakeWord;
  const HINTS = [
    { kind: "scratch", say: "scratch that", play: [
      { text: "the fix lands on thursday", pause: 700 }, { text: "scratch that", pause: 700 }, { text: "the fix lands on friday period" }] },
    // a recognizer hears a name it doesn't know as a word it does
    { kind: "spell", say: "spell that s i o b h a n",
      title: "This page's small speech model often hears “spell” as “bell” or “fill”; before “that” and spelled letters, the page reads those as “spell”.", play: [
      { text: "I'll send it to Shivon", pause: 900 }, { text: "spell that s i o b h a n" }] },
    { kind: "select", say: "select previous word", play: [
      { text: "ship it on friday", pause: 900 }, { text: "select previous word", pause: 1100 }, { text: "monday period" }] },
    { kind: "correct", say: "correct monday to friday", play: [
      { text: "the launch is on monday period", pause: 900 }, { text: "we ship at noon period", pause: 900 },
      { text: "correct monday to friday" }] },
    { kind: "format", say: "twenty five percent by october sixth", play: [
      { text: "steps colon new number ship twenty five percent by october sixth new number celebrate emoji rocket" }] },
    // the app sends the instruction to your language model; this page has
    // none, so its stand-in knows three — formal (a fixed list of word
    // swaps), upper case, title case — leaves your words alone for any
    // other, and the chip says so
    { kind: "rewrite", say: wake.toUpperCase() + ", make that formal", note: "page stand-in",
      title: "This page has no language model: its stand-in knows three rewrites — formal (a fixed list of word swaps), " +
        "upper case and title case — and changes nothing for any other. The app sends the instruction to your language model.", play: [
      { text: "i think it works now", pause: 900 }, { text: wake + " make that formal" }] },
    { kind: "intent", say: wake.toUpperCase() + ", run find every todo", register: "shell", play: [
      { text: wake + " run find every to do in this repo" }] },
  ];
  const chips = new Map();
  const lead = document.createElement("span");
  lead.className = "hints-lead"; lead.textContent = "try saying";
  strip.appendChild(lead);
  for (const h of HINTS) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = "“" + h.say + "”";
    if (h.note) {
      const n = document.createElement("span");
      n.textContent = " · " + h.note;
      n.style.opacity = ".75";
      b.appendChild(n);
    }
    b.title = (h.title ? h.title + " " : "") + "Tap to play it as a scripted demo.";
    b.addEventListener("click", async () => {
      if (Dictation.busy()) return;  // not over a recording, not over the scripted demo
      bus.emit("simulate:hint", { kind: h.kind });
      if (h.register) Window.setRegister(h.register, { silent: true });  // a command belongs at a prompt
      Window.clearDoc();   // each hint plays alone: not joined onto earlier text
      Window.scripted("hint", true);
      try { await Dictation.simulate({ utterances: h.play }); }
      finally { Window.scripted("hint", false); }
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
