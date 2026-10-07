// ═══ DICTATION — the product's forward lane, molten ════════════════════════
// Every path — the in-tab speech model (the default), the hosted xAI relay
// (opt-in, behind its sheet), your browser's own speech service (only when
// you pick it, after the in-tab model could not run), and the scripted
// demo — feeds the same engine (flow.js), the same way every provider
// feeds the daemon's. One engine lives for one recording; each utterance
// the recognizer closes is a final segment inside it. Words render molten,
// repair in place, freeze when the utterance closes, honor the spoken
// grammar and the focused window's register — and every keystroke lands
// on the board and in the window's text field. "spell that …" swaps a word
// on screen; a navigation command said on its own waits for the text to
// land, then presses its chord on the board and moves the caret, exactly
// in the daemon's order.
//
// Nothing happens on the visitor's behalf: when the in-tab model can't
// run, the recording stops, the page says why, and the visitor chooses
// what happens next. Nothing is typed that the visitor didn't say.
import { $, mic, micCap, stopBtn, favicon, reduced, SR, baseTitle, FAV_IDLE, FAV_REC, os } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { Ticker } from "./ticker.js";
import { Signal } from "./signal.js";
import { settings } from "./settings.js";
import { Demo } from "./demo-relay.js";
import { LocalSTT } from "./stt-local.js";
import { Window } from "./window.js";
import { Typist } from "./typist.js";
import { Keyboard } from "./keyboard.js";
import { moltenLine, compileScript, pageRewrite, pageRewriteKind, continuationState } from "./flow.js";
import { keymap, chordsFor, label as navLabel } from "./nav.js";
import { intentRequest, pageCommand } from "./intent.js";
import { readAsMeant } from "./heard.js";

export const Dictation = (() => {
  const D = { recording: false };
  let engine = "none";        // "local" | "relay" | "browser" — whatever the current recording uses
  let rec = null;             // the browser's SpeechRecognition, when the visitor chose it
  let browserChosen = false;  // the visitor picked the browser's speech service "for this visit" (this page load)
  let listening = false;      // audio is really being captured for recognition
  let settling = false;       // stopped; the recognizer is still finishing what was said
  let failed = false;         // this recording ended in a failure the page already explained
  let relay = null, relayT = 0, funneled = false;
  let local = null;           // the in-tab model session
  let sigP = null;            // the mic acquisition for the current recording
  let line = null;            // the engine for the current recording
  let rawFinal = "", rawInterim = "";
  let guard = false;          // focus changed mid-dictation: typing is frozen
  let busy = false;           // a chord or a rewrite is landing: hold the typing
  let tickT = 0, autoStopT = 0;
  let script = null;          // the scripted session in progress {cancel}
  let scripted = false;
  let sessionCommits = 0;      // utterances closed since the mic was tapped
  // where the last recording left the caret — [flow] rejoin: a recording
  // that starts within 30 s, in the same app and the same prose register,
  // continues that text (a space, a capital only after a sentence end)
  let landing = null;         // { register, tail, when }
  const REJOIN_MS = 30000;
  let seen = { scratches: 0, corrections: 0, pauses: 0, formats: 0 };  // for the hints strip and the status line
  Object.defineProperty(D, "engine", { get: () => engine });

  const liveFlow = () => settings.flowLive && settings.interim;
  const wait = (ms) => new Promise((r) => setTimeout(r, reduced ? 0 : ms));   // an animation beat
  const hold = (ms) => new Promise((r) => setTimeout(r, ms));                  // a speaker's pause, motion or not
  const log = (text, cls) => Window.log(text, cls);

  // ── whose speech service: named plainly, with where the audio goes ──────
  // Chrome's SpeechRecognition streams audio to Google (on Android it may
  // stay on the device), desktop Edge's to Microsoft; Safari and every iOS
  // browser go through Apple's, which may run on Apple's servers. Anything
  // else is named generically.
  const vendor = (() => {
    const brands = ((navigator.userAgentData && navigator.userAgentData.brands) || []).map((b) => b.brand);
    const ua = navigator.userAgent || "";
    const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    const android = /Android/.test(ua);
    const generic = "your browser's speech service";
    if (ios) return { service: /CriOS|FxiOS|EdgiOS|OPiOS/.test(ua) ? generic : "Safari's speech service", goes: "may send your audio to Apple" };
    if (brands.includes("Microsoft Edge") && !android) return { service: "Edge's speech service", goes: "sends your audio to Microsoft" };
    if (brands.includes("Google Chrome")) return { service: "Chrome's speech service", goes: (android ? "may send" : "sends") + " your audio to Google" };
    if (/Safari\//.test(ua) && /Apple/.test(navigator.vendor || "") && !/Chrome\/|Chromium/.test(ua))
      return { service: "Safari's speech service", goes: "may send your audio to Apple" };
    return { service: generic, goes: "may send your audio to your browser maker's servers" };
  })();

  // the download's progress, held between files: the model's files arrive
  // one after another, and a moment between two of them is not a restart
  // (a GPU that gives up does restart it, for the CPU's weights: so does this)
  let lastPct = null, pctDevice = null;
  function loadPct() {
    if (LocalSTT.state !== "loading" || LocalSTT.device !== pctDevice) { lastPct = null; pctDevice = LocalSTT.device; }
    if (LocalSTT.state === "loading" && LocalSTT.pct != null) lastPct = LocalSTT.pct;
    return lastPct;
  }
  function engineLabel() {
    if (scripted) return ["scripted demo · nothing is listening", "sim"];
    if (engine === "local") {
      const pct = loadPct();
      if (LocalSTT.state === "ready")
        return ["Moonshine · in this tab · " + (LocalSTT.device === "webgpu" ? "on your GPU (WebGPU)" : "on your CPU (WebAssembly)"), "live"];
      if (LocalSTT.state === "loading")
        return [(pct != null ? "loading Moonshine into this tab · " + pct + "% downloaded" : "starting Moonshine in this tab") +
                (D.recording ? " · your words appear when it's ready" : settling ? " · what you said appears when it's ready" : ""), "live"];
      return ["", ""];
    }
    if (engine === "relay") return ["xAI via relay · opt-in", "live"];
    if (engine === "browser") return [vendor.service + " · " + vendor.goes + " · your choice for this visit", "live"];
    return ["", ""];
  }
  function caption() { Window.setEngine(...engineLabel()); capText(); }
  // the big caption says what is really happening to your voice right now
  function capText() {
    if (!micCap) return;
    let t = "Tap and speak";
    if (D.recording) {
      if (!listening) t = engine === "relay" ? "Connecting to the hosted engine…" : "Opening the microphone…";
      else if (engine === "local" && LocalSTT.state !== "ready") {
        const pct = loadPct();
        t = "Listening · model loading" + (pct != null ? " " + pct + "%" : "…");
      }
      else t = "Listening… tap to stop";
    } else if (settling) {
      const pct = loadPct();
      t = LocalSTT.state === "loading"
        ? "Transcribing once the model loads" + (pct != null ? " · " + pct + "%" : "…")
        : "Transcribing…";
    }
    micCap.textContent = t;
  }
  bus.on("stt:progress", () => { if (engine === "local") caption(); });

  function setRecording(on) {
    D.recording = on;
    state.recording = on;   // the ticker's idle-park check reads this mirror
    if (!on) listening = false;
    stopBtn.hidden = !on;
    mic.classList.toggle("live", on);
    mic.setAttribute("aria-pressed", String(on));
    document.title = on ? "● recording — " + baseTitle : baseTitle;
    favicon.href = on ? FAV_REC : FAV_IDLE;
    document.body.classList.toggle("recording", on);
    capText();
  }

  // ── when a speech engine can't run: say why, and let the visitor choose ─
  // Nothing starts on its own. The browser's speech service is offered by
  // name, with where it sends the audio, and starts only on that click.
  const Choice = (() => {
    const stage = $("stage");
    if (!stage) return { show() {}, hide() {} };
    const el = document.createElement("div");
    el.id = "stt-choice"; el.className = "stt-choice";
    el.style.cssText = "display:none;gap:12px;padding:14px 16px;border:1px solid var(--glass-line-2);border-radius:12px;background:var(--glass)";
    const msg = document.createElement("p");
    msg.id = "stt-choice-msg"; msg.setAttribute("role", "alert");
    msg.style.cssText = "margin:0;font:14px/1.5 var(--sans);color:var(--sand-100)";
    const row = document.createElement("div");
    row.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;align-items:center";
    const btn = (cls, text, fn) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = cls; b.textContent = text;
      b.style.cssText = "white-space:normal;text-align:left;line-height:1.4";
      b.addEventListener("click", fn);
      return b;
    };
    // the choice lasts until the page is reloaded: the button says so, and
    // the status line names the service on every recording it serves
    const useBrowser = btn("sbtn", "Use " + vendor.service + " for this visit — " + vendor.goes, () => {
      browserChosen = true; hide(); start();
    });
    const useLocal = btn("sbtn", "Use the speech model in this tab instead", () => {
      Demo.want = false; browserChosen = false;
      const arm = $("relay-arm"); if (arm) arm.checked = false;
      hide(); start();
    });
    const watch = btn("sbtn", "Watch the scripted demo", () => {
      hide();
      const a = $("autopilot"); if (a && a.getAttribute("aria-pressed") !== "true") a.click();
    });
    const dismiss = btn("textbtn", "dismiss", () => hide());
    row.append(useBrowser, useLocal, watch, dismiss);
    const det = document.createElement("details");
    det.style.cssText = "font:12px/1.5 var(--mono);color:var(--sand-500)";
    const sum = document.createElement("summary"); sum.textContent = "technical detail"; sum.style.cursor = "pointer";
    const code = document.createElement("code"); code.style.cssText = "display:block;white-space:pre-wrap;word-break:break-word;margin-top:6px";
    det.append(sum, code);
    el.append(msg, row, det);
    const hints = $("hints");
    if (hints && hints.parentNode === stage) stage.insertBefore(el, hints); else stage.appendChild(el);

    function show({ text, detail = "", browser = false, local: offerLocal = false }) {
      useBrowser.hidden = !(browser && SR);
      useLocal.hidden = !offerLocal;
      code.textContent = detail; det.hidden = !detail; det.open = false;
      el.style.display = "grid";
      msg.textContent = text;
    }
    function hide() { el.style.display = "none"; }
    return { show, hide };
  })();

  // ── one render pipe: raw transcript -> engine -> the focused window ─────
  // live engines re-recognize or revise the whole utterance, so words stay
  // molten until the utterance closes (a pause) and freeze then — never on
  // a clock that a slow pass could satisfy by accident. The scripted demo
  // keeps the daemon's 1.5 s window so its molten→frozen beat shows.
  function rejoinState() {
    const reg = Window.register();
    if (!landing || !settings.rejoin || !reg.smartCaps) return null;
    if (landing.register !== Window.focusedName() || Date.now() - landing.when > REJOIN_MS) return null;
    return continuationState(landing.tail, reg);
  }
  function newEngine(live = false) {
    const state0 = rejoinState();
    line = moltenLine({ register: Window.register(), cfg: live ? { ...settings, stabilityMs: Infinity } : settings, state: state0 });
    rawFinal = ""; rawInterim = ""; guard = false; busy = false;
    seen = { scratches: 0, corrections: 0, pauses: 0, formats: 0 };
    state.lastError = "";
    if (state0) log("continuing the last dictation · a space first" + (state0.capNext ? ", then a capital" : ""), "dim");
  }
  // the hints strip lights the command that just worked
  const digits = (s) => (String(s || "").match(/\d/g) || []).length;
  function noticed(r) {
    if (!r) return;
    if (r.scratches > seen.scratches) { seen.scratches = r.scratches; bus.emit("flow:did", { kind: "scratch" }); }
    if (r.corrections && r.corrections.length > seen.corrections) {
      // "correct X to Y" and "spell that …" both fix a word: the later of
      // the two commands in the transcript is the one that just acted
      seen.corrections = r.corrections.length;
      const t = raw().toLowerCase();
      bus.emit("flow:did", { kind: t.lastIndexOf("correct ") > t.lastIndexOf("spell that") ? "correct" : "spell" });
    }
    // spoken numbers became digits ("twenty five percent" -> "25%") — in
    // prose and the terminal, where that is formatting; python, shell and
    // javascript compile code ("range ten" -> range(10)), which isn't
    const formats = Math.max(0, digits(r.frozen) + digits(r.molten) - digits(raw()));
    if (formats > seen.formats && ["prose", "terminal"].includes(Window.focusedName())) { seen.formats = formats; bus.emit("flow:did", { kind: "format" }); }
    if (r.pauseLog && r.pauseLog.length > seen.pauses) {
      // a period the recognizer put at a pause, revised by the next words
      const [before, after] = r.pauseLog[r.pauseLog.length - 1];
      seen.pauses = r.pauseLog.length;
      log("⌁ the pause decided: “" + before + "” → “" + after + "”", "dim");
      bus.emit("flow:did", { kind: "pause" });
    }
  }
  // the instruction lanes the daemon routes by verb: "run …" is the
  // [intent] channel (ONE command line, Enter refused); anything else is a
  // rewrite of the just-typed text
  function instruct(instr) {
    const request = settings.intent && settings.intent.enabled ? intentRequest(instr, settings.intent.verbs) : null;
    if (request !== null) { bus.emit("flow:did", { kind: "intent" }); return draftCommand(request); }
    return rewriteInPlace(instr);
  }
  const raw = () => (rawFinal + " " + rawInterim).trim();
  function paint(r) {
    if (guard || busy || !r) return;             // the daemon never types into the wrong window
    Typist.setTarget(r);
    if (r.action) act(r.action);
  }
  function pump() {
    if (!line) return;
    if (!liveFlow()) {                            // flow.live = false → the old behavior
      Typist.setTarget({ frozen: rawFinal, molten: settings.interim ? rawInterim : "" });
      return;
    }
    const r = line.update(raw(), performance.now());
    paint(r); noticed(r);
  }
  function tick() { if (line && liveFlow()) { const r = line.tick(performance.now()); paint(r); noticed(r); } }
  // a final closes an utterance: it becomes a final segment of the engine —
  // the stability window is satisfied, "scratch that" and "spell that" act,
  // a lone navigation command becomes a barrier, a wake-word instruction
  // that ends the utterance is taken
  function closeUtterance(text) {
    if (!line) return;
    rawFinal = (rawFinal + " " + (text || "")).trim(); rawInterim = "";
    sessionCommits++;
    state.lastRaw = (text || "").trim() || state.lastRaw;
    if (!liveFlow()) { pump(); return; }
    paint(line.update(raw(), performance.now(), { final: true }));
    noticed(line.result());
    const instr = line.takeInstruction();
    if (instr) instruct(instr);
    Window.panes();
  }
  function armAutoStop() {
    clearTimeout(autoStopT);
    const ms = settings.autoStopMs;
    if (ms > 0 && D.recording)
      autoStopT = setTimeout(() => { log("auto-stop: " + ms + " ms of silence", "dim"); stop(); }, ms);
  }
  // the in-tab model's usual mishearings of "VK" and "spell", read as
  // meant (heard.js) — only for that model, whose habits they are
  const asHeard = (text) => (engine === "local" ? readAsMeant(text, settings.wakeWord) : text);
  // every recognizer feeds these two
  function heardInterim(text) {
    rawInterim = asHeard(text);
    state.mark = performance.now();
    pump(); armAutoStop();
    bus.emit("rec:interim", { text });
  }
  function heardFinal(text) {
    state.mark = performance.now();
    bus.emit("rec:final", { text });
    closeUtterance(asHeard(text));
    armAutoStop();
  }

  // ── navigation: the text lands, then the chord, then the caret moves ────
  // The engine holds everything after the command until completeAction;
  // the Typist drains first so keys never fire mid-word (the daemon's
  // worker.drain before press_combo). Returns whether the keys were pressed.
  async function pressAction(action) {
    const reg = Window.register();
    await Typist.settled();
    if (guard) { log("focus moved · " + navLabel(action.action, action.count) + " not pressed", "dim"); return false; }
    // the visitor's own platform's keys: the daemon on a Mac presses
    // option+arrows and command+arrows, the Linux daemon ctrl+arrows
    const chords = chordsFor(action.action, action.count, keymap({ terminal: !!reg.terminal, platform: os }));
    if (!chords) {
      log("can't " + action.action.split(":")[0] + " that here · " + (reg.terminal ? "a terminal has no selection" : "no binding in this app"), "err");
      return false;
    }
    const buf = Window.buffer();
    for (const chord of chords) {
      await Keyboard.chord(chord);
      buf.press(chord);
      Window.moved();
    }
    log("⌁ " + navLabel(action.action, action.count) + " · " + chords.map((c) => c.join("+")).join(" "), "nav");
    bus.emit("flow:did", { kind: action.action.split(":")[0] === "press" ? "nav" : action.action.split(":")[0] });
    return true;
  }
  async function act(action) {
    if (busy || !line) return;
    busy = true;
    const l = line;
    const pressed = await pressAction(action);
    // the barrier is completed on the engine that raised it, even when
    // the recording ended meanwhile — stop() waits on `busy` and must not
    // find the same command still pending and press it twice
    if (pressed) Typist.release();              // the caret moved: the text behind it is not ours
    l.completeAction(performance.now(), { pressed });
    busy = false;
    if (l === line) Typist.setTarget(l.peek());
  }

  // ── the human's own keys ────────────────────────────────────────────────
  // The one thing the keyboard never does: pressing Enter. In the scripted
  // demo the human is scripted too, and says so. The key rings in the
  // visitor's color, the field gets its line break, and the engine lets go
  // of the text above — the caret is somewhere new now.
  async function humanPress(key) {
    if (key !== "enter") return;
    busy = true;
    await Typist.settled();
    Keyboard.press("Enter", { heat: "user" });
    Window.buffer().insert("\n");
    Typist.release();
    if (line) line.detach();
    Window.moved();
    log("⏎ pressed — by the demo, as you would", "consent");
    busy = false;
  }

  // ── "VK, run …": one command line at the caret, and Enter is yours ──────
  // The daemon sends the request to your [llm] and types its one line with
  // Enter refused in the injector. The page's stand-in answers a table
  // (intent.js) and types the line the same way; the board's Enter key can
  // only ring. In a terminal that is a drafted command at the prompt.
  async function draftCommand(request) {
    const l = line;
    const { command, known } = pageCommand(request);
    busy = true;
    await Typist.settled();
    await wait(300);
    const before = Typist.shown() ? l.committed() : "";  // nothing of ours at the caret: a fresh line
    const text = before + (before && !/\s$/.test(before) ? " " : "") + command;
    l.rewrite(text);
    Typist.setTarget({ frozen: text, molten: "" });
    await Typist.settled();
    Keyboard.press("Enter", { heat: "nav" });   // lit, never pressed: the refusal, on screen
    log("⌁ typed — Enter is yours" + (known ? "" : " · the page has no model: the daemon asks your [llm]"), "consent");
    busy = false;
    if (l === line) Typist.setTarget(l.peek());
  }

  // ── the wake word: rewrite the just-typed text in place ─────────────────
  // The daemon sends it to your [llm]; the page applies a small
  // deterministic rewrite instead, labeled as such. Nothing leaves. The
  // stand-in knows three instructions (formal, upper case, title case);
  // any other leaves your words as they are, and the status line says so.
  async function rewriteInPlace(instr) {
    const l = line;
    const text = l.committed();
    if (!text) { log("nothing typed yet to rewrite · dictate first, then say “" + settings.wakeWord.toUpperCase() + ", …”", "dim"); return; }
    const asked = instr.trim().replace(/[.,!?;:]+$/, "");
    const said = "“" + settings.wakeWord + (asked ? ", " + asked : "") + "”";
    if (!asked) { log(said + " with no instruction after it · nothing changed", "dim"); return; }
    if (!pageRewriteKind(instr)) {
      log(said + " · nothing changed: this page's stand-in knows only formal, upper case and title case " +
        "(the app asks your language model)", "dim");
      return;
    }
    bus.emit("flow:did", { kind: "rewrite" });
    const rewritten = /^\s*/.exec(text)[0] + pageRewrite(text, instr);   // the space that joined it to earlier text stays
    busy = true;
    await Typist.settled();
    await wait(420);
    if (l !== line) { busy = false; return; }   // the recording is gone: nothing of it to rewrite
    Typist.setTarget({ frozen: "", molten: rewritten, repair: true });
    log(said + " · rewritten in place (page stand-in for your LLM)", "dim");
    await Typist.settled();
    await wait(320);
    l.rewrite(rewritten);
    Typist.setTarget({ frozen: rewritten, molten: "" });
    busy = false;
    if (l === line) Typist.setTarget(l.peek());
  }

  // ── a recording ─────────────────────────────────────────────────────────
  // The in-tab model unless the visitor opted into the relay, or picked
  // the browser's speech service after the model could not run here.
  function start() {
    if (D.recording) return;
    if (settling) { log("still transcribing the last recording", "dim"); return; }
    cancelScript();
    Choice.hide();
    scripted = false; failed = false;
    if (Demo.armed()) { begin("relay"); startRelay(); return; }
    if (browserChosen && SR) { begin("browser"); startBrowser(); return; }
    if (!LocalSTT.supported()) {
      Choice.show({
        text: "This browser can't run the speech model here: it lacks microphone capture, Web Audio or WebAssembly. Nothing was recorded.",
        browser: true,
      });
      return;
    }
    begin("local");
    startLocal();
  }
  function begin(kind) {
    engine = kind;
    sessionCommits = 0;
    newEngine(true);
    Typist.reset();
    setRecording(true);
    Window.setLatency(null);
    sigP = Signal.start();
    Ticker.wake();
    // the stability clock ticks even between provider updates
    tickT = setInterval(tick, 350);
    armAutoStop();
    caption();
    bus.emit("rec:start", { engine: kind });
  }

  // — the in-tab path: mic PCM → an open-source model, in this tab —
  async function startLocal() {
    const mine = sigP;
    try {
      await mine;
      if (!D.recording || sigP !== mine) { if (!D.recording) Signal.stop(); return; }
      const au = Signal.audio();
      if (!au.stream) { micFail(); return; }
      // the download starts once the mic is yours to use — not at the tap,
      // so a blocked or dismissed mic downloads nothing; capture starts now
      // too, so what you say while it loads is still heard. Failures land
      // in the session.
      if (LocalSTT.state !== "ready") LocalSTT.load().catch(() => {});
      const session = await LocalSTT.start(au.stream, { onInterim: heardInterim, onFinal: heardFinal, onError: localFail });
      if (!D.recording || sigP !== mine) { session.stop(); return; }
      local = session;
      listening = true;
      caption();
    } catch (err) {
      localFail(err);
    }
  }
  function localFail(err) {
    if (failed) return;
    failed = true;
    const detail = err && err.message ? err.message : String(err || "");
    state.lastError = "local model: " + detail;
    const landed = sessionCommits > 0 || !!raw();
    if (local) { const l = local; local = null; l.stop().catch(() => {}); }
    if (D.recording) halt();
    log("", "");
    Choice.show({
      text: (landed
        ? "The speech model stopped working: " + LocalSTT.explain(err) + ". What it typed stays; nothing after that was transcribed"
        : "The speech model couldn't start: " + LocalSTT.explain(err) + ". Nothing was transcribed") +
        ", and no audio left this tab.",
      detail, browser: true,
    });
  }
  function micFail() {
    failed = true;
    const why = Signal.error();
    state.lastError = "microphone: " + why;
    if (D.recording) halt();
    engine = "none";   // nothing is listening: no engine line, green or otherwise
    caption();
    Choice.show({
      text: why === "NotFoundError" ? "No microphone was found, so nothing was recorded."
        : why === "unsupported" ? "This browser doesn't give pages microphone access here, so nothing was recorded."
        : "Microphone access was blocked, so nothing was recorded. Allow the microphone for this page, then tap the mic again.",
      detail: why,
    });
  }

  // — the browser's own speech service: only after the visitor chose it —
  function startBrowser() {
    let fatal = "", restarts = 0;
    const r = rec = new SR();
    r.lang = settings.lang || "en-US";
    r.continuous = true;
    r.interimResults = true;
    r.onstart = () => { if (rec === r && D.recording) { listening = true; caption(); } };
    r.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        if (res.isFinal) heardFinal(res[0].transcript);
        else interim += res[0].transcript;
      }
      heardInterim(interim);
    };
    r.onerror = (e) => { if (e.error !== "no-speech" && e.error !== "aborted") fatal = e.error || "error"; };
    r.onend = () => {
      if (rec !== r || !D.recording) return;
      if (fatal) { browserFail(fatal); return; }
      // the service closes a session after a quiet spell; you haven't stopped
      if (restarts++ < 20) { try { r.start(); return; } catch {} }
      stop();
    };
    try { r.start(); } catch (e) { browserFail(e && e.name ? e.name : "start"); return; }
    // a service that never answers (a Chromium build without one, say)
    // is a failure to report, not a "listening" to keep up
    const mine = sigP;
    Promise.resolve(mine).then(() => setTimeout(() => {
      if (rec === r && D.recording && !listening) { try { r.abort(); } catch {} browserFail("timeout"); }
    }, 8000));
  }
  function browserFail(code) {
    if (failed) return;
    failed = true;
    state.lastError = "browser speech: " + code;
    const why = code === "network" ? "it couldn't reach its servers"
      : code === "not-allowed" || code === "service-not-allowed" ? "the browser didn't allow it (microphone or speech permission)"
      : code === "audio-capture" ? "no microphone was available to it"
      : code === "language-not-supported" ? "it doesn't support " + (settings.lang || "this language")
      : code === "timeout" ? "it never answered"
      : "it ended with an error";
    const started = listening;
    if (D.recording) halt();
    browserChosen = false;
    const name = vendor.service[0].toUpperCase() + vendor.service.slice(1);
    Choice.show({
      text: started ? name + " stopped: " + why + "." + (sessionCommits || raw() ? " What it typed stays." : "")
        : name + " didn't start: " + why + ". Nothing was transcribed.",
      detail: code, local: true,
    });
  }

  // — the hosted-relay dictation path: mic PCM → relay → xai grok stt —
  async function startRelay() {
    const mine = sigP;
    try {
      await mine;
      // The user may have hit stop while the mic permission prompt was up:
      // release the now-arrived stream and never open the socket.
      if (!D.recording || sigP !== mine) { if (!D.recording) Signal.stop(); return; }
      const au = Signal.audio();
      if (!au.stream || !au.ctx) { micFail(); return; }
      const rate = Math.round(au.ctx.sampleRate);
      const ws = new WebSocket(Demo.wsBase + "/v1/demo/stt?sample_rate=" + rate +
        "&encoding=pcm&interim_results=true&language=" + encodeURIComponent((settings.lang || "en").slice(0, 2)));
      ws.binaryType = "arraybuffer";
      const src = au.ctx.createMediaStreamSource(au.stream);
      const proc = au.ctx.createScriptProcessor(4096, 1, 1);
      const sink = au.ctx.createGain();
      sink.gain.value = 0;  // the processor needs a destination, not an echo
      relay = { ws, src, proc, sink, done: false };
      proc.onaudioprocess = (e) => {
        if (!relay || ws.readyState !== 1) return;
        const f = e.inputBuffer.getChannelData(0);
        const pcm = new Int16Array(f.length);
        for (let i = 0; i < f.length; i++) {
          const s = Math.max(-1, Math.min(1, f[i]));
          pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        ws.send(pcm.buffer);
      };
      ws.onopen = () => {
        src.connect(proc); proc.connect(sink); sink.connect(au.ctx.destination);
        if (D.recording) { listening = true; caption(); }
      };
      ws.onmessage = relayEvent;
      ws.onerror = () => relayFail("the connection failed");
      ws.onclose = () => {
        if (!relay) return;
        if (D.recording) relayFail("the connection closed");
        else relaySettle();  // audio.done sent; no more events are coming
      };
      caption();
    } catch (err) {
      relayFail(err && err.message ? err.message : "it couldn't start");
    }
  }
  function relayEvent(m) {
    if (!relay) return;
    let ev; try { ev = JSON.parse(m.data); } catch { return; }
    if (ev.type === "transcript.partial") {
      if (ev.is_final && ev.text) heardFinal(ev.text);
      else heardInterim(ev.text || "");
    } else if (ev.type === "transcript.done") {
      const t = String(ev.text || "");
      // the relay's closing transcript only matters if nothing was committed yet
      if (!sessionCommits && !raw() && t) { rawFinal = t; rawInterim = ""; }
      pump();
      relay.done = true;
      if (D.recording) stop();  // the demo cap finalized for us
      else relaySettle();
    } else if (ev.type === "demo.limit") {
      log(ev.message, "dim");
    } else if (ev.type === "error") {
      relayFail(ev.message || "it reported an error");
    }
  }
  function relayCleanup() {
    if (!relay) return;
    try { relay.proc.disconnect(); relay.src.disconnect(); relay.sink.disconnect(); } catch {}
    try { relay.ws.onclose = null; relay.ws.onmessage = null; relay.ws.close(); } catch {}
    relay = null;
  }
  function relayFinish() {
    try { relay.proc.disconnect(); } catch {}
    if (relay.done) { relaySettle(); return; }
    try { relay.ws.send(JSON.stringify({ type: "audio.done" })); } catch { relaySettle(); return; }
    relayT = setTimeout(relaySettle, 6000);  // deadline for the final transcript
  }
  function relaySettle() {
    if (!relay) return;
    clearTimeout(relayT);
    relayCleanup();
    finish().then((text) => { if (text) funnel(); });
  }
  function relayFail(msg) {
    // NB: relay may be null here — a failure before the socket/nodes were
    // assigned (mic denied, WebSocket ctor threw). Clean up only if built.
    if (relay) relayCleanup();
    clearTimeout(relayT);
    if (failed) return;
    state.lastError = "hosted engine: " + msg;
    if (!D.recording) { log("hosted engine: " + msg + " · its last words may be missing", "err"); finish(); return; }
    failed = true;
    halt();
    Choice.show({
      text: "The hosted engine stopped: " + msg + ". Nothing more was sent." + (sessionCommits || raw() ? " What it already typed stays." : ""),
      local: true,
    });
  }
  function funnel() {
    if (funneled) return;
    funneled = true;
    log("that came through xAI via the relay", "dim");
  }

  // ── the landing: finalize the engine, walk its barriers, commit ─────────
  // The daemon's stop path: finalize, and for every navigation command not
  // yet pressed, put its segment on screen, press its keys, move on.
  // Resolves to the text of the whole recording ("" when nothing landed).
  async function settleLine() {
    if (!line) return "";
    const l = line; line = null;
    if (!liveFlow()) {
      const text = raw();
      if (guard) return clipboardLanding(text);
      Typist.setTarget({ frozen: text, molten: "" });
      await Typist.settled();
      if (text) done(text);
      Typist.release();
      return text;
    }
    while (busy) await wait(50);               // a chord or rewrite mid-flight finishes first
    let r = l.finalize(raw(), performance.now());
    noticed(r);
    if (guard) { const t = (r.typedBefore + " " + r.text).trim(); return clipboardLanding(t); }
    while (r.action) {
      Typist.setTarget({ frozen: r.text, molten: "" });
      busy = true;
      const pressed = await pressAction(r.action);
      busy = false;
      if (pressed) Typist.release();
      r = l.completeAction(performance.now(), { pressed });
    }
    if (r.instruction && (r.text || intentRequest(r.instruction, (settings.intent || {}).verbs))) {
      busy = false;
      line = l; await instruct(r.instruction);
      if (line === l) line = null;   // a recording that began meanwhile keeps its engine
      r = { ...r, text: l.committed() };
    } else if (r.instruction) {
      log("nothing typed yet to rewrite · dictate first, then say “" + settings.wakeWord.toUpperCase() + ", …”", "dim");
    }
    Typist.setTarget({ frozen: r.text, molten: "" });
    await Typist.settled();
    Typist.release();
    Window.paintDoc();
    const whole = (r.typedBefore + (r.text[0] === " " || !r.typedBefore ? "" : " ") + r.text).trim();
    if (whole) done(whole, r.text);
    return whole;
  }
  function clipboardLanding(text) {
    // focus moved mid-dictation: typing stops, never into the wrong window.
    // The daemon then puts the whole transcript on your clipboard; this page
    // never writes your clipboard without asking, so it leaves it alone —
    // the transcript stays in the panes below ("you said: …")
    if (!text) return "";
    log("focus changed · typing stopped · this page leaves your clipboard alone (the app would put the transcript there)", "dim");
    state.dictations++;
    done(text);
    return text;
  }

  // the recording ends: mic off, timers off
  function teardown() {
    setRecording(false);
    clearInterval(tickT);
    clearTimeout(autoStopT);
    Signal.stop();
    if (rec) { try { rec.stop(); } catch {} rec = null; }
    state.dictations++;
    bus.emit("rec:stop", {});
  }
  // what landed lands; nothing is ever typed in place of what you said
  async function finish() {
    const text = await settleLine();
    settling = false;
    if (!text && !sessionCommits && !failed) log("Nothing was recognized in that recording.", "dim");
    caption();
    return text;
  }
  // a failure ends the recording at once
  function halt() {
    if (!D.recording) return;
    teardown();
    if (engine === "relay" && relay) relayCleanup();
    finish();
  }
  function stop() {
    if (!D.recording) return;
    teardown();
    if (engine === "relay") {
      if (relay) relayFinish(); else finish();
      return;
    }
    if (engine === "local") {
      const l = local; local = null;
      if (l) { settling = true; caption(); l.stop().then(finish, finish); } else finish();
      return;
    }
    // give the browser's final result a beat to arrive, then settle the line
    setTimeout(finish, 350);
  }

  // ── scripted playback: the autopilot and the hint chips ─────────────────
  // A scripted session replays interim snapshots through the same molten
  // engine a live session uses — deterministic, and shaped like the truth:
  // one recording, several utterances, a pause between them. With reduced
  // motion each utterance lands whole; the pauses between them stay.
  // script: { text, revise } for one utterance, or { utterances: [...] }.
  async function playScript(scr, opts = {}) {
    cancelScript();
    Choice.hide();
    const utterances = scr.utterances || [{ text: scr.text, revise: scr.revise || null }];
    const token = { live: true };
    script = { cancel: () => { token.live = false; } };
    newEngine(false);
    if (opts.raw) line = moltenLine({ register: { name: "verbatim", smartCaps: false, grammar: false }, cfg: settings });
    scripted = true;
    caption();
    Typist.reset();
    const stopped = () => !token.live;
    for (const u of utterances) {
      if (u.press) { await humanPress(u.press); await hold(u.pause || 700); if (stopped()) return; continue; }
      const compiled = compileScript(u.text, { revise: u.revise || null });
      if (!reduced && liveFlow()) {
        const t0 = performance.now();
        for (const step of compiled.steps) {
          await wait(Math.max(0, step.t - (performance.now() - t0)));
          if (stopped()) return;
          rawInterim = step.text; pump();
        }
        await wait(560);
        if (stopped()) return;
      }
      closeUtterance(compiled.final);
      while (busy && !stopped()) await hold(50);   // a chord lands before the next words
      await hold(u.pause || 900);
      if (stopped()) return;
    }
    const text = await settleLine();
    if (stopped()) return;
    state.dictations++;
    script = null;
    if (opts.onDone) opts.onDone(text);
  }
  function cancelScript() {
    if (script) { script.cancel(); script = null; }
    if (scripted && line) { line = null; Typist.reset(); }
  }

  // `atCaret` is the text the caret is actually after — after a trailing
  // caret command there is none, and the next recording rejoins nothing
  function done(text, atCaret = text) {
    state.ledger.push({ text, app: Window.focusedName(), when: Date.now() });
    if (state.ledger.length > 20) state.ledger.shift();
    landing = guard || !atCaret ? null : { register: Window.focusedName(), tail: atCaret.slice(-1), when: Date.now() };
    bus.emit("type:text", { text });
  }
  bus.on("doc:cleared", () => { landing = null; });

  D.start = start; D.stop = stop;
  D.toggle = () => (D.recording ? stop() : start());
  // scripted typing into the focused window — the autopilot uses this;
  // it ends in the same type:text event real dictation does
  D.simulate = (scr, opts) => { if (!D.recording && !settling) return playScript(scr, opts); return Promise.resolve(""); };
  D.busy = () => D.recording || settling || script !== null;
  D.cancelScript = cancelScript;
  // the focus guard: the window calls this when focus moves mid-dictation
  D.guard = () => {
    if (!D.recording || guard) return;
    guard = true;
    Typist.reset();
    log("focus changed · typing frozen", "dim");
  };

  bus.on("simulate", ({ script: s }) => D.simulate(s));
  bus.on("stt:slow", ({ ms }) => log("slow device: " + (ms / 1000).toFixed(1) + " s per pass · words land late here", "dim"));
  bus.on("focus:changed", () => D.guard());
  mic.addEventListener("click", () => D.toggle());
  stopBtn.addEventListener("click", () => stop());
  setRecording(false);
  return D;
})();
