// ═══ SPELLING — "spell that n g i n x", letter by letter ═══════════════════
// A port of voice_keyboard/flow/spelling.py. Recognizers hand spelled
// letters back in several shapes — single-letter tokens ("N G I N X"),
// one hyphenated token ("N-G-I-N-X"), one capitalized token ("NGINX"),
// NATO words ("november golf …") —
// and a letter can be marked upper case with "capital". lettersAt reads
// one letter (or a hyphenated run) at a token index; the grammar strings
// them together. Pure functions: the parse stays deterministic.

export const MAX_SPELLED_LETTERS = 32;

export const NATO = {
  alpha: "a", alfa: "a", bravo: "b", charlie: "c", delta: "d", echo: "e",
  foxtrot: "f", golf: "g", hotel: "h", india: "i", juliet: "j", juliett: "j",
  kilo: "k", lima: "l", mike: "m", november: "n", oscar: "o", papa: "p",
  quebec: "q", romeo: "r", sierra: "s", tango: "t", uniform: "u", victor: "v",
  whiskey: "w", whisky: "w", "x-ray": "x", xray: "x", yankee: "y", zulu: "z",
};

// spoken single digits inside a spelled word ("k eight s" -> k8s)
export const DIGITS = {
  zero: "0", one: "1", two: "2", three: "3", four: "4",
  five: "5", six: "6", seven: "7", eight: "8", nine: "9",
};

export const CAPITAL = new Set(["capital", "cap", "uppercase", "upper"]);

const STRIP = /^[.,!?;:]+|[.,!?;:]+$/g;
const strip = (t) => t.toLowerCase().replace(STRIP, "");

// one spelled symbol from a lowercased, punctuation-stripped token
function one(core) {
  if (core.length === 1 && /[a-z0-9]/.test(core)) return core;
  if (core in NATO) return NATO[core];
  return DIGITS[core] || null;
}

// The letters spelled starting at `index`, and how many tokens they used;
// ["", 0] when the token there is not a spelled letter. A "capital" prefix
// upper-cases the next letter (it needs one: a bare trailing "capital" is
// not a letter). A hyphenated token spells every part ("N-G-I-N-X").
export function lettersAt(tokens, index) {
  if (index >= tokens.length) return ["", 0];
  const core = strip(tokens[index]);
  if (CAPITAL.has(core)) {
    if (index + 1 >= tokens.length) return ["", 0];
    const letter = one(strip(tokens[index + 1]));
    if (letter === null || !/[a-z]/.test(letter)) return ["", 0];
    return [letter.toUpperCase(), 2];
  }
  const letter = one(core);
  if (letter !== null) return [letter, 1];
  if (core.includes("-") && !(core in NATO)) {
    const parts = core.split("-");
    const spelled = parts.map(one);
    if (parts.length > 1 && spelled.every(Boolean)) return [spelled.join(""), 1];
  }
  // a recognizer that heard the letters as one word writes it in capitals
  // ("NGINX", "K8S"): those are the letters, spelled
  const raw = tokens[index].replace(STRIP, "");
  if (raw.length >= 2 && /^[A-Z0-9]+$/.test(raw) && /[A-Z]/.test(raw)) return [raw.toLowerCase(), 1];
  return ["", 0];
}

// a trailing "capital" might still get its letter in the next update
export const couldBeCapital = (token) => CAPITAL.has(strip(token));
