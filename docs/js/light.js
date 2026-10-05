// ═══ LIGHT — the floor under the board, as one fragment shader ════════════
// A single full-screen triangle: a spice-colored pool of light under the
// keyboard that swells with the voice, a slow dune haze, a rim from the
// lower right, film grain, and short-lived heat where keys went down.
// No geometry, no simulation buffers — a lost context restores for free.
// The same signal that feeds every other gauge feeds this one; reduced
// motion or no WebGL2 leaves the CSS gradient in its place.
import { $, stage, reduced, webgl2 } from "./env.js";
import { bus } from "./bus.js";
import { Ticker } from "./ticker.js";
import { Signal } from "./signal.js";
import { AudioOut } from "./demo-relay.js";
import { Keyboard } from "./keyboard.js";

const VERT = `#version 300 es
void main() {
  vec2 p = vec2(gl_VertexID == 1 ? 3.0 : -1.0, gl_VertexID == 2 ? 3.0 : -1.0);
  gl_Position = vec4(p, 0.0, 1.0);
}`;
const FRAG = `#version 300 es
precision mediump float;
uniform vec2 uRes;
uniform float uTime, uLevel, uCool, uAlpha, uDim, uRadius;
uniform vec2 uPool;
uniform float uBands[8];
uniform vec4 uHeat[8];
out vec4 frag;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
}
void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  uv.y = 1.0 - uv.y;
  float asp = uRes.x / uRes.y;
  const vec3 cinnamon = vec3(0.706, 0.318, 0.122);
  const vec3 saffron = vec3(0.914, 0.635, 0.231);
  const vec3 sand = vec3(0.953, 0.918, 0.851);
  const vec3 ember = vec3(0.85, 0.33, 0.17);
  // the pool: light under the board, swelling with the voice
  vec2 q = vec2((uv.x - uPool.x) * asp, uv.y - uPool.y);
  float r = uRadius * (1.0 + 0.35 * uLevel);
  float pool = smoothstep(r, 0.0, length(q));
  pool *= pool;
  float hi = (uBands[5] + uBands[6] + uBands[7]) / 3.0;
  float lo = (uBands[0] + uBands[1] + uBands[2]) / 3.0;
  vec3 col = mix(cinnamon, saffron, clamp(0.35 + hi * 0.8 - lo * 0.3, 0.0, 1.0));
  col = mix(col, sand, uCool * 0.6);
  vec3 c = col * pool * (0.20 + 0.55 * uLevel);
  // dune haze, drifting
  float n = noise(uv * vec2(3.0 * asp, 3.0) + vec2(uTime * 0.02, -uTime * 0.013)) * 0.6
          + noise(uv * vec2(7.0 * asp, 7.0) - vec2(uTime * 0.03, uTime * 0.01)) * 0.4;
  c += cinnamon * n * n * 0.10 * smoothstep(0.3, 1.0, uv.y);
  // rim from the lower right
  float rim = smoothstep(1.5, 0.2, length(vec2((1.0 - uv.x) * asp, 1.0 - uv.y)));
  c += mix(cinnamon, saffron, 0.4) * rim * rim * 0.07;
  // heat where keys went down
  for (int i = 0; i < 8; i++) {
    vec4 h = uHeat[i];
    if (h.z <= 0.0) continue;
    float hd = length(vec2((uv.x - h.x) * asp, uv.y - h.y));
    float sp = smoothstep(0.08 * (1.6 - 0.6 * h.z), 0.0, hd);
    vec3 hc = h.w > 1.5 ? sand : (h.w > 0.5 ? ember : saffron);
    c += hc * sp * h.z * 0.5;
  }
  c += (hash(gl_FragCoord.xy + fract(uTime) * 17.0) - 0.5) * 0.02;
  c *= 1.0 - 0.45 * uDim;
  frag = vec4(max(c, vec3(0.0)) * uAlpha, 0.0);
}`;

export const Light = (() => {
  const out = { active: false };
  const canvas = $("light");
  if (!canvas) return out;
  if (reduced || !webgl2) { canvas.hidden = true; return out; }
  const gl = canvas.getContext("webgl2", { alpha: true, antialias: false, premultipliedAlpha: true, powerPreference: "low-power" });
  if (!gl) { canvas.hidden = true; return out; }

  const DPR_CAP = 1.5, RES = 0.5;
  let program = null, uni = {}, lost = false, W = 0, H = 0;
  let levelSm = 0, cool = 0, dim = 0, dimTarget = 0, radius = .5, radiusTarget = .5;
  const bandsSm = new Float32Array(8);
  const heat = new Float32Array(32); let heatI = 0;
  const RADII = [.5, .42, .46, .55, .22, .5];

  function compile(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) || "shader");
    return s;
  }
  function build() {
    program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) || "link");
    gl.useProgram(program);
    for (const n of ["uRes", "uTime", "uLevel", "uCool", "uAlpha", "uDim", "uRadius", "uPool", "uBands", "uHeat"])
      uni[n] = gl.getUniformLocation(program, n);
    gl.bindVertexArray(gl.createVertexArray());
    gl.blendFunc(gl.ONE, gl.ONE);
    gl.uniform1f(uni.uAlpha, parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--light-alpha")) || .9);
  }
  function size() {
    const dpr = Math.min(devicePixelRatio || 1, DPR_CAP) * RES;
    W = canvas.width = Math.max(2, Math.round(canvas.clientWidth * dpr));
    H = canvas.height = Math.max(2, Math.round(canvas.clientHeight * dpr));
    gl.viewport(0, 0, W, H);
  }
  function tick(dt, t) {
    if (lost) return;
    const f = Signal.frame();
    const level = Math.min(1, Math.max(f.live ? f.peak : f.peak * 0.35, AudioOut.level()));
    levelSm += (level - levelSm) * Math.min(1, dt * (level > levelSm ? 9 : 2.6));
    const raw = Signal.bands();
    for (let i = 0; i < 8; i++) bandsSm[i] += (raw[i] - bandsSm[i]) * Math.min(1, dt * (raw[i] > bandsSm[i] ? 9 : 2.2));
    cool = Math.max(0, cool - dt * 3);
    dim += (dimTarget - dim) * Math.min(1, dt * 3);
    radius += (radiusTarget - radius) * Math.min(1, dt * 3);
    for (let i = 0; i < 8; i++) heat[i * 4 + 2] = Math.max(0, heat[i * 4 + 2] - dt * 2.2);
    // the pool sits under the board's projected centre
    const s = stage.getBoundingClientRect(), b = Keyboard.boardRect();
    const px = (b.left + b.width / 2 - s.left) / s.width, py = (b.top + b.height * .62 - s.top) / s.height;

    gl.useProgram(program);
    gl.uniform2f(uni.uRes, W, H);
    gl.uniform1f(uni.uTime, t);
    gl.uniform1f(uni.uLevel, levelSm);
    gl.uniform1f(uni.uCool, cool);
    gl.uniform1f(uni.uDim, dim);
    gl.uniform1f(uni.uRadius, radius);
    gl.uniform2f(uni.uPool, px, py);
    gl.uniform1fv(uni.uBands, bandsSm);
    gl.uniform4fv(uni.uHeat, heat);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  try { build(); } catch { canvas.hidden = true; return out; }
  size();
  addEventListener("resize", size);
  canvas.addEventListener("webglcontextlost", (e) => { e.preventDefault(); lost = true; });
  canvas.addEventListener("webglcontextrestored", () => { try { build(); size(); lost = false; } catch { canvas.hidden = true; } });

  const sub = Ticker.add({ el: canvas, fps: 30, fn: tick });
  const on = () => { sub.fps = 60; Ticker.wake(); };
  const off = () => { sub.fps = 30; };
  bus.on("rec:start", on); bus.on("rec:stop", off);
  bus.on("tts:start", on); bus.on("tts:end", off);
  bus.on("flow:freeze", () => { cool = 1; });
  bus.on("key:down", ({ x, y, heat: h }) => {
    heat.set([x, y, 1, h === "user" ? 2 : h === "repair" ? 1 : 0], heatI * 4);
    heatI = (heatI + 1) % 8;
    Ticker.wake();
  });
  bus.on("story:progress", ({ p, index }) => {
    const t = p * 5, i = Math.min(4, Math.floor(t)), f = t - i;
    radiusTarget = RADII[i] + (RADII[i + 1] - RADII[i]) * f;
    dimTarget = index === 3 ? 1 : 0;
  });
  out.active = true;
  return out;
})();
