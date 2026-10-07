// ═══ LAYOUT — a 75% board, and how text becomes key presses ══════════════
// 82 keys in six rows, every row 16 units wide: the 60% core, an F-row,
// a right-hand column (del · home · end · pgup · pgdn) and an arrow
// cluster — the keys hands-free navigation presses. Legends are words,
// not glyphs (the self-hosted mono font is subsetted). c = KeyboardEvent
// .code, l = legend (and the unshifted character when it is one),
// s = shifted character, w = width in units, r = role (mod | edit |
// consent), sub = small legend, gap = empty units between key groups.
const K = (c, l, s, extra = {}) => ({ c, l, s, ...extra });
const letter = (ch) => K("Key" + ch.toUpperCase(), ch, ch.toUpperCase());
const mod = (c, l, w = 1, extra = {}) => K(c, l, null, { w, r: "mod", ...extra });
const fkeys = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => mod("F" + (from + i), "f" + (from + i), 1, { opt: 1 }));
const GAP = { gap: 0.5 };

export const LAYOUT_75 = [
  [mod("Escape", "esc", 1, { opt: 1 }), GAP, ...fkeys(1, 4), GAP, ...fkeys(5, 8), GAP, ...fkeys(9, 12), GAP,
   K("Delete", "del", null, { r: "edit" })],
  [K("Backquote", "`", "~"), K("Digit1", "1", "!"), K("Digit2", "2", "@"), K("Digit3", "3", "#"),
   K("Digit4", "4", "$"), K("Digit5", "5", "%"), K("Digit6", "6", "^"), K("Digit7", "7", "&"),
   K("Digit8", "8", "*"), K("Digit9", "9", "("), K("Digit0", "0", ")"), K("Minus", "-", "_"),
   K("Equal", "=", "+"), K("Backspace", "back", null, { w: 2, r: "edit" }), mod("Home", "home")],
  [mod("Tab", "tab", 1.5), ..."qwertyuiop".split("").map(letter),
   K("BracketLeft", "[", "{"), K("BracketRight", "]", "}"), K("Backslash", "\\", "|", { w: 1.5 }), mod("End", "end")],
  [mod("CapsLock", "caps", 1.75), ..."asdfghjkl".split("").map(letter),
   K("Semicolon", ";", ":"), K("Quote", "'", '"'),
   K("Enter", "enter", null, { w: 2.25, r: "consent", sub: "yours" }), mod("PageUp", "pgup")],
  [mod("ShiftLeft", "shift", 2.25), ..."zxcvbnm".split("").map(letter),
   K("Comma", ",", "<"), K("Period", ".", ">"), K("Slash", "/", "?"),
   mod("ShiftRight", "shift", 1.75), mod("ArrowUp", "up"), mod("PageDown", "pgdn")],
  [mod("ControlLeft", "ctrl", 1.25), mod("MetaLeft", "super", 1.25, { opt: 1 }),
   mod("AltLeft", "alt", 1.25), K("Space", "", " ", { w: 6.25 }),
   mod("AltRight", "alt", 1, { opt: 1 }), mod("Fn", "fn", 1, { opt: 1 }),
   mod("ControlRight", "ctrl", 1, { sub: "kai" }),
   mod("ArrowLeft", "left"), mod("ArrowDown", "down"), mod("ArrowRight", "right")],
];
export const LAYOUT_60 = LAYOUT_75;  // the name older modules import
export const UNITS_WIDE = 16;

// char -> { code, shift }
export const CHAR_MAP = (() => {
  const m = new Map();
  for (const row of LAYOUT_75) for (const k of row) {
    if (k.gap) continue;
    if (k.l.length === 1) m.set(k.l, { code: k.c, shift: false });
    if (k.s && k.s.length === 1) m.set(k.s, { code: k.c, shift: k.c !== "Space" });
  }
  m.set(" ", { code: "Space", shift: false });
  m.set("\t", { code: "Tab", shift: false });
  return m;
})();
export const codeFor = (ch) => { const m = CHAR_MAP.get(ch); return m ? m.code : null; };

// the daemon's key names (nav.py / injector KEY_NAMES) -> KeyboardEvent.code
export const KEY_CODES = {
  ctrl: "ControlLeft", control: "ControlLeft", shift: "ShiftLeft", alt: "AltLeft", option: "AltLeft",
  super: "MetaLeft", meta: "MetaLeft", win: "MetaLeft", cmd: "MetaLeft", command: "MetaLeft",
  tab: "Tab", esc: "Escape", escape: "Escape", space: "Space", backspace: "Backspace",
  delete: "Delete", del: "Delete", up: "ArrowUp", down: "ArrowDown", left: "ArrowLeft",
  right: "ArrowRight", home: "Home", end: "End", pageup: "PageUp", pagedown: "PageDown",
  enter: "Enter", return: "Enter",
};
export function codeForKeyName(name) {
  const n = String(name).toLowerCase();
  if (KEY_CODES[n]) return KEY_CODES[n];
  if (/^f([1-9]|1[0-2])$/.test(n)) return n.toUpperCase();
  if (n.length === 1) return codeFor(n);
  return null;
}
export const MODIFIER_NAMES = new Set(["ctrl", "control", "shift", "alt", "option", "super", "meta", "win", "cmd", "command"]);

// ops: [{kind:"bs"} | {kind:"ch", ch}] -> presses the board can drain.
// A press: { code, kind: ch|bs|hold|release|key, ch, heat, ghost }
//   hold/release wrap a Shift chord (consecutive capitals share one hold);
//   "\n" is a ghost (ghost:"newline"): the window inserts the break and the
//   board only lights Enter, because this board never pushes Enter. The
//   daemon does press a key for it — Enter in an editor or a document,
//   Shift+Enter in a chat or a web page — and nothing in a terminal or a
//   one-line field, where Enter would run or submit (the grammar drops it
//   in a terminal register before it gets here);
//   anything the board has no key for is what the daemon pastes on Linux
//   (full Unicode via clipboard) — shown as a brief Ctrl+V chord.
export function toPresses(ops, { heat = "molten" } = {}) {
  const out = [];
  let shift = false;
  const setShift = (on) => {
    if (on === shift) return;
    out.push({ code: "ShiftLeft", kind: on ? "hold" : "release" });
    shift = on;
  };
  for (const op of ops) {
    if (op.kind === "bs") { setShift(false); out.push({ code: "Backspace", kind: "bs", heat: "repair" }); continue; }
    const ch = op.ch;
    if (ch === "\n") { setShift(false); out.push({ code: null, kind: "ch", ch, ghost: "newline", heat }); continue; }
    const m = CHAR_MAP.get(ch);
    if (!m) {
      setShift(false);
      out.push({ code: "ControlLeft", kind: "hold" });
      out.push({ code: "KeyV", kind: "ch", ch, ghost: "paste", heat });
      out.push({ code: "ControlLeft", kind: "release" });
      continue;
    }
    setShift(m.shift);
    out.push({ code: m.code, kind: "ch", ch, heat });
  }
  setShift(false);
  return out;
}

// a navigation chord -> presses: hold the modifiers, tap the key, let go.
// No caret command presses Enter, here or in the daemon (nav.js refuses
// any chord with it); should one name it anyway, it taps nothing and the
// board only lights the key.
export function chordToPresses(names, { heat = "nav", onDown = null } = {}) {
  const mods = names.filter((n) => MODIFIER_NAMES.has(String(n).toLowerCase()));
  const key = names.find((n) => !MODIFIER_NAMES.has(String(n).toLowerCase()));
  const out = [];
  for (const m of mods) out.push({ code: codeForKeyName(m), kind: "hold" });
  const code = key ? codeForKeyName(key) : null;
  out.push({ code: code === "Enter" ? null : code, kind: "key", key, heat, onDown, ghost: code === "Enter" ? "enter" : null });
  for (const m of [...mods].reverse()) out.push({ code: codeForKeyName(m), kind: "release" });
  return out;
}
