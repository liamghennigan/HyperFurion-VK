// ═══ AUTOPILOT — a scripted demo, on request, honestly labeled ════════════
// Most visitors will not hand a landing page their microphone, and Firefox
// has no SpeechRecognition at all. So the demo can run itself, when asked:
// three recordings through the same molten engine, each one several
// utterances with a pause between — a mis-heard word that repairs itself,
// "scratch that", a word fixed by spelling it, a caret moved by voice and
// the text typed over the selection; then a shell command edited with
// readline's own keys; then the python register compiling speech. The
// window's engine badge says "scripted" the whole time, and any real
// interaction (the mic, Esc, a tab) stops it. It never starts on its own.
import { $, reduced } from "./env.js";
import { bus } from "./bus.js";
import { Window } from "./window.js";
import { Dictation } from "./dictation.js";

export const Autopilot = (() => {
  const btn = $("autopilot");
  const A = { running: false };
  let token = null;

  const SCRIPT = [
    { register: "prose", utterances: [
      { text: "fixed the race condition in the audio thread period", revise: { at: 3, wrong: "addition" } },
      { text: "scratch that", pause: 700 },
      { text: "fixed the race in audio capture instead period", pause: 1100 },
      { text: "the ingress now runs on engine-x", pause: 900 },
      { text: "spell that n g i n x", pause: 1300 },
      { text: "select previous word", pause: 1300 },
      { text: "caddy period", pause: 900 },
    ] },
    { register: "shell", utterances: [
      { text: "pytest dash x tests slash test underscore flow dot py", pause: 1200 },
      { text: "delete previous word", pause: 1300 },
      { text: "tests slash test underscore nav dot py", pause: 900 },
    ] },
    { register: "python", utterances: [
      { text: "for i in range ten colon", pause: 600 },
    ] },
  ];

  const wait = (ms) => new Promise((r) => setTimeout(r, reduced ? 0 : ms));

  async function start() {
    if (A.running || Dictation.busy()) return;
    A.running = true;
    const mine = token = {};
    document.body.classList.add("autopilot");
    if (btn) { btn.textContent = "stop the demo"; btn.setAttribute("aria-pressed", "true"); }
    await wait(200);
    for (const step of SCRIPT) {
      if (token !== mine) return;
      Window.setRegister(step.register, { silent: true });
      Window.clearDoc();
      await Dictation.simulate({ utterances: step.utterances });
      if (token !== mine) return;
      await wait(1600);
    }
    if (token === mine) stop(true);
  }
  function stop(natural = false) {
    if (!A.running) return;
    A.running = false;
    token = null;
    if (!natural) Dictation.cancelScript();
    document.body.classList.remove("autopilot");
    if (btn) { btn.textContent = natural ? "replay the demo" : "watch a scripted demo"; btn.setAttribute("aria-pressed", "false"); }
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
