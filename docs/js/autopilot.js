// ═══ AUTOPILOT — a scripted ghost user, honestly labeled ══════════════════
// Most visitors will not hand a landing page their microphone, and Firefox
// has no SpeechRecognition at all. So the demo drives itself: a ghost taps
// the hotkey on the board, dictates molten into the editor — mis-hears a
// word and lets it repair itself — says "scratch that", switches to the
// terminal and python registers, has the wake word rewrite a line, and
// ends by reading it aloud. Every second is labeled scripted, and any real
// interaction (the mic, Esc, a tab, a chip) takes over.
import { $, synth, reduced, coarse } from "./env.js";
import { bus } from "./bus.js";
import { Window } from "./window.js";
import { Dictation } from "./dictation.js";
import { TTS } from "./tts.js";
import { Keyboard } from "./keyboard.js";
import { compileScript } from "./flow.js";

export const Autopilot = (() => {
  const btn = $("autopilot"), ghost = $("ghost"), ghostCap = $("ghost-cap");
  const A = { running: false, interacted: false };
  let timers = [], loopT = 0;
  // also run as the attract loop until someone interacts — on wide screens only;
  // on a phone the demo would hide the headline, so there it waits to be asked
  const ATTRACT = !coarse && innerWidth >= 900;

  const SCRIPT = [
    { register: "prose", say: { text: "fixed the race condition in the audio thread period", revise: { at: 3, wrong: "addition" } },
      cap: "mis-heard “addition” — watch it backspace, repair, then freeze" },
    { register: "prose", say: { text: "scratch that fixed the race in audio capture instead period" },
      cap: "“scratch that” is a command — the sentence comes back" },
    { register: "terminal", say: { text: "twenty three failed tests comma rerun the flaky ones" },
      cap: "terminal register: digits, no caps — and Enter stays yours", enter: true },
    { register: "python", say: { text: "for i in range ten colon" },
      cap: "python register compiles speech: for i in range(10):" },
    { register: "prose", say: { text: "we fixed a bunch of bugs and it works now VK, make that formal" },
      cap: "the wake word rewrites what you just said, in place (page stand-in for your [llm])", speak: true },
  ];

  const at = (ms, fn) => { timers.push(setTimeout(fn, reduced ? 0 : ms)); };
  function caption(text) { if (ghostCap) ghostCap.textContent = text; }
  function pressCombo() {
    Keyboard.press("ControlLeft", { heat: "user", hold: true });
    at(60, () => Keyboard.press("AltLeft", { heat: "user", hold: true }));
    at(140, () => Keyboard.press("KeyV", { heat: "user" }));
    at(420, () => { Keyboard.release("AltLeft"); Keyboard.release("ControlLeft"); });
  }

  function start() {
    if (A.running || Dictation.busy()) return;
    clearTimeout(loopT);
    A.running = true;
    document.body.classList.add("autopilot");
    if (btn) { btn.textContent = "stop the demo"; btn.setAttribute("aria-pressed", "true"); }
    if (ghost) ghost.hidden = false;
    let t = 300;
    SCRIPT.forEach((step) => {
      const dur = compileScript(step.say.text, { revise: step.say.revise || null }).dur;
      at(t, () => { Window.setRegister(step.register, { silent: true }); caption(step.cap); pressCombo(); });
      at(t + 520, () => Dictation.simulate(step.say));
      if (step.enter) at(t + 520 + dur + 900, () => { Keyboard.press("Enter"); caption("Enter refused — the drafted line waits for your hand"); });
      t += 520 + dur + (step.say.text.includes("VK,") ? 3400 : 2300) + (step.enter ? 900 : 0);
    });
    at(t, () => {
      // the finale: the reverse lane reads the freshly rewritten line aloud
      const lines = document.querySelectorAll("#fwin-lines .line");
      const last = lines[lines.length - 1];
      if (synth && last && last.firstChild) {
        const r = document.createRange();
        r.selectNodeContents(last);
        const sel = getSelection();
        sel.removeAllRanges(); sel.addRange(r);
        TTS.speakSelection();
        caption("…and read back aloud. your turn — tap the mic");
        at(2600, () => stop(true));
      } else stop(true);
    });
  }
  function stop(natural = false) {
    if (!A.running) return;
    A.running = false;
    for (const t of timers) clearTimeout(t);
    timers = [];
    if (!natural) Dictation.cancelScript();
    Keyboard.releaseAll();
    document.body.classList.remove("autopilot");
    if (ghost) ghost.hidden = true;
    if (btn) { btn.textContent = natural ? "replay the demo" : "play the scripted demo"; btn.setAttribute("aria-pressed", "false"); }
    if (natural && ATTRACT && !A.interacted && !reduced) loopT = setTimeout(() => { if (!document.hidden) start(); }, 14000);
  }
  function interact() { A.interacted = true; clearTimeout(loopT); if (A.running) stop(false); }

  if (btn) btn.addEventListener("click", () => { A.interacted = true; clearTimeout(loopT); A.running ? stop(false) : start(); });
  addEventListener("keydown", (e) => { if (e.code === "Escape") interact(); });
  bus.on("rec:start", interact);              // the real mic always wins
  bus.on("register:change", interact);
  bus.on("simulate", interact);
  // attract: start shortly after load unless motion is reduced or someone acted first
  if (ATTRACT && !reduced) loopT = setTimeout(() => { if (!A.interacted && !document.hidden) start(); }, 1800);
  document.addEventListener("visibilitychange", () => { if (document.hidden && A.running) stop(false); });

  A.start = start; A.stop = () => stop(false);
  return A;
})();
