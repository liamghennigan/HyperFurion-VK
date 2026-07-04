// ═══ AUTOPILOT — a scripted ghost user, honestly labeled ══════════════════
// Most visitors will not hand a landing page their microphone, and Firefox
// has no SpeechRecognition at all. So the demo can drive itself: a ghost
// presses the hotkey, dictates molten into the editor — mis-hears a word
// and lets it repair itself — says "scratch that" out loud, shows the
// terminal register turning spoken numbers into digits, and ends by having
// a sentence read aloud. Every second of it is labeled as scripted, and
// any real interaction (Esc, the mic, a key) takes over.
import { $, hint, synth, reduced, SR } from "./env.js";
import { bus } from "./bus.js";
import { Desktop } from "./desktop.js";
import { Dictation } from "./dictation.js";
import { TTS } from "./tts.js";

export const Autopilot = (() => {
  const watchBtn = $("watch");
  const A = { running: false };
  let timers = [], overlay = null;

  const SCRIPT = [
    { focus: "editor",
      say: { text: "fixed the race condition in the audio thread period",
             revise: { at: 3, wrong: "addition" } } },
    { focus: "editor",
      say: { text: "scratch that fixed the race in audio capture instead period" } },
    { focus: "terminal",
      say: { text: "twenty three tests green comma zero flaky" } },
  ];

  function ghostKbd() {
    overlay = document.createElement("div");
    overlay.className = "ghostkbd";
    overlay.setAttribute("aria-hidden", "true");
    overlay.innerHTML = "<span class='sigcap'>autopilot" +
      (SR ? "" : " — your browser has no speech engine") +
      " · scripted, nothing is listening · esc takes over</span>" +
      "<span class='keys'><kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>V</kbd></span>";
    document.body.appendChild(overlay);
  }
  function pressCombo() {
    if (!overlay) return;
    for (const k of overlay.querySelectorAll("kbd")) k.classList.add("down");
    timers.push(setTimeout(() => {
      if (overlay) for (const k of overlay.querySelectorAll("kbd")) k.classList.remove("down");
    }, 420));
  }
  function at(ms, fn) { timers.push(setTimeout(fn, reduced ? Math.min(ms, 120 * (timers.length + 1)) : ms)); }

  function start() {
    if (A.running || Dictation.recording) return;
    A.running = true;
    watchBtn.textContent = "■ stop watching";
    ghostKbd();
    const dur = reduced ? 400 : 4600;   // per step: press, dictate molten, settle
    SCRIPT.forEach((step, i) => {
      at(i * dur + 200, () => { Desktop.focus(step.focus); pressCombo(); });
      at(i * dur + (reduced ? 250 : 800), () => Dictation.simulate(step.say));
    });
    // the finale: the reverse lane reads the freshly typed line aloud
    at(SCRIPT.length * dur + 400, () => {
      const line = [...document.querySelectorAll("#ebody .eline span")]
        .find((s) => /audio capture/.test(s.textContent));
      if (synth && line && line.firstChild) {
        const r = document.createRange();
        r.selectNodeContents(line);
        const sel = getSelection();
        sel.removeAllRanges();
        sel.addRange(r);
        TTS.speakSelection();
      }
      stop("autopilot: done — your turn. the mic is up top.");
    });
    const el = document.createElement("span");
    hint.replaceChildren(el);
    el.outerHTML = "<span class='engine-live'>autopilot</span> — scripted demo, honestly labeled. " +
      "watch the amber words: molten, then repaired, then frozen. " +
      "press <kbd>Esc</kbd> or the mic to take over.";
  }
  function stop(msg) {
    if (!A.running) return;
    A.running = false;
    for (const t of timers) clearTimeout(t);
    timers = [];
    if (overlay) { overlay.remove(); overlay = null; }
    watchBtn.textContent = "▶ watch it work";
    if (msg) hint.textContent = msg;
  }

  watchBtn.hidden = false;
  watchBtn.addEventListener("click", () => (A.running ? stop("") : start()));
  addEventListener("keydown", (e) => { if (e.code === "Escape") stop(""); });
  bus.on("rec:start", () => stop(""));  // the real mic always wins

  A.start = start; A.stop = stop;
  return A;
})();
