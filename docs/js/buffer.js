// ═══ BUFFER — the focused app's text field, as the keys see it ════════════
// A plain text buffer with a caret and a selection anchor: the thing a
// keystroke injector types into. It knows nothing about dictation. Every
// change arrives as a key would — a character, Backspace, a chord — and
// the buffer reacts the way the app would: an editor (notes.md, main.py)
// moves by words and extends selections with Shift; a shell's line editor
// (readline) answers alt+b, ctrl+a, ctrl+w and has no selection at all.
// Pure logic, no DOM: the window paints it, the tests drive it.
const isWord = (c) => /[\p{L}\p{N}_]/u.test(c);
const isSpace = (c) => /\s/.test(c);
const isAlnum = (c) => /[\p{L}\p{N}]/u.test(c);

// editor words: a run of word characters, or a run of punctuation
export function wordLeft(text, i) {
  while (i > 0 && isSpace(text[i - 1])) i--;
  if (i > 0) {
    if (isWord(text[i - 1])) while (i > 0 && isWord(text[i - 1])) i--;
    else while (i > 0 && !isWord(text[i - 1]) && !isSpace(text[i - 1])) i--;
  }
  return i;
}
export function wordRight(text, i) {
  const n = text.length;
  while (i < n && isSpace(text[i])) i++;
  if (i < n) {
    if (isWord(text[i])) while (i < n && isWord(text[i])) i++;
    else while (i < n && !isWord(text[i]) && !isSpace(text[i])) i++;
  }
  return i;
}
// readline words: runs of letters and digits (alt+b / alt+f / alt+d)
export function rlWordLeft(text, i) {
  while (i > 0 && !isAlnum(text[i - 1])) i--;
  while (i > 0 && isAlnum(text[i - 1])) i--;
  return i;
}
export function rlWordRight(text, i) {
  const n = text.length;
  while (i < n && !isAlnum(text[i])) i++;
  while (i < n && isAlnum(text[i])) i++;
  return i;
}
// readline's unix-word-rubout (ctrl+w): back over spaces, then to a space
export function rlRubout(text, i) {
  while (i > 0 && isSpace(text[i - 1])) i--;
  while (i > 0 && !isSpace(text[i - 1])) i--;
  return i;
}
export const lineStart = (text, i) => text.lastIndexOf("\n", i - 1) + 1;
export const lineEnd = (text, i) => { const k = text.indexOf("\n", i); return k === -1 ? text.length : k; };

export function createBuffer({ terminal = false, max = 1600 } = {}) {
  let text = "", caret = 0, anchor = 0;
  let meta = false;  // a terminal's Escape prefix, pending for the next key
  const B = {};
  Object.defineProperties(B, {
    text: { get: () => text }, caret: { get: () => caret }, anchor: { get: () => anchor },
    terminal: { get: () => terminal }, length: { get: () => text.length },
  });
  const sel = () => (anchor === caret ? null : [Math.min(anchor, caret), Math.max(anchor, caret)]);
  B.selection = sel;
  B.selected = () => { const s = sel(); return s ? text.slice(s[0], s[1]) : ""; };

  function splice(a, b, insert) {
    text = text.slice(0, a) + insert + text.slice(b);
    caret = anchor = a + insert.length;
    trim();
  }
  function deleteSelection() {
    const s = sel();
    if (!s) return false;
    splice(s[0], s[1], "");
    return true;
  }
  // bound the document: drop whole lines (or words) off the top, shifting
  // the caret with the text — the window shows the bottom anyway
  function trim() {
    if (text.length <= max) return;
    let cut = text.indexOf("\n", text.length - max);
    if (cut === -1 || cut > text.length - max / 2) cut = text.indexOf(" ", text.length - max);
    if (cut === -1) cut = text.length - max;
    cut += 1;
    text = text.slice(cut);
    caret = Math.max(0, caret - cut); anchor = Math.max(0, anchor - cut);
  }

  B.insert = (ch) => {
    if (ch === "\t" && terminal) return;  // completion, not a character
    deleteSelection();
    splice(caret, caret, ch);
  };
  B.backspace = () => {
    if (deleteSelection()) return;
    if (caret > 0) splice(caret - 1, caret, "");
  };
  B.clear = () => { text = ""; caret = anchor = 0; };
  B.setCaret = (i, { extend = false } = {}) => {
    caret = Math.max(0, Math.min(text.length, i));
    if (!extend) anchor = caret;
  };
  B.set = (t, c = t.length) => { text = t; caret = anchor = Math.max(0, Math.min(t.length, c)); };

  // ── a chord, as the app would take it ─────────────────────────────────
  // returns true when the app did something with it
  B.press = (names) => {
    const set = new Set(names.map((n) => String(n).toLowerCase()));
    const ctrl = set.has("ctrl") || set.has("control"), shift = set.has("shift");
    const alt = set.has("alt") || set.has("option"), cmd = set.has("cmd") || set.has("command") || set.has("super") || set.has("meta");
    const key = names.map((n) => String(n).toLowerCase())
      .find((n) => !["ctrl", "control", "shift", "alt", "option", "super", "meta", "cmd", "command"].includes(n));
    if (!key || key === "enter" || key === "return") return false;
    const move = (to) => { B.setCaret(to, { extend: shift && !terminal }); return true; };
    const kill = (a, b) => { if (a < b) splice(a, b, ""); return true; };
    if (terminal) {
      // readline / zsh emacs keys — no selection to extend; an Escape
      // prefix is Meta for the key that follows it
      if (key === "escape") { meta = true; return true; }
      const wasMeta = meta; meta = false;
      if ((alt || wasMeta) && key === "b") return move(rlWordLeft(text, caret));
      if ((alt || wasMeta) && key === "f") return move(rlWordRight(text, caret));
      if ((alt || wasMeta) && key === "d") return kill(caret, rlWordRight(text, caret));
      if (ctrl && key === "a") return move(lineStart(text, caret));
      if (ctrl && key === "e") return move(lineEnd(text, caret));
      if (ctrl && key === "w") return kill(rlRubout(text, caret), caret);
      if (ctrl && key === "u") return kill(lineStart(text, caret), caret);
      if (ctrl && key === "k") return kill(caret, lineEnd(text, caret));
      if (key === "left") return move(ctrl ? rlWordLeft(text, caret) : caret - 1);
      if (key === "right") return move(ctrl ? rlWordRight(text, caret) : caret + 1);
      if (key === "home") return move(lineStart(text, caret));
      if (key === "end") return move(lineEnd(text, caret));
      if (key === "backspace") return kill(ctrl ? rlRubout(text, caret) : caret - 1, caret);
      if (key === "delete") return kill(caret, ctrl ? rlWordRight(text, caret) : caret + 1);
      return false;  // tab (completion), escape, history keys: nothing to show
    }
    // an editor — on a Mac, option moves by word and command to the ends
    if ((ctrl || cmd) && key === "a") { anchor = 0; caret = text.length; return true; }
    if (cmd && key === "up") return move(0);
    if (cmd && key === "down") return move(text.length);
    if (key === "left") return move(cmd ? lineStart(text, caret) : ctrl || alt ? wordLeft(text, caret) : sel() && !shift ? sel()[0] : caret - 1);
    if (key === "right") return move(cmd ? lineEnd(text, caret) : ctrl || alt ? wordRight(text, caret) : sel() && !shift ? sel()[1] : caret + 1);
    if (key === "home") return move(ctrl ? 0 : lineStart(text, caret));
    if (key === "end") return move(ctrl ? text.length : lineEnd(text, caret));
    if (key === "pageup") return move(0);
    if (key === "pagedown") return move(text.length);
    if (key === "up" || key === "down") {
      const ls = lineStart(text, caret), col = caret - ls;
      if (key === "up") {
        if (ls === 0) return move(0);
        const ps = lineStart(text, ls - 1);
        return move(Math.min(ps + col, ls - 1));
      }
      const le = lineEnd(text, caret);
      if (le >= text.length) return move(text.length);
      return move(Math.min(le + 1 + col, lineEnd(text, le + 1)));
    }
    if (key === "backspace") { if (deleteSelection()) return true; return kill(ctrl || alt ? wordLeft(text, caret) : caret - 1, caret); }
    if (key === "delete") { if (deleteSelection()) return true; return kill(caret, ctrl || alt ? wordRight(text, caret) : caret + 1); }
    if (key === "tab") { B.insert("\t"); return true; }
    if (key === "escape") { anchor = caret; return true; }
    if (key === "space") { B.insert(" "); return true; }
    return false;
  };
  return B;
}
