// ═══ MEASURE — the colophon: real size, measured, not claimed ═════════════
// Counts the page's own files and bytes, and every file the page asked
// another server for, as the browser itself reports them (Resource Timing,
// watched live, so the footer updates while files arrive). The browser
// lists one entry per file, with any redirects folded into it, so the
// footer counts files, not network round trips, and names the servers
// that answered after a redirect too (Hugging Face sends its downloads on
// to its CDN; stt-local.js reports those hosts). Nothing goes to another
// server until you do something, and the footer names what you did: start
// a dictation (the speech model and its runtime, from jsDelivr and Hugging
// Face, fetched once the mic is granted), or press a button in the
// hosted-engine sheet (a status check, a question, a voice line). What the
// browser doesn't list is counted from the page's own events: a dictation
// streamed to the hosted engine over a WebSocket, a dictation through your
// browser's own speech service, and a selection read aloud by a voice that
// is, or may be, online — the last two go wherever the browser sends them.
import { $, reduced } from "./env.js";
import { bus } from "./bus.js";

(() => {
  const bytesEl = $("bytes"), fetchedEl = $("fetched");
  if (!bytesEl) return;
  let htmlBytes = 0;
  try {
    const nav = performance.getEntriesByType("navigation")[0];
    htmlBytes = (nav && (nav.transferSize || nav.decodedBodySize)) ||
      ("<!doctype html>" + document.documentElement.outerHTML).length;
  } catch {}
  try { performance.setResourceTimingBufferSize(2000); } catch {}

  // what you did that sends something: set by the page's own events
  const did = { mic: 0, status: false, ask: false, tts: false };   // mic: when you first started a dictation
  const streamed = { relay: 0, browser: 0 };
  bus.on("rec:start", ({ engine } = {}) => {
    if (engine === "local" && !did.mic) did.mic = performance.now();
    if (engine === "relay") streamed.relay++;
    if (engine === "browser") streamed.browser++;
    schedule();
  });
  bus.on("relay:asked", ({ what } = {}) => { if (what in did) did[what] = true; schedule(); });
  // read-aloud through an online voice, or the browser's default voice
  // when it lists none (which may be online) — the chip said so before you tapped it
  let readOnline = 0, readMaybe = 0;
  bus.on("tts:online", ({ maybe } = {}) => { if (maybe) readMaybe++; else readOnline++; schedule(); });
  // the hosts that finally answered the model's downloads, after redirects
  const answered = new Set();
  bus.on("stt:host", ({ host } = {}) => { if (host) { answered.add(host); schedule(); } });

  // every request to another origin, grouped by what it was for
  const GROUPS = [
    { key: "model", test: (u) => u.hostname === "cdn.jsdelivr.net" || /(^|\.)(huggingface\.co|hf\.co)$/.test(u.hostname) },
    { key: "status", test: (u) => /\/v1\/demo\/status$/.test(u.pathname) },
    { key: "ask", test: (u) => /\/v1\/demo\/ask$/.test(u.pathname) },
    { key: "tts", test: (u) => /\/v1\/demo\/tts$/.test(u.pathname) },
  ];
  function measure() {
    let own = 0, ownBytes = 0;
    const count = { model: 0, status: 0, ask: 0, tts: 0, other: 0 }, hosts = { model: new Set(), relay: new Set(), other: new Set() };
    let cached = 0, modelBeforeMic = 0;
    try {
      for (const r of performance.getEntriesByType("resource")) {
        let u;
        try { u = new URL(r.name); } catch { continue; }
        if (u.origin === location.origin || u.protocol === "data:" || u.protocol === "blob:") {
          if (u.origin === location.origin) { own++; ownBytes += r.transferSize || r.decodedBodySize || 0; }
          continue;
        }
        const g = GROUPS.find((x) => x.test(u));
        const key = g ? g.key : "other";
        count[key]++;
        if (key === "model" && !(did.mic && r.startTime >= did.mic - 50)) modelBeforeMic++;
        (key === "model" ? hosts.model : key === "other" ? hosts.other : hosts.relay).add(u.hostname);
        // the browser answered it from its own cache: nothing reached the server
        // (only measurable when the server allows timing details)
        if (r.transferSize === 0 && r.decodedBodySize > 0) cached++;
      }
    } catch {}
    return { files: own + 1, kb: Math.round((htmlBytes + ownBytes) / 1024), count, hosts, cached, modelBeforeMic };
  }

  const n = (k, one, many = one + "s") => k + " " + (k === 1 ? one : many);
  const list = (set) => { const a = [...set]; return a.length < 2 ? a.join("") : a.slice(0, -1).join(", ") + " and " + a[a.length - 1]; };
  function sentence(m) {
    const parts = [];
    const c = m.count;
    if (c.model) {
      const onward = new Set([...answered].filter((h) => !m.hosts.model.has(h)));
      parts.push("requests for " + n(c.model, "file") + " of the speech model and its runtime, to " + list(m.hosts.model) +
        (onward.size ? " (some redirected on to " + list(onward) + ")" : "") +
        (!m.modelBeforeMic ? ", because you started a dictation" : ""));
    }
    const relayHost = list(m.hosts.relay) || "the relay";
    if (c.status) parts.push(n(c.status, "status check") + " to " + relayHost + (did.status ? ", because you pressed “check the relay”" : ""));
    if (c.ask) parts.push(n(c.ask, "question") + " to " + relayHost + " (on to xAI)" + (did.ask ? ", because you pressed “ask”" : ""));
    if (c.tts) parts.push(n(c.tts, "voice-line request") + " to " + relayHost + " (on to xAI)" + (did.tts ? ", because you asked to hear it" : ""));
    if (c.other) parts.push(n(c.other, "request") + " to " + list(m.hosts.other));
    if (streamed.relay) parts.push(n(streamed.relay, "dictation") + " streamed to the hosted engine over a WebSocket (on to xAI), because you switched it on");
    if (streamed.browser) parts.push(n(streamed.browser, "dictation") + " through your browser's speech service, which you picked — it may send your audio to its maker's servers");
    if (readOnline) parts.push(n(readOnline, "selection") + " read aloud by your browser's online voice, which you picked — the text went to its maker");
    if (readMaybe) parts.push(n(readMaybe, "selection") + " read aloud by your browser's default voice, which you picked though it may be online — if so, the text went to its maker");
    if (!parts.length) return "";
    return "Sent from this page to other servers so far: " + parts.join(" · ") +
      (m.cached ? " (" + m.cached + " of these " + (m.cached === 1 ? "requests was" : "were") + " answered from your browser's cache and never left it)" : "") + ".";
  }
  const foreignOf = (m) => Object.values(m.count).reduce((a, b) => a + b, 0) + streamed.relay + streamed.browser + readOnline + readMaybe;
  function paint(m, kb) {
    bytesEl.textContent = n(m.files, "file") + " · " + kb + " KB · self-hosted" + (foreignOf(m) ? "" : " · no third-party requests");
    if (fetchedEl) {
      const s = sentence(m);
      fetchedEl.hidden = !s;
      fetchedEl.textContent = s;
    }
  }

  // repaint on every new resource, at most once per animation frame
  let rolling = false, pending = 0;
  function schedule() {
    if (pending) return;
    pending = requestAnimationFrame(() => { pending = 0; if (!rolling) { const m = measure(); paint(m, m.kb); } });
  }
  try {
    new PerformanceObserver(schedule).observe({ type: "resource", buffered: true });
  } catch {
    // no live resource timing here: repaint whenever the page knows it asked for something
    bus.on("stt:progress", schedule);
  }
  bus.on("relay:request", schedule);
  { const m = measure(); paint(m, m.kb); }

  // the byte count rolls up once, when the footer first comes into view
  if (reduced || !("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver((es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    io.disconnect();
    const t0 = performance.now();
    rolling = true;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / 900), m = measure();
      paint(m, Math.round(m.kb * k * (2 - k)));
      if (k < 1) requestAnimationFrame(step);
      else { rolling = false; schedule(); }
    };
    requestAnimationFrame(step);
  });
  io.observe(bytesEl);
})();
