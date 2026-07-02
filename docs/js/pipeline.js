// ═══ PIPELINE — 01 / signal path, lit by the real lifecycle ═══════════════
import { $, reduced } from "./env.js";
import { bus } from "./bus.js";
import { Ticker } from "./ticker.js";
import { Signal } from "./signal.js";
import { Dictation } from "./dictation.js";

export const Pipeline = (() => {
  const fig = $("pipeline"), status = $("pl-status"), engineSub = $("pl-engine");
  const liveLayer = $("pl-live-layer");
  const eMic = $("pl-e1"), eProv = $("pl-e2"), eRet = $("pl-e3"), eType = $("pl-e4");
  const ENGINE_IDLE = engineSub.textContent;
  let flow = 0, packets = [], rainQ = [], rainN = 0, rainT = 0;

  function tick(dt) {
    if (fig.classList.contains("rec")) {
      const f = Signal.frame();
      flow -= (40 + f.peak * 160) * dt;
      eMic.style.strokeDashoffset = flow.toFixed(1);
      eProv.style.strokeDashoffset = flow.toFixed(1);
    }
    // packets along daemon -> provider -> daemon
    for (const p of packets) {
      p.t += dt * 2.2;
      const path = p.t < 1 ? eProv : eRet;
      const u = p.t < 1 ? p.t : Math.min(1, p.t - 1);
      try {
        const L = path.getTotalLength();
        const pt = path.getPointAtLength(u * L);
        p.el.setAttribute("cx", pt.x); p.el.setAttribute("cy", pt.y);
      } catch {}
      if (p.t >= 2) { p.el.remove(); p.dead = true; }
    }
    packets = packets.filter((p) => !p.dead);
    // keystroke rain along uinput -> app
    rainT += dt;
    if (rainQ.length && rainN < 8 && rainT > .08) { rainT = 0; spawnGlyph(rainQ.shift()); }
    for (const g of rain) {
      g.t += dt / .8;
      const x = 585 + g.t * 55, y = 85 + Math.pow(g.t, 2) * 26;
      g.el.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
      g.el.setAttribute("opacity", String(Math.max(0, 1 - g.t)));
      if (g.t >= 1) { g.el.remove(); g.dead = true; rainN--; }
    }
    rain = rain.filter((g) => !g.dead);
  }
  let rain = [];
  function spawnGlyph(ch) {
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.textContent = ch;
    liveLayer.appendChild(t);
    const g = { el: t, t: 0 };
    liveLayer.classList.add("pl-rain");
    rain.push(g); rainN++;
  }
  if (!reduced) Ticker.add({ el: fig, fn: tick });

  bus.on("rec:start", () => {
    fig.classList.add("rec");
    engineSub.textContent = Dictation.engine === "relay"
      ? "xai grok stt (hosted)" : "browser speech engine";
    status.textContent = "capturing audio";
  });
  bus.on("rec:interim", () => {
    status.textContent = "transcript arriving";
    if (!reduced && packets.length < 4) {
      const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      c.setAttribute("r", "3.2"); c.setAttribute("class", "pl-packet");
      liveLayer.appendChild(c);
      packets.push({ el: c, t: 0 });
    }
  });
  bus.on("rec:stop", () => {
    setTimeout(() => { fig.classList.remove("rec"); engineSub.textContent = ENGINE_IDLE; }, 400);
  });
  bus.on("type:text", ({ text }) => {
    status.textContent = "typing " + text.length + " characters";
    setTimeout(() => { status.textContent = "idle"; }, 1500);
    if (!reduced) rainQ.push(...text.replace(/\s+/g, " ").slice(0, 40));
  });
  bus.on("tts:start", () => { fig.classList.add("speak"); });
  bus.on("tts:end", () => { fig.classList.remove("speak", "word"); });
  let wordT = 0;
  bus.on("tts:word", () => {
    const out = fig.querySelector(".t-out");
    out.classList.add("word");
    clearTimeout(wordT);
    wordT = setTimeout(() => out.classList.remove("word"), 120);
  });
})();
