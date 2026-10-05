// ═══ LOCAL STT — an open-source model on your GPU, in this tab ════════════
// Moonshine tiny (27M parameters, MIT) through transformers.js and ONNX
// Runtime, on WebGPU where the browser has it and on WASM where it does
// not. Nothing is fetched until the mic is tapped; then the runtime and
// the weights come down once (and are cached by the browser) and audio
// never leaves the tab. The transcript is produced the way a streaming
// provider produces it: the whole current utterance is re-recognized
// every few hundred milliseconds, so words land, revise, and settle —
// the molten engine treats each pass as one more interim result.
import { bus } from "./bus.js";

export const LocalSTT = (() => {
  const CDN = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.0/dist/transformers.min.js";
  const MODEL = "onnx-community/moonshine-tiny-ONNX";
  const S = { state: "idle", device: null, error: "", label: "Moonshine tiny", stats: { passes: 0, lastMs: 0, audioSec: 0 },
              get sizeMB() { return navigator.gpu ? 55 : 28; } };
  let pipe = null, loadP = null, tf = null;

  S.supported = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia &&
    (window.AudioContext || window.webkitAudioContext) && typeof WebAssembly === "object");
  S.gpu = () => !!navigator.gpu;

  async function makePipe(device, progress_callback) {
    const opts = { device, progress_callback };
    // 4-bit weights on the GPU (the quantization WebGPU kernels are built
    // for), 8-bit on the CPU; neither needs shader-f16
    opts.dtype = device === "webgpu"
      ? { encoder_model: "q4", decoder_model_merged: "q4" }
      : { encoder_model: "q8", decoder_model_merged: "q8" };
    return tf.pipeline("automatic-speech-recognition", MODEL, opts);
  }
  function load(onProgress) {
    if (pipe) return Promise.resolve(pipe);
    if (loadP) return loadP;
    S.state = "loading";
    loadP = (async () => {
      tf = await import(/* webpackIgnore: true */ CDN);
      tf.env.allowLocalModels = false;
      bus.emit("relay:request", {});            // the footer counts what you asked for
      const files = new Map();
      const progress_callback = (p) => {
        if (p.status !== "progress" || !p.total) return;
        files.set(p.file, [p.loaded, p.total]);
        let l = 0, t = 0;
        for (const [a, b] of files.values()) { l += a; t += b; }
        if (onProgress) onProgress(l, t);
      };
      let device = navigator.gpu ? "webgpu" : "wasm";
      try { pipe = await makePipe(device, progress_callback); }
      catch (e) {
        if (device !== "webgpu") throw e;
        device = "wasm";                        // the adapter refused: same model, on the CPU
        pipe = await makePipe(device, progress_callback);
      }
      S.device = device;
      await pipe(new Float32Array(8000));       // warm the shaders before the first word
      S.state = "ready";
      return pipe;
    })().catch((e) => { S.state = "failed"; S.error = e && e.message ? e.message : String(e); loadP = null; throw e; });
    return loadP;
  }

  // ── a session: mic stream in, transcripts out ──────────────────────────
  // onInterim(text): the current utterance so far; onFinal(text): an
  // utterance closed by ~1.2 s of silence (or by stop()).
  async function start(stream, { onInterim, onFinal }) {
    const p = await load();
    const AC = window.AudioContext || window.webkitAudioContext;
    const ctx = new AC({ sampleRate: 16000 });
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(2048, 1, 1);
    const sink = ctx.createGain(); sink.gain.value = 0;
    const MAX = 16000 * 28, MIN = 16000 * 0.35, GAP = 1200, EVERY = 500, FLOOR = 0.012;
    let chunks = [], total = 0, voiced = 0, lastVoice = 0, lastRun = 0, busy = false, again = false, closed = false, lastText = "";

    function take() {
      const out = new Float32Array(total);
      let o = 0;
      for (const c of chunks) { out.set(c, o); o += c.length; }
      return out;
    }
    async function run(final) {
      if (busy) { again = true; return; }
      busy = true;
      try {
        const audio = take();
        if (audio.length >= MIN) {
          const t0 = performance.now();
          const r = await p(audio);
          S.stats.passes++; S.stats.lastMs = performance.now() - t0; S.stats.audioSec = audio.length / 16000;
          // honest about slow hardware: a pass slower than the audio it covers
          if (!S.slow && S.stats.lastMs > Math.max(3000, S.stats.audioSec * 1500)) { S.slow = true; bus.emit("stt:slow", { ms: S.stats.lastMs }); }
          const text = (r && r.text ? r.text : "").trim();
          if (final) { chunks = []; total = 0; voiced = 0; lastText = ""; if (text) onFinal(text); }
          else if (text !== lastText) { lastText = text; onInterim(text); }
        } else if (final) { chunks = []; total = 0; voiced = 0; lastText = ""; }
      } catch (e) { S.error = e && e.message ? e.message : String(e); }
      busy = false;
      lastRun = performance.now();
      if (again && !closed) { again = false; run(false); }
    }
    proc.onaudioprocess = (e) => {
      if (closed) return;
      const f = e.inputBuffer.getChannelData(0);
      chunks.push(new Float32Array(f));
      total += f.length;
      let sum = 0;
      for (let i = 0; i < f.length; i++) sum += f[i] * f[i];
      const rms = Math.sqrt(sum / f.length);
      const now = performance.now();
      if (rms > FLOOR) { lastVoice = now; voiced += f.length; }
      if (total > MAX) { while (total > MAX) total -= chunks.shift().length; }
      if (voiced > 0 && lastVoice && now - lastVoice > GAP) { lastVoice = 0; run(true); return; }
      // preview cadence adapts to the device: never more often than a pass takes
      if (voiced > 0 && now - lastRun > Math.max(EVERY, S.stats.lastMs * 1.2)) run(false);
    };
    src.connect(proc); proc.connect(sink); sink.connect(ctx.destination);
    return {
      async stop() {
        closed = true;
        try { proc.disconnect(); src.disconnect(); sink.disconnect(); } catch {}
        // one last pass over whatever is still open — busy or not, wait our turn
        while (busy) await new Promise((r) => setTimeout(r, 30));
        closed = false; await run(true); closed = true;
        try { await ctx.close(); } catch {}
      },
    };
  }

  S.load = load; S.start = start;
  return S;
})();
