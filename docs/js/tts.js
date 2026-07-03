// ═══ TTS — the reverse lane, with word-by-word highlight ══════════════════
import { favicon, synth, coarse, baseTitle, FAV_IDLE, FAV_SPK } from "./env.js";
import { bus } from "./bus.js";
import { Config } from "./config.js";
import { Dictation } from "./dictation.js";

export const TTS = (() => {
  let chip = null;
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
  function speakSelection() {
    if (!synth) return;
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
    u.rate = Config.cfg.rate;
    u.pitch = Config.cfg.pitch;
    if (Config.cfg.voice) u.voice = Config.cfg.voice;
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
    chip.textContent = coarse ? "🔊 read aloud" : "🔊 voice-keyboard tts";
    chip.style.left = Math.min(
      Math.max(8, r.left + scrollX), scrollX + innerWidth - 170
    ) + "px";
    chip.style.top = (r.bottom + scrollY + (coarse ? 14 : 6)) + "px";
    chip.addEventListener("mousedown", (e) => e.preventDefault());
    chip.addEventListener("pointerdown", (e) => e.preventDefault());
    chip.addEventListener("click", speakSelection);
    document.body.appendChild(chip);
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
