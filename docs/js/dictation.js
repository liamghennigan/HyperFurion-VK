// ═══ DICTATION — the product's forward lane ═══════════════════════════════
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

export const Dictation = (() => {
  const SIM_LINES = [
    "dictated, not typed — this sentence never touched a keyboard",
    "git commit -m 'wrote this one out loud'",
    "note to self: cancel the RSI appointment",
  ];
  const D = { recording: false };
  let engine = "none", rec = null, typeTimer = 0, simIdx = 0, redSample = 0;
  let relay = null, relayT = 0, funneled = false;
  Object.defineProperty(D, "engine", { get: () => engine });

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
  function start() {
    if (D.recording) return;
    clearInterval(typeTimer);
    Desktop.setLine("", "");
    setRecording(true);
    const sigP = Signal.start();
    Ticker.wake();
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
        let committed = "";
        rec.onresult = (e) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const r = e.results[i];
            if (r.isFinal) { committed += r[0].transcript; bus.emit("rec:final", { text: r[0].transcript }); }
            else interim += r[0].transcript;
          }
          engine = "live";
          Desktop.setLine(committed, interim);
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
      relay = { ws, src, proc, sink, committed: "", done: false };
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
        relay.committed = relay.committed ? relay.committed + " " + ev.text : ev.text;
        Desktop.setLine(relay.committed, "");
        bus.emit("rec:final", { text: ev.text });
      } else {
        Desktop.setLine(relay.committed, ev.text || "");
        bus.emit("rec:interim", { text: ev.text || "" });
      }
    } else if (ev.type === "transcript.done") {
      const t = String(ev.text || "");
      if (t.length >= relay.committed.length) relay.committed = t;
      Desktop.setLine(relay.committed, "");
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
    const settled = Desktop.commit();
    if (settled) { done(settled); funnel(); }
    else typeSim(SIM_LINES[simIdx++ % SIM_LINES.length]);
    Hints.advance();
  }
  function relayFail(msg) {
    // NB: relay may be null here — a failure before the socket/nodes were
    // assigned (mic denied, WebSocket ctor threw). Clean up only if built.
    if (relay) relayCleanup();
    clearTimeout(relayT);
    Terminal.print("hosted demo: " + msg, "err");
    if (D.recording) {
      engine = "none";
      Terminal.print("falling back to your browser's engine", "dim");
      startBrowser();
      Hero.caption();
    } else {
      const settled = Desktop.commit();
      if (settled) done(settled);
      else typeSim(SIM_LINES[simIdx++ % SIM_LINES.length]);
      Hints.advance();
    }
  }
  function funnel() {
    if (funneled) return;
    funneled = true;
    Terminal.print("· that came through xai grok stt — the hosted tier. $5/mo, one key: type subscribe", "dim");
  }

  function stop() {
    if (!D.recording) return;
    setRecording(false);
    clearInterval(redSample);
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
        const settled = Desktop.commit();
        if (settled) { done(settled); }
        else typeSim(SIM_LINES[simIdx++ % SIM_LINES.length]);
        Hints.advance();
      }
      return;
    }
    // give a final result a beat to arrive, then settle the line
    setTimeout(() => {
      const settled = Desktop.commit();
      if (settled) { done(settled); }
      else typeSim(SIM_LINES[simIdx++ % SIM_LINES.length]);
      Hints.advance();
    }, engine === "live" || engine === "trying" ? 350 : 0);
  }
  function typeSim(text) {
    if (reduced) { Desktop.setLine(text, ""); Desktop.commit(); done(text); return; }
    let n = 0;
    typeTimer = setInterval(() => {
      Desktop.setLine(text.slice(0, ++n), "");
      if (n >= text.length) {
        clearInterval(typeTimer);
        Desktop.commit();
        done(text);
      }
    }, 40);
  }
  function done(text) {
    bus.emit("type:text", { text });
    engine = "none";
  }
  D.start = start; D.stop = stop;
  D.toggle = () => (D.recording ? stop() : start());
  // scripted typing into the focused window — the try-saying chips and the
  // autopilot use this; it ends in the same type:text event real dictation does
  D.simulate = (text) => { if (!D.recording) typeSim(String(text)); };
  // starting from the hero mic: bring the terminal into view so you can
  // watch your words get typed — the whole point of the demo
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
