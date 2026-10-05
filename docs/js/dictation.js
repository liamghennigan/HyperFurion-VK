// ═══ DICTATION — the product's forward lane, molten ════════════════════════
// Every path — your browser's speech engine, the hosted xAI relay, and the
// scripted chips — feeds the same flow engine (flow.js), the same way every
// provider feeds the daemon's. Words render molten, repair in place, freeze
// on the stability window, honor the spoken grammar and the focused
// window's register — and every keystroke lands on the board.
import { mic, micCap, stopBtn, favicon, reduced, SR, baseTitle, FAV_IDLE, FAV_REC } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { Ticker } from "./ticker.js";
import { Signal } from "./signal.js";
import { settings } from "./settings.js";
import { Demo } from "./demo-relay.js";
import { LocalSTT } from "./stt-local.js";
import { Window } from "./window.js";
import { Typist } from "./typist.js";
import { moltenLine, compileScript, pageRewrite } from "./flow.js";

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
  let line = null;            // the molten line for the current utterance
  let rawFinal = "", rawInterim = "";
  let guard = false;          // focus changed mid-dictation: typing is frozen
  let tickT = 0, autoStopT = 0;
  let playTimers = [];        // scripted playback
  let scripted = false;
  Object.defineProperty(D, "engine", { get: () => engine });

  const liveFlow = () => settings.flowLive && settings.interim;
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
  function newLine() {
    line = moltenLine({ register: Window.register(), cfg: settings });
    rawFinal = ""; rawInterim = ""; guard = false;
    state.lastError = "";
  }
  function raw() { return (rawFinal + " " + rawInterim).trim(); }
  function paint(r) {
    if (guard) return;                       // the daemon never types into the wrong window
    for (let k = 0; k < (r.retracts || 0); k++) Typist.retract();
    Typist.setTarget(r);
  }
  function pump() {
    if (!line) return;
    if (!liveFlow()) {                       // flow.live = false → the old behavior
      Typist.setTarget({ frozen: rawFinal, molten: settings.interim ? rawInterim : "" });
      return;
    }
    paint(line.update(raw(), performance.now()));
  }
  function armAutoStop() {
    clearTimeout(autoStopT);
    const ms = settings.autoStopMs;
    if (ms > 0 && D.recording)
      autoStopT = setTimeout(() => { log("auto-stop: " + ms + " ms of silence", "dim"); stop(); }, ms);
  }

  function start() {
    if (D.recording) return;
    stopPlayback();
    scripted = false;
    newLine();
    Typist.reset();
    setRecording(true);
    Window.setLatency(null);
    const sigP = Signal.start();
    Ticker.wake();
    // the stability clock ticks even between provider updates
    tickT = setInterval(pump, 350);
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
          rawFinal += " " + text; rawInterim = "";
          state.mark = performance.now();
          pump(); armAutoStop();
          bus.emit("rec:final", { text });
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
            if (r.isFinal) { rawFinal += " " + r[0].transcript; bus.emit("rec:final", { text: r[0].transcript }); }
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
        rawFinal += " " + ev.text;
        rawInterim = "";
        pump(); armAutoStop();
        bus.emit("rec:final", { text: ev.text });
      } else {
        rawInterim = ev.text || "";
        pump(); armAutoStop();
        bus.emit("rec:interim", { text: ev.text || "" });
      }
    } else if (ev.type === "transcript.done") {
      const t = String(ev.text || "");
      if (t.length >= rawFinal.trim().length) { rawFinal = t; rawInterim = ""; }
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
    if (!settleLine()) playScript(SIM_LINES[simIdx++ % SIM_LINES.length]);
    else funnel();
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
      if (!settleLine()) playScript(SIM_LINES[simIdx++ % SIM_LINES.length]);
    }
  }
  function funnel() {
    if (funneled) return;
    funneled = true;
    log("that came through xAI via the relay", "dim");
  }

  // ── the landing: flush the grammar, run the rewrite lane, commit ────────
  // Returns the committed text ("" when nothing was recognized).
  function settleLine() {
    if (!line) return "";
    state.lastRaw = raw();
    if (!liveFlow()) {
      line = null;
      const text = raw();
      if (guard) { return clipboardLanding(text); }
      Typist.setTarget({ frozen: text, molten: "" });
      const settled = Typist.commit(text);
      if (settled) done(settled);
      return settled;
    }
    const r = line.flush();
    const text = (r.frozen + r.molten).trim();
    if (guard) { line = null; return clipboardLanding(text); }
    for (let k = 0; k < (r.retracts || 0); k++) Typist.retract();
    line = null;
    if (r.instr && text) {
      // the wake word: rewrite the just-typed utterance in place. These
      // timers finish on their own — a new dictation must never cancel
      // the commit out from under the window.
      Typist.setTarget({ frozen: text, molten: "" });
      const rewritten = pageRewrite(text, r.instr);
      Typist.settled().then(() => setTimeout(() => {
        Typist.setTarget({ frozen: "", molten: rewritten, repair: true });
        log("“" + settings.wakeWord + ", " + r.instr + "” · rewritten in place (page stand-in for your LLM)", "dim");
        Typist.settled().then(() => setTimeout(() => {
          Typist.setTarget({ frozen: rewritten, molten: "" });
          const t = Typist.commit(rewritten); if (t) done(t);
        }, reduced ? 0 : 320));
      }, reduced ? 0 : 420));
      return rewritten;
    }
    if (r.instr && !text) {
      log("nothing typed yet to rewrite · dictate first, then the wake word", "dim");
      return "";
    }
    Typist.setTarget({ frozen: text, molten: "" });
    const settled = Typist.commit(text);
    if (settled) done(settled);
    return settled;
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
    if (engine === "relay") {
      if (relay) relayFinish();
      else if (!settleLine()) playScript(SIM_LINES[simIdx++ % SIM_LINES.length]);
      return;
    }
    if (engine === "local" || engine === "loading") {
      const l = local; local = null;
      const finish = () => { if (!settleLine()) playScript(SIM_LINES[simIdx++ % SIM_LINES.length]); caption(); };
      if (l) l.stop().then(finish, finish); else finish();
      return;
    }
    // give a final result a beat to arrive, then settle the line
    setTimeout(() => {
      if (!settleLine()) {
        if (engine !== "live") log("nothing recognized · a scripted line stands in", "dim");
        playScript(SIM_LINES[simIdx++ % SIM_LINES.length]);
      }
      caption();
    }, engine === "live" || engine === "trying" ? 350 : 0);
  }

  // ── scripted playback: chips, autopilot, and the no-engine fallback ─────
  // A compiled script replays interim snapshots through the same molten
  // engine a live session uses — deterministic, and shaped like the truth.
  function playScript(script, opts = {}) {
    stopPlayback();
    const sc = typeof script === "string" ? { text: script } : script;
    const compiled = compileScript(sc.text, { revise: sc.revise || null });
    newLine();
    scripted = true;
    caption();
    if (opts.raw) line = moltenLine({ register: { name: "verbatim", smartCaps: false, grammar: false }, cfg: settings });
    if (reduced || !liveFlow()) {
      rawFinal = compiled.final;
      line.update(compiled.final, performance.now());
      settleLine();
      state.dictations++;
      return;
    }
    for (const step of compiled.steps) {
      playTimers.push(setTimeout(() => {
        rawFinal = ""; rawInterim = step.text;
        pump();
      }, step.t));
    }
    playTimers.push(setTimeout(() => {
      rawFinal = compiled.final; rawInterim = "";
      pump();
      settleLine();
      state.dictations++;
    }, compiled.dur + 560));
  }
  function stopPlayback() {
    for (const t of playTimers) clearTimeout(t);
    playTimers = [];
  }

  function done(text) {
    state.ledger.push({ text, app: Window.focusedName(), when: Date.now() });
    if (state.ledger.length > 20) state.ledger.shift();
    bus.emit("type:text", { text });
    engine = "none";
  }

  D.start = start; D.stop = stop;
  D.toggle = () => (D.recording ? stop() : start());
  // scripted typing into the focused window — the chips and the autopilot
  // use this; it ends in the same type:text event real dictation does
  D.simulate = (script, opts) => { if (!D.recording) playScript(script, opts); };
  D.busy = () => D.recording || playTimers.length > 0;
  D.cancelScript = () => { stopPlayback(); if (line) { line = null; Typist.reset(); } };
  // the focus guard: the window calls this when focus moves mid-dictation
  D.guard = () => {
    if (!D.recording || guard) return;
    guard = true;
    Typist.reset();
    log("focus changed · typing frozen", "dim");
  };

  bus.on("simulate", ({ script }) => D.simulate(script));
  bus.on("stt:slow", ({ ms }) => log("slow device: " + (ms / 1000).toFixed(1) + " s per pass · words land late here", "dim"));
  bus.on("focus:changed", () => D.guard());
  mic.addEventListener("click", () => D.toggle());
  stopBtn.addEventListener("click", () => stop());
  setRecording(false);
  return D;
})();
