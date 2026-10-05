// ═══ LAYOUT — a 60% ANSI board, and how text becomes key presses ══════════
// 61 keys, every row 15 units wide. Legends are words, not glyphs (the
// self-hosted mono font is subsetted). c = KeyboardEvent.code, l = legend
// (and the unshifted character when it is one), s = shifted character,
// w = width in units, r = role (mod | edit | consent), sub = small legend.
const K = (c, l, s, extra = {}) => ({ c, l, s, ...extra });
const letter = (ch) => K("Key" + ch.toUpperCase(), ch, ch.toUpperCase());

export const LAYOUT_60 = [
  [K("Backquote", "`", "~"), K("Digit1", "1", "!"), K("Digit2", "2", "@"), K("Digit3", "3", "#"),
   K("Digit4", "4", "$"), K("Digit5", "5", "%"), K("Digit6", "6", "^"), K("Digit7", "7", "&"),
   K("Digit8", "8", "*"), K("Digit9", "9", "("), K("Digit0", "0", ")"), K("Minus", "-", "_"),
   K("Equal", "=", "+"), K("Backspace", "back", null, { w: 2, r: "edit" })],
  [K("Tab", "tab", null, { w: 1.5, r: "mod" }), ..."qwertyuiop".split("").map(letter),
   K("BracketLeft", "[", "{"), K("BracketRight", "]", "}"), K("Backslash", "\\", "|", { w: 1.5 })],
  [K("CapsLock", "caps", null, { w: 1.75, r: "mod" }), ..."asdfghjkl".split("").map(letter),
   K("Semicolon", ";", ":"), K("Quote", "'", '"'),
   K("Enter", "enter", null, { w: 2.25, r: "consent", sub: "yours" })],
  [K("ShiftLeft", "shift", null, { w: 2.25, r: "mod" }), ..."zxcvbnm".split("").map(letter),
   K("Comma", ",", "<"), K("Period", ".", ">"), K("Slash", "/", "?"),
   K("ShiftRight", "shift", null, { w: 2.75, r: "mod" })],
  [K("ControlLeft", "ctrl", null, { w: 1.25, r: "mod" }), K("MetaLeft", "super", null, { w: 1.25, r: "mod", opt: 1 }),
   K("AltLeft", "alt", null, { w: 1.25, r: "mod" }), K("Space", "", " ", { w: 6.25 }),
   K("AltRight", "alt", null, { w: 1.25, r: "mod", opt: 1 }), K("Fn", "fn", null, { w: 1.25, r: "mod", opt: 1 }),
   K("ContextMenu", "menu", null, { w: 1.25, r: "mod", opt: 1 }),
   K("ControlRight", "ctrl", null, { w: 1.25, r: "mod", sub: "kai" })],
];

// char -> { code, shift }
export const CHAR_MAP = (() => {
  const m = new Map();
  for (const row of LAYOUT_60) for (const k of row) {
    if (k.l.length === 1) m.set(k.l, { code: k.c, shift: false });
    if (k.s && k.s.length === 1) m.set(k.s, { code: k.c, shift: k.c !== "Space" });
  }
  m.set(" ", { code: "Space", shift: false });
  return m;
})();
export const codeFor = (ch) => { const m = CHAR_MAP.get(ch); return m ? m.code : null; };

// ops: [{kind:"bs"} | {kind:"ch", ch}] -> presses the board can drain.
// A press: { code, kind: ch|bs|hold|release, ch, heat, ghost }
//   hold/release wrap a Shift chord (consecutive capitals share one hold);
//   "\n" never presses Enter (ghost:"newline" — the window inserts the break);
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
