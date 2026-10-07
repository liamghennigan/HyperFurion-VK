// ═══ TTS — the reverse lane, with word-by-word highlight ══════════════════
// Select text on this page and the chip reads it aloud with your browser's
// speech synthesis. It picks a voice that runs on this device; an online
// voice (Chrome's "Google …" voices) would send the text to its maker, so
// the page uses one only when the browser has nothing else, and the chip
// says so before you tap it.
import { favicon, synth, coarse, baseTitle, FAV_IDLE, FAV_SPK } from "./env.js";
import { bus } from "./bus.js";
import { settings } from "./settings.js";
import { Dictation } from "./dictation.js";

export const TTS = (() => {
  let chip = null;
  // a voice on this device, in the page's language when there is one; null
  // with online=true when the browser only has online voices
  function pickVoice() {
    if (settings.tts.voice) return { voice: settings.tts.voice, online: !settings.tts.voice.localService };
    const all = synth ? synth.getVoices() : [];
    if (!all.length) return { voice: null, online: false };   // not listed (yet): the browser's default
    const lang = String(settings.lang || "en").toLowerCase(), base = lang.slice(0, 2);
    const fits = (v) => String(v.lang || "").toLowerCase().replace("_", "-").startsWith(base);
    const local = all.filter((v) => v.localService);
    const voice = local.find((v) => v.default && fits(v)) || local.find((v) => String(v.lang || "").toLowerCase() === lang) ||
      local.find(fits) || local.find((v) => v.default) || local[0] || null;
    if (voice) return { voice, online: false };
    return { voice: all.find((v) => v.default && fits(v)) || all.find(fits) || all[0], online: true };
  }
  if (synth) { try { synth.getVoices(); } catch {} }  // asking once starts loading the list
  function removeChip() { if (chip) { chip.remove(); chip = null; } }
  function clearHighlight() {
    if (window.Highlight && CSS.highlights) CSS.highlights.delete("vk-tts");
  }
  function textSegments(range) {
    // map absolute offsets in range.toString() -> positions in the live DOM
    const segs = [];
    let abs = 0;
    const root = range.commonAncestorContainer.nodeType === 3
      ? range.commonAncestorContainer.parentNode : range.commonAncestorContainer;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (!range.intersectsNode(n)) continue;
      let s = 0, e = n.data.length;
      if (n === range.startContainer) s = range.startOffset;
      if (n === range.endContainer) e = range.endOffset;
      if (e > s) { segs.push({ node: n, s, abs, len: e - s }); abs += e - s; }
    }
    return segs;
  }
  function highlightWord(segs, from, to) {
    if (!window.Highlight || !CSS.highlights) return;
    try {
      const r = document.createRange();
      let started = false;
      for (const g of segs) {
        const gEnd = g.abs + g.len;
        if (!started && from >= g.abs && from < gEnd) {
          r.setStart(g.node, g.s + (from - g.abs));
          started = true;
        }
        if (started && to > g.abs && to <= gEnd) {
          r.setEnd(g.node, g.s + (to - g.abs));
          CSS.highlights.set("vk-tts", new Highlight(r));
          return;
        }
      }
    } catch { /* highlight is garnish; never let it break speech */ }
  }
  function setSpeaking(on) {
    if (Dictation.recording) return;   // recording state owns the favicon
    document.title = on ? "🔊 speaking — " + baseTitle : baseTitle;
    favicon.href = on ? FAV_SPK : FAV_IDLE;
  }
  function speakSelection(e) {
    if (!synth) return;
    const pick = pickVoice();
    // an online voice only from the chip that says so: the shortcut shows the chip first
    if (pick.online && !(e && e.currentTarget === chip && chip)) { maybeShowChip(); return; }
    const sel = getSelection();
    let raw = sel && sel.rangeCount ? sel.toString() : "";
    let range = raw.trim() ? sel.getRangeAt(0).cloneRange() : null;
    if (!range && chipRange) {
      // touch: tapping the chip can collapse the selection first — the
      // chip remembered what you had selected
      range = chipRange;
      raw = chipText;
    }
    if (!raw.trim() || !range) return;
    synth.cancel();
    clearHighlight();
    const segs = textSegments(range);
    const u = new SpeechSynthesisUtterance(raw);
    u.rate = settings.tts.rate;
    u.pitch = settings.tts.pitch;
    if (pick.voice) u.voice = pick.voice;
    u.onstart = () => { bus.emit("tts:start"); setSpeaking(true); };
    u.onboundary = (ev) => {
      if (ev.name && ev.name !== "word") return;
      bus.emit("tts:word", {});
      const rest = u.text.slice(ev.charIndex);
      const m = rest.match(/^\s*\S+/);
      if (!m) return;
      const lead = m[0].length - m[0].trimStart().length;
      highlightWord(segs, ev.charIndex + lead, ev.charIndex + m[0].length);
    };
    const end = () => { clearHighlight(); bus.emit("tts:end"); setSpeaking(false); };
    u.onend = end;
    u.onerror = end;
    if (pick.online) bus.emit("tts:online", {});   // the footer counts it: the text goes to the voice's maker
    synth.speak(u);
    removeChip();
  }
  function maybeShowChip() {
    removeChip();
    if (!synth) return;
    const sel = getSelection();
    const text = sel ? sel.toString().trim() : "";
    if (text.length < 3 || sel.rangeCount === 0) return;
    const r = sel.getRangeAt(0).getBoundingClientRect();
    if (!r.width && !r.height) return;
    chipRange = sel.getRangeAt(0).cloneRange();
    chipText = sel.toString();
    chip = document.createElement("button");
    chip.className = "ttschip";
    chip.type = "button";
    const online = pickVoice().online;
    chip.textContent = (coarse ? "read aloud" : "read aloud · ctrl+alt+t") + (online ? " · online voice: the text leaves this tab" : "");
    chip.style.top = (r.bottom + scrollY + (coarse ? 14 : 6)) + "px";
    chip.addEventListener("mousedown", (e) => e.preventDefault());
    chip.addEventListener("pointerdown", (e) => e.preventDefault());
    chip.addEventListener("click", speakSelection);
    document.body.appendChild(chip);
    chip.style.left = Math.max(8, Math.min(r.left + scrollX, scrollX + innerWidth - chip.offsetWidth - 8)) + "px";
  }
  let chipRange = null, chipText = "", selT = 0;
  document.addEventListener("mouseup", () => setTimeout(maybeShowChip, 0));
  document.addEventListener("selectionchange", () => {
    const sel = getSelection();
    if (!sel || !sel.toString().trim()) {
      // grace period: on touch, tapping the chip collapses the selection
      // a beat before the click lands — don't yank the chip out from
      // under the finger
      clearTimeout(selT);
      selT = setTimeout(() => { removeChip(); chipRange = null; chipText = ""; }, 400);
      return;
    }
    // touch selection never fires mouseup — show the chip once the
    // selection handles settle
    if (coarse) {
      clearTimeout(selT);
      selT = setTimeout(maybeShowChip, 350);
    }
  });
  return { speakSelection, clearHighlight };
})();
