// ═══ FIELD — voice as a physical medium: a weave of signal threads ════════
// A stateless WebGL2 particle field behind the whole page. Every particle's
// position is a pure function of (seed, time, audio, scroll) computed in the
// vertex shader — no simulation buffers, so a lost context restores for
// free and integrated GPUs never chase a feedback loop.
//
// The same signal that feeds every other gauge feeds this one: the live
// microphone while you dictate, the relay's real eve voice while it speaks,
// and the synthetic speech-burst generator the rest of the time. The
// browser's own speechSynthesis cannot be routed into WebAudio (platform
// limitation), so its word boundaries ping the pulse envelope instead —
// at word cadence the eye can't tell.
//
// Frequency maps to color along the spectral ramp (bass = indigo,
// sibilants = hot white); in the light scheme the same field renders as
// ink stipple on paper. Reduced motion never initializes this module.
import { reduced } from "./env.js";
import { bus } from "./bus.js";
import { Ticker } from "./ticker.js";
import { Signal } from "./signal.js";
import { AudioOut } from "./demo-relay.js";

const VERT = `#version 300 es
precision highp float;
layout(location = 0) in vec4 aSeed;   // four uniform randoms in [0,1)
uniform float uTime;                  // seconds
uniform float uLevel;                 // loudness 0..1 (mic, relay voice, or sim)
uniform float uPulse;                 // event envelope: words landing, keys typing
uniform float uCalm;                  // 0 = hero (dense, luminous) .. 1 = prose (sparse, at the edges)
uniform float uScene;                 // 0..1 scroll through the page
uniform float uPx;                    // point-size scale (device pixels)
uniform float uBands[8];              // log-spaced spectrum, bass -> sibilance
uniform vec3  uSpec[5];               // the spectral ramp
out vec3 vColor;
out float vAlpha;

vec3 ramp(float t) {
  float x = clamp(t, 0.0, 1.0) * 4.0;
  int i = int(min(x, 3.0));
  return mix(uSpec[i], uSpec[i + 1], fract(min(x, 3.9999)));
}

void main() {
  // threads, not grain: every particle is a bead on one of 24 strands.
  // A strand is a pure function of (its id, time), so the whole weave
  // stays stateless — but neighbours share a path, and the eye reads
  // flowing filaments of signal instead of television snow.
  float sid = floor(aSeed.x * 24.0);
  float r1 = fract(aSeed.x * 24.0);             // uniform again after the split
  float g1 = fract(sid * 0.6180339);            // per-strand goldens
  float g2 = fract(sid * 0.3819661);
  // each thread IS one frequency of the voice: three strands per band,
  // and a whole thread swells together when its band sounds — the weave
  // is the spectrum, laid out as matter
  int band = int(mod(sid, 8.0));
  float energy = uBands[band];                  // smoothed on the CPU — no flicker
  float e = smoothstep(0.10, 0.85, energy);
  // depth belongs to the strand, not the bead — a whole thread sits at one
  // remove, so it renders as a ribbon instead of dissolving into bokeh
  float depth = 0.35 + g2 * 0.65;

  // the bead drifts along its strand — motion with a direction, not jitter
  float t = fract(aSeed.y + uTime * (0.006 + 0.016 * g1) + g2 * 0.4);

  // ── hero weave: strands run with the old trace's corridor ─────────────
  float hx = t * 2.6 - 1.3;
  float base = (g1 - 0.5) * (0.30 + 0.85 * g2);  // stacked around the corridor
  float ph = hx * (1.5 + g2 * 1.9) + sid * 1.7;
  float hy = base
    + sin(ph + uTime * 0.42) * (0.055 + 0.12 * uLevel)
    + sin(ph * 2.6 - uTime * 0.27) * (0.028 + 0.05 * uLevel)
    + sign(base + 0.0001) * e * (0.08 + 0.28 * depth);
  // thread thickness: a tight sheath of beads around the core path
  float sheath = aSeed.z - 0.5;
  hy += sheath * (0.008 + 0.028 * e);
  vec2 heroP = vec2(hx, hy);

  // ── prose margins: a few strands climb the edges like signal in a wire ─
  float mside = mix(-1.0, 1.0, step(0.5, g2));
  float my = 1.3 - t * 2.6;                      // rising
  float mx = mside * (0.84 + (g1 - 0.5) * 0.18)
    + sin(my * 2.2 + uTime * 0.35 + sid) * (0.02 + 0.05 * e)
    + sheath * 0.012;
  vec2 marginP = vec2(mx, my);

  float calm = smoothstep(0.0, 1.0, uCalm);
  vec2 p = mix(heroP, marginP, calm);

  // the pulse envelope breathes the whole weave outward for a beat
  float r = max(length(p), 0.001);
  p += (p / r) * uPulse * 0.06 * depth;

  // the page settles as you approach the colophon
  p *= 1.0 - 0.08 * uScene;

  gl_Position = vec4(p, 0.0, 1.0);

  float size = uPx * (0.5 + depth * 0.7) * (0.8 + e * 1.1 + uPulse * 0.4);
  gl_PointSize = clamp(size, 1.0, 12.0);

  // hue anchored on the phosphor cyan mid-ramp; energy walks it hotter
  float hue = 0.14 + float(band) / 7.0 * 0.62 + e * 0.24;
  vec3 col = ramp(hue);
  col = mix(col, uSpec[4], e * e * 0.6);        // hot when loud
  vColor = col;

  // in the margins only a third of the strands stay lit — a few clear
  // threads, never a band of snow
  float keep = mix(1.0, step(g1, 0.34) * 0.85, calm);
  // the thread glows from its core: beads brighten toward the strand's
  // centre line, so each filament reads as a line of light, not dots
  float core = 1.0 - abs(sheath) * 2.0;
  vAlpha = (0.10 + 0.14 * core * core + 0.5 * e + 0.10 * uLevel + 0.22 * uPulse)
         * (0.35 + 0.65 * depth)
         * keep
         * mix(1.0, 0.55, calm);
}
`;

const FRAG = `#version 300 es
precision mediump float;
in vec3 vColor;
in float vAlpha;
uniform float uAlpha;   // scheme master alpha (--field-alpha)
uniform float uInk;     // 1 = light scheme: ink stipple, source-over
out vec4 frag;
void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;
  float core = smoothstep(1.0, 0.0, d);
  float a = core * core * core * vAlpha * uAlpha;
  // premultiplied output: additive glow in the dark, ink on paper in the light
  frag = vec4(vColor * a, mix(0.0, a, uInk));
}
`;

function cssColor(name) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const m = v.match(/^#?([0-9a-f]{6})$/i);
  if (!m) return [0, 0, 0];
  const n = parseInt(m[1], 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
}

export const Field = (() => {
  const out = { active: false, count: 0 };
  if (reduced) return out;                       // stillness is a valid rendering

  const canvas = document.createElement("canvas");
  canvas.id = "field";
  canvas.setAttribute("aria-hidden", "true");
  const gl = canvas.getContext("webgl2", { alpha: true, antialias: false, premultipliedAlpha: true, powerPreference: "low-power" });
  if (!gl) return out;                           // hero2d keeps the old instrument running
  document.body.prepend(canvas);

  const DPR_CAP = 1.5, RES = 0.75;               // additive glow hides the upscale
  let W = 0, H = 0, program = null, seedBuf = null, vao = null, uni = {};
  let count = Math.min(30000, Math.max(8000, Math.floor(innerWidth * innerHeight / 35)));
  let pulse = 0, calm = 0, calmTarget = 0, scene = 0, lost = false;
  let ratchet = 0, levelSm = 0;
  const dts = [], bandsSm = new Float32Array(8);

  function compile(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
      throw new Error(gl.getShaderInfoLog(s) || "shader");
    return s;
  }
  function build() {
    program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS))
      throw new Error(gl.getProgramInfoLog(program) || "link");
    gl.useProgram(program);
    for (const n of ["uTime", "uLevel", "uPulse", "uCalm", "uScene", "uPx", "uBands", "uSpec", "uAlpha", "uInk"])
      uni[n] = gl.getUniformLocation(program, n);
    const seeds = new Float32Array(count * 4);
    for (let i = 0; i < seeds.length; i++) seeds[i] = Math.random();
    vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    seedBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, seedBuf);
    gl.bufferData(gl.ARRAY_BUFFER, seeds, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 4, gl.FLOAT, false, 0, 0);
    palette();
  }
  function palette() {
    gl.useProgram(program);
    const spec = new Float32Array(15);
    for (let i = 0; i < 5; i++) spec.set(cssColor("--spec-" + (i + 1)), i * 3);
    gl.uniform3fv(uni.uSpec, spec);
    gl.uniform1f(uni.uAlpha, parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--field-alpha")) || .5);
    const dark = true;  // this page is always the dark instrument
    gl.uniform1f(uni.uInk, dark ? 0 : 1);
    // dark: pure additive phosphor; light: premultiplied ink over paper
    if (dark) gl.blendFunc(gl.ONE, gl.ONE);
    else gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  }
  function size() {
    const dpr = Math.min(devicePixelRatio || 1, DPR_CAP) * RES;
    W = canvas.width = Math.max(2, Math.round(canvas.clientWidth * dpr));
    H = canvas.height = Math.max(2, Math.round(canvas.clientHeight * dpr));
    gl.viewport(0, 0, W, H);
  }

  function tick(dt, t) {
    if (lost) return;
    // envelopes and scroll shaping
    pulse = Math.max(0, pulse - dt * 2.2);
    const doc = document.documentElement;
    const scrollMax = Math.max(1, doc.scrollHeight - innerHeight);
    scene = Math.min(1, scrollY / scrollMax);
    const heroH = innerHeight;
    calmTarget = Math.min(1, Math.max(0, (scrollY - heroH * 0.55) / (heroH * 0.6)));
    calm += (calmTarget - calm) * Math.min(1, dt * 4);

    const f = Signal.frame();
    const level = Math.min(1, Math.max(f.peak, AudioOut.level()));
    // envelope followers: fast attack, slow release — beads swell and fade
    // instead of flickering like snow
    const raw = Signal.bands();
    for (let i = 0; i < 8; i++) {
      const k = raw[i] > bandsSm[i] ? 9 : 2.2;
      bandsSm[i] += (raw[i] - bandsSm[i]) * Math.min(1, dt * k);
    }
    levelSm += (level - levelSm) * Math.min(1, dt * (level > levelSm ? 9 : 2.6));

    gl.useProgram(program);
    gl.bindVertexArray(vao);
    gl.uniform1f(uni.uTime, t);
    gl.uniform1f(uni.uLevel, levelSm);
    gl.uniform1f(uni.uPulse, Math.min(1, pulse));
    gl.uniform1f(uni.uCalm, calm);
    gl.uniform1f(uni.uScene, scene);
    gl.uniform1f(uni.uPx, (H / 1080) * 7 + 3);
    gl.uniform1fv(uni.uBands, bandsSm);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.drawArrays(gl.POINTS, 0, count >> ratchet);

    // adaptive one-way ratchet: whenever a 120-frame window runs slow,
    // shed half the particles (down to 1/8) — never thrashes back up
    dts.push(dt);
    if (dts.length >= 120) {
      dts.sort((a, b) => a - b);
      const p90 = dts[Math.floor(dts.length * 0.9)];
      dts.length = 0;
      if (p90 > 0.022 && ratchet < 3) { ratchet++; out.count = count >> ratchet; }
    }
  }

  try { build(); } catch { canvas.remove(); return out; }
  size();
  addEventListener("resize", () => size());
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", palette);

  canvas.addEventListener("webglcontextlost", (e) => { e.preventDefault(); lost = true; });
  canvas.addEventListener("webglcontextrestored", () => {
    try { build(); size(); lost = false; } catch { canvas.remove(); }
  });

  // the field runs at 30 while idle and 60 while the page is listening or
  // speaking; the Ticker parks it entirely after a minute of stillness
  const sub = Ticker.add({ el: canvas, fps: 30, fn: tick });
  const on = () => { sub.fps = 60; Ticker.wake(); };
  const off = () => { sub.fps = 30; };
  bus.on("rec:start", on);
  bus.on("rec:stop", off);
  bus.on("tts:start", on);
  bus.on("tts:end", off);
  bus.on("rec:final", () => { pulse = Math.min(1, pulse + 0.7); });
  bus.on("rec:interim", () => { pulse = Math.min(1, pulse + 0.12); });
  bus.on("tts:word", () => { pulse = Math.min(1, pulse + 0.45); });
  bus.on("type:text", () => { pulse = Math.min(1, pulse + 0.6); });

  out.active = true;
  out.count = count;
  return out;
})();
