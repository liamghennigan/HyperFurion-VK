// ═══ DICTATION — the product's forward lane, now molten ═══════════════════
// Every path — your browser's speech engine, the hosted xai relay, and the
// scripted chips — feeds the same flow engine (flow.js), the same way every
// provider feeds the daemon's. Words render molten, repair in place, freeze
// on the stability window, honor the spoken grammar and the focused
// window's register. flow.live = false in the live config restores the old
// record → wait → type behavior, exactly like the daemon's flow.enabled.
import { desktop, pill, mic, stopBtn, favicon, reduced, SR, baseTitle, FAV_IDLE, FAV_REC } from "./env.js";
import { bus } from "./bus.js";
import { state } from "./state.js";
import { Ticker } from "./ticker.js";
import { Signal } from "./signal.js";
import { Scope } from "./scope.js";
import { Hero } from "./hero2d.js";
import { Config } from "./config.js";
import { Demo } from "./demo-relay.js";
import { Terminal } from "./terminal.js";
import { Desktop } from "./desktop.js";
import { Hints } from "./hints.js";
import { moltenLine, compileScript, pageRewrite } from "./flow.js";

export const Dictation = (() => {
  const SIM_LINES = [
    { text: "dictated comma not typed em dash this sentence never touched a keyboard period" },
    { text: "note to self colon cancel the RSI appointment", revise: { at: 6, wrong: "RSVP" } },
    { text: "twenty three unread emails question mark later period" },
  ];
  const D = { recording: false };
  let engine = "none", rec = null, simIdx = 0, redSample = 0;
  let relay = null, relayT = 0, funneled = false;
  let line = null;            // the molten line for the current utterance
  let rawFinal = "", rawInterim = "";
  let guard = false;          // focus changed mid-dictation: typing is frozen
  let tickT = 0, autoStopT = 0;
  let playTimers = [];        // scripted playback
  Object.defineProperty(D, "engine", { get: () => engine });

  const liveFlow = () => Config.cfg.flowLive && Config.cfg.interim;

  function setRecording(on) {
    D.recording = on;
    state.recording = on;   // the ticker's idle-park check reads this mirror
    pill.hidden = !on;
    stopBtn.hidden = !on;
    mic.classList.toggle("live", on);
    mic.setAttribute("aria-pressed", String(on));
    document.title = on ? "● recording — " + baseTitle : baseTitle;
    favicon.href = on ? FAV_REC : FAV_IDLE;
    Desktop.recMode(on);
  }

  // ── one render pipe: raw transcript -> engine -> the focused window ─────
  function newLine() {
    line = moltenLine({ register: Desktop.register(), cfg: Config.cfg });
    rawFinal = ""; rawInterim = ""; guard = false;
    state.lastError = "";
  }
  function raw() { return (rawFinal + " " + rawInterim).trim(); }
  function paint(r) {
    if (guard) return;                       // the daemon never types into the wrong window
    for (let k = 0; k < (r.retracts || 0); k++) Desktop.retract();
    Desktop.setLine(r.frozen, r.molten, { repair: r.repair, instr: r.instr });
  }
  function pump() {
    if (!line) return;
    if (!liveFlow()) {                       // flow.live = false → the old behavior
      Desktop.setLine(rawFinal, Config.cfg.interim ? rawInterim : "", {});
      return;
    }
    paint(line.update(raw(), performance.now()));
  }
  function armAutoStop() {
    clearTimeout(autoStopT);
    const ms = Config.cfg.autoStopMs;
    if (ms > 0 && D.recording)
      autoStopT = setTimeout(() => { Terminal.print("· auto-stop: " + ms + " ms of silence", "dim"); stop(); }, ms);
  }

  function start() {
    if (D.recording) return;
    stopPlayback();
    newLine();
    Desktop.setLine("", "", {});
    Desktop.probe();
    setRecording(true);
    const sigP = Signal.start();
    Ticker.wake();
    // the stability clock ticks even between provider updates
    tickT = setInterval(pump, 350);
    armAutoStop();
    if (reduced) {
      // no animation loop runs, but the frozen waveform still needs data
      redSample = setInterval(() => Scope.push(Signal.frame().peak), 250);
    }
    if (Demo.armed()) {
      engine = "relay";
      bus.emit("rec:start", { engine });
      startRelay(sigP);
      return;
    }
    bus.emit("rec:start", { engine });
    startBrowser();
  }
  function startBrowser() {
    if (SR) {
      try {
        rec = new SR();
        rec.lang = Config.cfg.lang || navigator.language || "en-US";
        rec.continuous = true;
        rec.interimResults = true;
        rec.onresult = (e) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const r = e.results[i];
            if (r.isFinal) { rawFinal += " " + r[0].transcript; bus.emit("rec:final", { text: r[0].transcript }); }
            else interim += r[0].transcript;
          }
          engine = "live";
          rawInterim = interim;
          pump();
          armAutoStop();
          bus.emit("rec:interim", { text: interim });
        };
        rec.onerror = () => { if (D.recording && engine !== "live") engine = "sim"; };
        rec.onend = () => { if (D.recording && engine !== "live") engine = "sim"; };
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
        "&encoding=pcm&interim_results=true&language=" + encodeURIComponent(Config.cfg.lang || "en"));
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
      Hero.caption();
    } catch (err) {
      relayFail(err && err.message ? err.message : "unavailable");
    }
  }
  function relayEvent(m) {
    if (!relay) return;
    let ev; try { ev = JSON.parse(m.data); } catch { return; }
    if (ev.type === "transcript.partial") {
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
      Terminal.print("· " + ev.message, "dim");
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
    Hints.advance();
  }
  function relayFail(msg) {
    // NB: relay may be null here — a failure before the socket/nodes were
    // assigned (mic denied, WebSocket ctor threw). Clean up only if built.
    if (relay) relayCleanup();
    clearTimeout(relayT);
    state.lastError = "hosted demo: " + msg;
    Terminal.print("hosted demo: " + msg, "err");
    if (D.recording) {
      engine = "none";
      Terminal.print("falling back to your browser's engine", "dim");
      startBrowser();
      Hero.caption();
    } else {
      if (!settleLine()) playScript(SIM_LINES[simIdx++ % SIM_LINES.length]);
      Hints.advance();
    }
  }
  function funnel() {
    if (funneled) return;
    funneled = true;
    Terminal.print("· that came through xai grok stt — the hosted tier. $5/mo, one key: type subscribe", "dim");
  }

  // ── the landing: flush the grammar, run the rewrite lane, commit ────────
  // Returns the committed text ("" when nothing was recognized).
  function settleLine() {
    if (!line) return "";
    if (!liveFlow()) {
      // flow.live = false — the old behavior exactly: no grammar, no
      // molten, the raw transcript lands on stop
      line = null;
      const text = raw();
      if (guard) {
        if (!text) return "";
        try { navigator.clipboard.writeText(text); } catch {}
        Terminal.print("⚑ focus changed mid-dictation — typing froze; the transcript landed on the clipboard", "dim");
        done(text);
        return text;
      }
      Desktop.setLine(text, "", {});
      const settled = Desktop.commit();
      if (settled) done(settled);
      return settled;
    }
    const r = line.flush();
    const text = (r.frozen + r.molten).trim();
    if (guard) {
      // focus moved mid-dictation: the transcript lands on the clipboard,
      // never in the wrong window — exactly what the daemon does
      line = null;
      if (!text) return "";
      try { navigator.clipboard.writeText(text); } catch {}
      Terminal.print("⚑ focus changed mid-dictation — typing froze; the transcript landed on the clipboard", "dim");
      state.dictations++;
      done(text);
      return text;
    }
    for (let k = 0; k < (r.retracts || 0); k++) Desktop.retract();
    line = null;
    if (r.instr && text) {
      // the wake word: rewrite the just-typed utterance in place. These
      // timers finish on their own — a new dictation must never cancel
      // the commit out from under the window.
      Desktop.setLine(text, "", {});
      const rewritten = pageRewrite(text, r.instr);
      setTimeout(() => {
        Desktop.setLine(rewritten, "", { repair: true });
        Terminal.print("✦ " + (Config.cfg.wakeWord || "furion") + ", " + r.instr +
          " — rewritten in place. page stand-in; the daemon sends it through your [llm]", "dim");
        setTimeout(() => { const t = Desktop.commit(); if (t) done(t); }, reduced ? 0 : 420);
      }, reduced ? 0 : 520);
      return rewritten;
    }
    if (r.instr && !text) {
      Terminal.print("✦ “" + (Config.cfg.wakeWord || "furion") + ", " + r.instr +
        "” — nothing typed yet to rewrite. dictate first, then speak the wake word", "dim");
      return "";
    }
    Desktop.setLine(text, "", {});
    const settled = Desktop.commit();
    if (settled) done(settled);
    return settled;
  }

  function stop() {
    if (!D.recording) return;
    setRecording(false);
    clearInterval(redSample);
    clearInterval(tickT);
    clearTimeout(autoStopT);
    Signal.stop();
    Scope.freeze();
    if (rec) { try { rec.stop(); } catch {} rec = null; }
    state.dictations++;
    bus.emit("rec:stop", {});
    if (engine === "relay") {
      if (relay) {
        // the socket outlives the mic: flush audio.done, then settle on
        // transcript.done (or the timeout)
        relayFinish();
      } else {
        // startRelay never opened a socket (mic still pending, or it
        // failed before assigning relay) — settle the line here so the
        // terminal never hangs
        if (!settleLine()) playScript(SIM_LINES[simIdx++ % SIM_LINES.length]);
        Hints.advance();
      }
      return;
    }
    // give a final result a beat to arrive, then settle the line
    setTimeout(() => {
      if (!settleLine()) playScript(SIM_LINES[simIdx++ % SIM_LINES.length]);
      Hints.advance();
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
    Desktop.probe();   // playback shows the focus probe too
    if (opts.raw) line = moltenLine({ register: { name: "verbatim", smartCaps: false, grammar: false }, cfg: Config.cfg });
    if (reduced || !liveFlow()) {
      rawFinal = compiled.final;
      line.update(compiled.final, performance.now());
      settleLine();
      state.dictations++;
      Hints.advance();
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
      // scripted playback advances the tour, the way a real dictation does
      state.dictations++;
      Hints.advance();
    }, compiled.dur + 560));
  }
  function stopPlayback() {
    for (const t of playTimers) clearTimeout(t);
    playTimers = [];
  }

  function done(text) {
    state.ledger.push({ text, app: Desktop.focusedName(), when: Date.now() });
    if (state.ledger.length > 20) state.ledger.shift();
    bus.emit("type:text", { text });
    engine = "none";
  }

  D.start = start; D.stop = stop;
  D.toggle = () => (D.recording ? stop() : start());
  // scripted typing into the focused window — the try-saying chips and the
  // autopilot use this; it ends in the same type:text event real dictation does
  D.simulate = (script, opts) => { if (!D.recording) playScript(script, opts); };
  // the focus guard: Desktop calls this when focus moves mid-dictation
  D.guard = () => {
    if (!D.recording || guard) return;
    guard = true;
    Desktop.setLine("", "", {});
    Terminal.print("⚑ focus changed — typing frozen; the transcript will land on the clipboard", "dim");
  };
  // `voice-keyboard transform "<instruction>"` — rewrite the last dictation
  D.transform = (instruction) => {
    const last = state.ledger[state.ledger.length - 1];
    if (!last) return null;
    const rewritten = pageRewrite(last.text, instruction);
    Desktop.replaceLast(last.app, rewritten);
    last.text = rewritten;
    return rewritten;
  };

  // starting from the hero mic: bring the demo into view so you can
  // watch your words get typed — the whole point
  mic.addEventListener("click", () => {
    const starting = !D.recording;
    D.toggle();
    if (starting) {
      const r = desktop.getBoundingClientRect();
      if (r.top > innerHeight - 120 || r.bottom < 80)
        desktop.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
    }
  });
  // the recording pill doubles as the stop control next to the terminal
  pill.setAttribute("role", "button");
  pill.setAttribute("tabindex", "0");
  pill.title = "stop recording";
  pill.style.cursor = "pointer";
  pill.addEventListener("click", () => stop());
  pill.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); stop(); }
  });
  // and an unmissable stop button lives in the terminal bar
  stopBtn.setAttribute("aria-label", "stop recording");
  stopBtn.addEventListener("click", () => stop());
  return D;
})();
