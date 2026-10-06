// ═══ DICTATION — the product's forward lane, molten ════════════════════════
// Every path — the in-tab speech model, your browser's speech engine, the
// hosted xAI relay, and the scripted demo — feeds the same engine
// (flow.js), the same way every provider feeds the daemon's. One engine
// lives for one recording; each utterance the recognizer closes is a
// final segment inside it. Words render molten, repair in place, freeze on
// the stability window, honor the spoken grammar and the focused window's
// register — and every keystroke lands on the board and in the window's
// text field. "spell that …" swaps a word on screen; a navigation command
// said on its own waits for the text to land, then presses its chord on
// the board and moves the caret, exactly in the daemon's order.
import { mic, micCap, stopBtn, favicon, reduced, SR, baseTitle, FAV_IDLE, FAV_REC, os } from "./env.js";
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
import { moltenLine, compileScript, pageRewrite, continuationState } from "./flow.js";
import { keymap, chordsFor, label as navLabel } from "./nav.js";

export const Dictation = (() => {
  const SIM_LINES = [
    { text: "dictated comma not typed em dash this sentence never touched a keyboard period" },
    { text: "note to self colon cancel the RSI appointment", revise: { at: 6, wrong: "RSVP" } },
    { text: "twenty three unread emails question mark later period" },
  ];
  const D = { recording: false };
  let engine = "none", rec = null, simIdx = 0;
  let relay = null, relayT = 0, funneled = false;
  let local = null;           // the in-tab model session
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
  let seen = { scratches: 0, corrections: 0 };  // for the hints strip
  Object.defineProperty(D, "engine", { get: () => engine });

  const liveFlow = () => settings.flowLive && settings.interim;
  const wait = (ms) => new Promise((r) => setTimeout(r, reduced ? 0 : ms));
  const log = (text, cls) => Window.log(text, cls);

  function engineLabel() {
    if (scripted) return ["scripted demo · nothing is listening", "sim"];
    if (engine === "local") return ["Moonshine · " + (LocalSTT.device === "webgpu" ? "WebGPU" : "WASM") + " · in this tab", "live"];
    if (engine === "loading") return ["loading Moonshine…", "live"];
    if (engine === "relay") return ["xAI via relay · opt-in", "live"];
    if (engine === "live") return ["browser speech engine", "live"];
    if (engine === "trying") return ["listening…", "live"];
    if (engine === "sim") return ["no speech engine · scripted stand-in", "sim"];
    return ["", ""];
  }
  function caption() { Window.setEngine(...engineLabel()); }

  function setRecording(on) {
    D.recording = on;
    state.recording = on;   // the ticker's idle-park check reads this mirror
    stopBtn.hidden = !on;
    mic.classList.toggle("live", on);
    mic.setAttribute("aria-pressed", String(on));
    document.title = on ? "● recording — " + baseTitle : baseTitle;
    favicon.href = on ? FAV_REC : FAV_IDLE;
    document.body.classList.toggle("recording", on);
    if (micCap) micCap.textContent = on ? "Listening… tap to stop" : "Tap and speak";
  }

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
    seen = { scratches: 0, corrections: 0 };
    state.lastError = "";
    if (state0) log("continuing the last dictation · a space first" + (state0.capNext ? ", then a capital" : ""), "dim");
  }
  // the hints strip lights the command that just worked
  function noticed(r) {
    if (!r) return;
    if (r.scratches > seen.scratches) { seen.scratches = r.scratches; bus.emit("flow:did", { kind: "scratch" }); }
    if (r.corrections && r.corrections.length > seen.corrections) { seen.corrections = r.corrections.length; bus.emit("flow:did", { kind: "spell" }); }
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
    paint(line.update(raw(), performance.now()));
  }
  function tick() { if (line && liveFlow()) paint(line.tick(performance.now())); }
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
    if (instr) { bus.emit("flow:did", { kind: "rewrite" }); rewriteInPlace(instr); }
    Window.panes();
  }
  function armAutoStop() {
    clearTimeout(autoStopT);
    const ms = settings.autoStopMs;
    if (ms > 0 && D.recording)
      autoStopT = setTimeout(() => { log("auto-stop: " + ms + " ms of silence", "dim"); stop(); }, ms);
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
    if (l !== line) { busy = false; return; }  // the recording ended; stop() walked the rest
    if (pressed) Typist.release();              // the caret moved: the text behind it is not ours
    l.completeAction(performance.now(), { pressed });
    busy = false;
    Typist.setTarget(l.peek());
  }

  // ── the wake word: rewrite the just-typed text in place ─────────────────
  // The daemon sends it to your [llm]; the page applies a small
  // deterministic rewrite instead, labeled as such. Nothing leaves.
  async function rewriteInPlace(instr) {
    const l = line;
    const text = l.committed();
    if (!text) { log("nothing typed yet to rewrite · dictate first, then the wake word", "dim"); return; }
    const rewritten = pageRewrite(text, instr);
    busy = true;
    await Typist.settled();
    await wait(420);
    if (l !== line && !l.pendingAction) { busy = false; return; }
    Typist.setTarget({ frozen: "", molten: rewritten, repair: true });
    log("“" + settings.wakeWord + ", " + instr + "” · rewritten in place (page stand-in for your LLM)", "dim");
    await Typist.settled();
    await wait(320);
    l.rewrite(rewritten);
    Typist.setTarget({ frozen: rewritten, molten: "" });
    busy = false;
    if (l === line) Typist.setTarget(l.peek());
  }

  function start() {
    if (D.recording) return;
    cancelScript();
    scripted = false;
    sessionCommits = 0;
    newEngine(true);
    Typist.reset();
    setRecording(true);
    Window.setLatency(null);
    const sigP = Signal.start();
    Ticker.wake();
    // the stability clock ticks even between provider updates
    tickT = setInterval(tick, 350);
    armAutoStop();
    if (Demo.armed()) {
      engine = "relay";
      caption();
      bus.emit("rec:start", { engine });
      startRelay(sigP);
      return;
    }
    if (LocalSTT.supported()) {
      engine = LocalSTT.state === "ready" ? "local" : "loading";
      caption();
      bus.emit("rec:start", { engine: "local" });
      startLocal(sigP);
      return;
    }
    bus.emit("rec:start", { engine });
    startBrowser();
    caption();
  }

  // — the in-tab path: mic PCM → an open-source model on your GPU —
  async function startLocal(sigP) {
    try {
      if (LocalSTT.state !== "ready") {
        log("fetching Moonshine, once…", "dim");
        await LocalSTT.load((loaded, total) => {
          log("loading Moonshine " + Math.round(loaded / 1e6) + " / " + Math.round(total / 1e6) + " MB", "dim");
        });
      }
      if (!D.recording) { Signal.stop(); return; }
      await sigP;
      if (!D.recording) { Signal.stop(); return; }
      const au = Signal.audio();
      if (!au.stream) throw new Error("microphone not granted");
      local = await LocalSTT.start(au.stream, {
        onInterim(text) {
          rawInterim = text;
          state.mark = performance.now();
          pump(); armAutoStop();
          bus.emit("rec:interim", { text });
        },
        onFinal(text) {
          state.mark = performance.now();
          bus.emit("rec:final", { text });
          closeUtterance(text);
          armAutoStop();
        },
      });
      engine = "local";
      log("", "");
      caption();
    } catch (err) {
      localFail(err && err.message ? err.message : "unavailable");
    }
  }
  function localFail(msg) {
    local = null;
    state.lastError = "local model: " + msg;
    log("local model failed: " + msg + " · using the browser engine", "err");
    if (D.recording) { engine = "none"; startBrowser(); caption(); }
  }
  function startBrowser() {
    if (SR) {
      try {
        rec = new SR();
        rec.lang = settings.lang || "en-US";
        rec.continuous = true;
        rec.interimResults = true;
        rec.onresult = (e) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const r = e.results[i];
            if (r.isFinal) { bus.emit("rec:final", { text: r[0].transcript }); closeUtterance(r[0].transcript); }
            else interim += r[0].transcript;
          }
          if (engine !== "live") { engine = "live"; caption(); }
          rawInterim = interim;
          state.mark = performance.now();
          pump();
          armAutoStop();
          bus.emit("rec:interim", { text: interim });
        };
        rec.onerror = () => { if (D.recording && engine !== "live") { engine = "sim"; caption(); } };
        rec.onend = () => { if (D.recording && engine !== "live") { engine = "sim"; caption(); } };
        rec.start();
        engine = "trying";
        return;
      } catch { /* fall through to sim */ }
    }
    engine = "sim";
  }

  // — the hosted-relay dictation path: mic PCM → relay → xai grok stt —
  async function startRelay(sigP) {
    try {
      await sigP;
      // The user may have hit stop while the mic permission prompt was up:
      // release the now-arrived stream and never open the socket.
      if (!D.recording) { Signal.stop(); return; }
      const au = Signal.audio();
      if (!au.stream || !au.ctx) throw new Error("microphone not granted");
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
      ws.onopen = () => { src.connect(proc); proc.connect(sink); sink.connect(au.ctx.destination); };
      ws.onmessage = relayEvent;
      ws.onerror = () => relayFail("connection failed");
      ws.onclose = () => {
        if (!relay) return;
        if (D.recording) relayFail("connection closed");
        else relaySettle();  // audio.done sent; no more events are coming
      };
      caption();
    } catch (err) {
      relayFail(err && err.message ? err.message : "unavailable");
    }
  }
  function relayEvent(m) {
    if (!relay) return;
    let ev; try { ev = JSON.parse(m.data); } catch { return; }
    if (ev.type === "transcript.partial") {
      state.mark = performance.now();
      if (ev.is_final && ev.text) {
        bus.emit("rec:final", { text: ev.text });
        closeUtterance(ev.text);
        armAutoStop();
      } else {
        rawInterim = ev.text || "";
        pump(); armAutoStop();
        bus.emit("rec:interim", { text: ev.text || "" });
      }
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
      relayFail(ev.message || "error");
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
    settleLine().then((text) => { if (text) funnel(); else log("nothing recognized", "dim"); });
  }
  function relayFail(msg) {
    // NB: relay may be null here — a failure before the socket/nodes were
    // assigned (mic denied, WebSocket ctor threw). Clean up only if built.
    if (relay) relayCleanup();
    clearTimeout(relayT);
    state.lastError = "hosted demo: " + msg;
    log("hosted engine: " + msg + " · using the browser engine", "err");
    if (D.recording) {
      engine = "none";
      startBrowser();
      caption();
    } else {
      settleLine();
    }
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
    if (r.instruction && r.text) {
      busy = false;
      line = l; await rewriteInPlace(r.instruction); line = null;
      r = { ...r, text: l.committed() };
    } else if (r.instruction) {
      log("nothing typed yet to rewrite · dictate first, then the wake word", "dim");
    }
    Typist.setTarget({ frozen: r.text, molten: "" });
    await Typist.settled();
    Typist.release();
    Window.paintDoc();
    const whole = (r.typedBefore + (r.text[0] === " " || !r.typedBefore ? "" : " ") + r.text).trim();
    if (whole) done(whole);
    return whole;
  }
  function clipboardLanding(text) {
    // focus moved mid-dictation: the transcript lands on the clipboard,
    // never in the wrong window — exactly what the daemon does
    if (!text) return "";
    try { navigator.clipboard.writeText(text); } catch {}
    log("focus changed · transcript went to the clipboard", "dim");
    state.dictations++;
    done(text);
    return text;
  }

  function stop() {
    if (!D.recording) return;
    setRecording(false);
    clearInterval(tickT);
    clearTimeout(autoStopT);
    Signal.stop();
    if (rec) { try { rec.stop(); } catch {} rec = null; }
    state.dictations++;
    bus.emit("rec:stop", {});
    const finish = async () => {
      const text = await settleLine();
      if (!text && !sessionCommits) {
        if (engine === "sim" || engine === "trying" || engine === "none") {
          log("nothing recognized · a scripted line stands in", "dim");
          playScript(SIM_LINES[simIdx++ % SIM_LINES.length]);
        } else log("nothing recognized", "dim");
      }
      caption();
    };
    if (engine === "relay") {
      if (relay) relayFinish(); else finish();
      return;
    }
    if (engine === "local" || engine === "loading") {
      const l = local; local = null;
      if (l) l.stop().then(finish, finish); else finish();
      return;
    }
    // give a final result a beat to arrive, then settle the line
    setTimeout(finish, engine === "live" || engine === "trying" ? 350 : 0);
  }

  // ── scripted playback: the autopilot and the no-engine fallback ─────────
  // A scripted session replays interim snapshots through the same molten
  // engine a live session uses — deterministic, and shaped like the truth:
  // one recording, several utterances, a pause between them.
  // script: { text, revise } for one utterance, or { utterances: [...] }.
  async function playScript(scr, opts = {}) {
    cancelScript();
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
      const compiled = compileScript(u.text, { revise: u.revise || null });
      if (reduced || !liveFlow()) { closeUtterance(compiled.final); continue; }
      let t0 = performance.now();
      for (const step of compiled.steps) {
        await wait(Math.max(0, step.t - (performance.now() - t0)));
        if (stopped()) return;
        rawInterim = step.text; pump();
      }
      await wait(560);
      if (stopped()) return;
      closeUtterance(compiled.final);
      while (busy && !stopped()) await wait(50);   // a chord lands before the next words
      await wait(u.pause || 900);
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

  function done(text) {
    state.ledger.push({ text, app: Window.focusedName(), when: Date.now() });
    if (state.ledger.length > 20) state.ledger.shift();
    landing = guard ? null : { register: Window.focusedName(), tail: text.slice(-1), when: Date.now() };
    bus.emit("type:text", { text });
  }
  bus.on("doc:cleared", () => { landing = null; });

  D.start = start; D.stop = stop;
  D.toggle = () => (D.recording ? stop() : start());
  // scripted typing into the focused window — the autopilot uses this;
  // it ends in the same type:text event real dictation does
  D.simulate = (scr, opts) => { if (!D.recording) return playScript(scr, opts); return Promise.resolve(""); };
  D.busy = () => D.recording || script !== null;
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
