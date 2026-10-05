// ═══ AUTOPILOT — a scripted demo, on request, honestly labeled ════════════
// Most visitors will not hand a landing page their microphone, and Firefox
// has no SpeechRecognition at all. So the demo can run itself, when asked:
// four dictations through the same molten engine — a mis-heard word that
// repairs itself, "scratch that", the terminal register, the python
// register. The window's engine badge says "scripted" the whole time, and
// any real interaction (the mic, Esc, a tab, a chip) stops it. It never
// starts on its own.
import { $, reduced } from "./env.js";
import { bus } from "./bus.js";
import { Window } from "./window.js";
import { Dictation } from "./dictation.js";
import { compileScript } from "./flow.js";

export const Autopilot = (() => {
  const btn = $("autopilot");
  const A = { running: false };
  let timers = [];

  const SCRIPT = [
    { register: "prose", say: { text: "fixed the race condition in the audio thread period", revise: { at: 3, wrong: "addition" } } },
    { register: "prose", say: { text: "scratch that fixed the race in audio capture instead period" } },
    { register: "terminal", say: { text: "twenty three failed tests comma rerun the flaky ones" } },
    { register: "python", say: { text: "for i in range ten colon" } },
  ];

  const at = (ms, fn) => { timers.push(setTimeout(fn, reduced ? 0 : ms)); };

  function start() {
    if (A.running || Dictation.busy()) return;
    A.running = true;
    document.body.classList.add("autopilot");
    if (btn) { btn.textContent = "stop the demo"; btn.setAttribute("aria-pressed", "true"); }
    let t = 200;
    SCRIPT.forEach((step) => {
      const dur = compileScript(step.say.text, { revise: step.say.revise || null }).dur;
      at(t, () => { Window.setRegister(step.register, { silent: true }); Dictation.simulate(step.say); });
      t += dur + 2400;
    });
    at(t, () => stop(true));
  }
  function stop(natural = false) {
    if (!A.running) return;
    A.running = false;
    for (const t of timers) clearTimeout(t);
    timers = [];
    if (!natural) Dictation.cancelScript();
    document.body.classList.remove("autopilot");
    if (btn) { btn.textContent = natural ? "replay the demo" : "play the scripted demo"; btn.setAttribute("aria-pressed", "false"); }
  }

  if (btn) btn.addEventListener("click", () => (A.running ? stop(false) : start()));
  addEventListener("keydown", (e) => { if (e.code === "Escape") stop(false); });
  bus.on("rec:start", () => stop(false));   // the real mic always wins
  bus.on("register:change", () => stop(false));
  bus.on("simulate", () => stop(false));
  document.addEventListener("visibilitychange", () => { if (document.hidden && A.running) stop(false); });

  A.start = start; A.stop = () => stop(false);
  return A;
})();
