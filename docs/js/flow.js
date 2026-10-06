// ═══ FLOW — the molten dictation engine, scaled to one tab ════════════════
// A faithful JS port of the daemon's flow pipeline (voice_keyboard/flow/):
// the same spoken grammar, the same punctuation table, the same register
// fold, the same molten/frozen mechanic. Words arrive molten, repair in
// place while the transcript firms up, and freeze once they survive the
// stability window. Frozen output is append-only — the page treats it the
// way the daemon treats keystrokes already typed into the field. Pure
// logic, no DOM; the demo wires it to the windows the way the daemon wires
// it to uinput.

import { MAX_SPELLED_LETTERS, lettersAt, couldBeCapital } from "./spelling.js";
import { VERBS as NAV_VERBS, PENDING as NAV_PENDING, parseNav, GLUED, FRESH_FIELD } from "./nav.js";
import * as pauses from "./pauses.js";

// ── the spoken grammar (grammar.py, verbatim defaults) ────────────────────
const PUNCT_STRIP = /[.,!?;:]+$/;
const core = (t) => t.toLowerCase().replace(PUNCT_STRIP, "");

const COMMANDS = {
  "scratch that": "scratch", "scratched that": "scratch", "scratch this": "scratch",
  "strike that": "scratch", "delete that": "scratch", "undo that": "scratch",
  "new line": "\n", "new paragraph": "\n\n",
};
// the wake word as a speech model is likely to write it
const WAKE_ALIASES = ["vk", "v.k.", "vk.", "vicky", "vikki", "veekay", "veek"];
// returns how many tokens at i spell the wake word (0 = none)
export function wakeAt(cores, i, wake) {
  const w = (wake || "vk").toLowerCase();
  const c = (cores[i] || "").replace(/\./g, "");
  if (c === w.replace(/\./g, "") || WAKE_ALIASES.some((a) => a.replace(/\./g, "") === c)) return 1;
  if (c === "v" && (cores[i + 1] || "").replace(/\./g, "") === "k") return 2;
  return 0;
}
// phrase -> [glyph, mode, sentenceEnd]; modes: left|right|both|none
const PUNCT = {
  "period": [".", "left", true], "full stop": [".", "left", true],
  "comma": [",", "left", false], "question mark": ["?", "left", true],
  "exclamation point": ["!", "left", true], "exclamation mark": ["!", "left", true],
  "colon": [":", "left", false], "semicolon": [";", "left", false],
  "dash": ["-", "none", false], "hyphen": ["-", "both", false],
  "em dash": ["—", "both", false], "ellipsis": ["...", "left", false],
  "dot dot dot": ["...", "left", false],
  "open quote": ['"', "right", false], "close quote": ['"', "left", false],
  "apostrophe": ["'", "both", false],
  "open paren": ["(", "right", false], "close paren": [")", "left", false],
  "open bracket": ["[", "right", false], "close bracket": ["]", "left", false],
  "open brace": ["{", "right", false], "close brace": ["}", "left", false],
  "at sign": ["@", "both", false], "ampersand": ["&", "none", false],
  "percent sign": ["%", "left", false], "dollar sign": ["$", "right", false],
  "underscore": ["_", "both", false], "forward slash": ["/", "both", false],
  "backslash": ["\\", "both", false], "pipe symbol": ["|", "none", false],
  "tilde": ["~", "right", false], "backtick": ["`", "both", false],
  "equals sign": ["=", "none", false], "plus sign": ["+", "none", false],
};
// [flow.vocabulary] — the documented example ships live on this page
const VOCAB = { "hyper furion": "HyperFurion" };

// phrase table: tokens -> ["punct", spec] | ["cmd", action] | ["vocab", text]
const PHRASES = new Map();
for (const [p, spec] of Object.entries(PUNCT)) PHRASES.set(p, ["punct", spec]);
for (const [p, act] of Object.entries(COMMANDS)) PHRASES.set(p, ["cmd", act]);
PHRASES.set("literal", ["cmd", "literal"]);
for (const [p, r] of Object.entries(VOCAB)) PHRASES.set(p, ["vocab", r]);
const MAX_PHRASE = 3;

function matchPhrase(cores, i, maxLen) {
  const limit = Math.min(MAX_PHRASE, cores.length - i, maxLen);
  for (let len = limit; len >= 1; len--) {
    const entry = PHRASES.get(cores.slice(i, i + len).join(" "));
    if (entry) return [entry, len];
  }
  return [null, 0];
}
function couldExtend(cores, i) {
  const tail = cores.slice(i).join(" ");
  if (!tail || cores.length - i >= MAX_PHRASE) return false;
  for (const p of PHRASES.keys())
    if (p.length > tail.length && p.startsWith(tail + " ")) return true;
  return false;
}

// ── spoken cardinals -> digits (numbers.py) ───────────────────────────────
// "one hundred twenty three" -> "123"; "three point one four" -> "3.14";
// "one two seven" -> "127". Deliberately conservative: anything not fully
// understood stays spoken words, and single small words ("one", "nine")
// are only converted in aggressive mode so "no one knows" survives.
const UNITS = { zero:0, one:1, two:2, three:3, four:4, five:5, six:6, seven:7,
  eight:8, nine:9, ten:10, eleven:11, twelve:12, thirteen:13, fourteen:14,
  fifteen:15, sixteen:16, seventeen:17, eighteen:18, nineteen:19 };
const TENS = { twenty:20, thirty:30, forty:40, fifty:50, sixty:60, seventy:70,
  eighty:80, ninety:90 };
const DIGITS = Object.fromEntries(Object.entries(UNITS).filter(([, v]) => v <= 9));
const NUMBER_WORDS = new Set([...Object.keys(UNITS), ...Object.keys(TENS),
  "hundred", "thousand", "and", "point"]);
const GLUE = new Set(["and", "point"]);
const PUNCT_TAIL = /[.,!?;:]+$/;

function parseCardinal(words) {
  if (!words.length) return null;
  let total = 0, current = 0, seen = false;
  for (const w of words) {
    if (w === "and") { if (!seen) return null; continue; }  // only valid mid-number
    if (w in UNITS) {
      const v = UNITS[w];
      if (v === 0) { if (seen || words.length > 1) return null; current = 0; }  // "zero" stands alone
      else if (v >= 10) { if (current % 100 !== 0) return null; current += v; }  // teens claim the slot
      else { if (current % 10 !== 0 || (current % 100 >= 10 && current % 100 < 20)) return null; current += v; }
      seen = true;
    } else if (w in TENS) {
      if (current % 100 !== 0) return null;
      current += TENS[w]; seen = true;
    } else if (w === "hundred") {
      if (!seen || current === 0 || current >= 100) return null;
      current *= 100;
    } else if (w === "thousand") {
      if (!seen || current === 0 || current >= 1000) return null;
      total += current * 1000; current = 0; seen = true;
    } else return null;
  }
  return seen ? total + current : null;
}
// "one two seven" -> "127": all words must be single digits
function parseDigitSequence(words) {
  if (words.length < 2 || words.some((w) => !(w in DIGITS))) return null;
  return words.map((w) => DIGITS[w]).join("");
}
function parseNumberRun(words) {
  const lowered = words.map((w) => w.toLowerCase());
  const split = lowered.indexOf("point");
  if (split !== -1) {
    const whole = lowered.slice(0, split), frac = lowered.slice(split + 1);
    if (!frac.length || frac.includes("point") || frac.some((w) => !(w in DIGITS))) return null;
    const wholeValue = whole.length ? parseCardinal(whole) : 0;
    if (wholeValue === null) return null;
    return wholeValue + "." + frac.map((w) => DIGITS[w]).join("");
  }
  const value = parseCardinal(lowered);
  if (value !== null) return String(value);
  return parseDigitSequence(lowered);
}
// Replace maximal runs of spoken-number words with digit strings. Single-
// word runs below minValue stay words (prose keeps "five" but converts
// "twenty three"); multi-word runs always convert.
export function convertNumbers(tokens, minValue = 0) {
  const result = [];
  let i = 0;
  while (i < tokens.length) {
    const c = core(tokens[i]);
    if (!NUMBER_WORDS.has(c) || GLUE.has(c)) { result.push(tokens[i]); i += 1; continue; }
    // greedily extend the run, then trim trailing glue words; a token with
    // attached punctuation ("four.") ends the run after itself
    let end = i;
    while (end < tokens.length && NUMBER_WORDS.has(core(tokens[end]))) {
      end += 1;
      if (PUNCT_TAIL.test(tokens[end - 1])) break;
    }
    while (end > i && GLUE.has(core(tokens[end - 1]))) end -= 1;
    const run = tokens.slice(i, end).map(core);
    const parsed = parseNumberRun(run);
    const multi = end - i > 1;
    if (parsed !== null && (multi || Math.abs(parseFloat(parsed)) >= minValue)) {
      const tail = tokens[end - 1];
      const suffix = (tail.match(PUNCT_TAIL) || [""])[0];
      result.push(parsed + suffix);
      i = end;
    } else { result.push(tokens[i]); i += 1; }
  }
  return result;
}

// ── registers (registers.py) ──────────────────────────────────────────────
export const REGISTERS = {
  prose:    { name: "prose",    smartCaps: true,  grammar: true,  numbersOn: false, numbersMin: 10 },
  terminal: { name: "terminal", smartCaps: false, grammar: true,  numbersOn: true,  numbersMin: 0, terminal: true },
  verbatim: { name: "verbatim", smartCaps: false, grammar: false, numbersOn: false, numbersMin: 10 },
  // semantic registers (code.py): speech is compiled, not transcribed
  python:   { name: "python",   smartCaps: false, grammar: true,  numbersOn: true,  numbersMin: 0, compiler: "python" },
  shell:    { name: "shell",    smartCaps: false, grammar: true,  numbersOn: true,  numbersMin: 0, compiler: "shell", terminal: true },
};

// ── the semantic compilers (code.py) — pure, prefix-stable folds ──────────
// "for i in range ten colon" -> `for i in range(10):`; "pipe grep dash i
// error" -> `| grep -i error`. Deterministic tables only; anything unknown
// falls through as a plain word. All carried context lives in the fold
// state's `pending` slot, so the molten commit/preview split stays exact.
const PY_GLYPHS = { dot: [".", "both"], equals: ["=", "none"], plus: ["+", "none"],
  minus: ["-", "none"], times: ["*", "none"], modulo: ["%", "none"], arrow: ["->", "none"] };
const SH_GLYPHS = { pipe: ["|", "none"], dot: [".", "both"], star: ["*", "right"], slash: ["/", "both"] };
const PY_CALLABLES = new Set(["range", "print", "len", "str", "int", "float", "input",
  "enumerate", "sorted", "reversed", "abs", "min", "max", "sum", "type", "repr"]);
const COMPILERS = {
  python: { glyphs: PY_GLYPHS, callables: PY_CALLABLES, dashHold: false },
  shell:  { glyphs: SH_GLYPHS, callables: new Set(), dashHold: true },
};
function compileCode(items, state, { glyphs, callables, dashHold }) {
  const out = [];
  let atStart = state.atStart, glueNext = state.glueNext, pending = state.pending || "";
  const emit = (text, glueLeft) => {
    if (!atStart && !glueNext && !glueLeft) out.push(" ");
    out.push(text); atStart = false; glueNext = false;
  };
  const emitMode = (glyph, mode) => {
    if (mode === "left") emit(glyph, true);
    else if (mode === "right") { emit(glyph, false); glueNext = true; }
    else if (mode === "both") { emit(glyph, true); glueNext = true; }
    else emit(glyph, false);
  };
  const flushDash = () => { if (pending === "dash") { emit("-", false); pending = ""; } };
  for (const it of items) {
    if (it.kind === "break") { flushDash(); pending = ""; out.push(it.text); atStart = false; glueNext = true; }
    else if (it.kind === "punct") {
      if (pending === "call" && it.text === ":") { emit("):", true); pending = ""; continue; }
      if (dashHold && it.text === "-" && it.mode === "none") { flushDash(); pending = "dash"; continue; }
      flushDash();
      if (pending === "call" && it.text === ")") pending = "";
      emitMode(it.text, it.mode);
    } else if (it.kind === "word") {
      const c = it.text.toLowerCase();
      if (pending === "dash") { emit("-" + it.text, false); pending = ""; continue; }
      const g = Object.hasOwn(glyphs, c) ? glyphs[c] : null;
      if (g) { emitMode(g[0], g[1]); continue; }
      if (callables.has(c) && pending !== "call") { emit(it.text + "(", false); glueNext = true; pending = "call"; continue; }
      emit(it.text, false);
    }
  }
  return { text: out.join(""), st: { ...state, atStart, glueNext, capNext: false, pending } };
}

// ── parse: raw tokens -> items, with the frozen fence (grammar.py) ────────
// items: {kind: word|punct|break|scratch|instruction|respell|key, text,
//         mode, sentenceEnd, count, s, e}  — s/e are [start, end) raw-token
// indices. respell: mode replace (the previous word) | insert. key: text
// is the action ("select:word:left"), count how many times.
// `settled` is how many tokens the provider has finalized: a spelled run
// or a navigation command starting inside them is decided there — it
// never waits for, or grows into, the next segment.
const SPELL_WORD = "spell";
// hesitation sounds a streaming recognizer writes down: dropped ([flow] fillers)
export const DEFAULT_FILLERS = ["um", "umm", "uh", "uhh", "uhm", "erm"];
const SENTENCE_STOPS = ".?!";
// a sentence end the recognizer attached to a hesitation ("… so, um.")
// still ends the sentence — after a word, and only once
function fillerStop(token, items, s, e) {
  const tail = (token.match(PUNCT_STRIP) || [""])[0];
  const stop = [...tail].find((ch) => SENTENCE_STOPS.includes(ch));
  if (!stop || !items.length) return;
  const before = [...items].reverse().find((it) => it.kind !== "filler");
  if (!before || before.kind !== "word" || SENTENCE_STOPS.includes(before.text.slice(-1))) return;
  items.push({ kind: "punct", text: stop, mode: "left", sentenceEnd: true, s, e });
}
const AMBIGUOUS = new Set(["a", "i", "one", "two", "four", "eight"]);  // letters that are also words
const PENDING = "pending";

export function parse(tokens, { flush = false, frozen = 0, settled = 0, bounds = [], commits = [], register, cfg }) {
  const reg = register || REGISTERS.prose;
  if (!reg.grammar) {
    return { items: tokens.map((t, i) => ({ kind: "word", text: t, s: i, e: i + 1 })),
             pendingFrom: null };
  }
  const wake = ((cfg && cfg.wakeWord) || "vk").toLowerCase();
  const spelling = !cfg || cfg.spelling !== false;
  const fillers = new Set(((cfg && cfg.fillers) || DEFAULT_FILLERS).map((f) => String(f).trim().toLowerCase()).filter(Boolean));
  const nav = !!(cfg && cfg.nav);
  const cores = tokens.map(core);
  const items = [];
  let pendingFrom = null, i = 0;
  // how far an open-ended command at i may read, and whether its end is
  // decided: inside the final segments it may not cross the end of the
  // segment it started in (nor the committed fence) and never waits; in
  // the molten tail it reads to the end and waits unless flushing
  // where the committed item holding token `at` ends: below the fence
  // every item is parsed within its own span, so it reads back exactly
  // as it was committed
  const ends = [];  // where the committed item holding each token ends, computed once
  for (const e of commits) while (ends.length < Math.min(e, frozen)) ends.push(Math.min(e, frozen));
  while (ends.length < frozen) ends.push(frozen);
  const itemEnd = (at) => (at < frozen ? ends[at] : frozen);
  const limitAt = (i) => {
    if (i >= settled && i >= frozen) return [tokens.length, flush];
    let limit = Math.max(settled, frozen);
    for (const b of bounds) if (b > i) { limit = Math.min(limit, b); break; }
    if (i < frozen) limit = Math.min(limit, itemEnd(i));
    return [limit, true];
  };
  while (i < tokens.length) {
    const fence = i < frozen ? itemEnd(i) - i : tokens.length;
    // wake word: everything after it is an instruction, never typed —
    // it resolves only at finalize; until then it holds the tail back
    const wk = wakeAt(cores, i, wake);
    if (wk && i < frozen) {
      // the page takes an instruction when its utterance closes (the
      // daemon waits for the stop); below the fence it reads back as the
      // instruction it was committed as
      const end = itemEnd(i);
      items.push({ kind: "instruction", text: tokens.slice(i + wk, end).join(" "), s: i, e: end });
      i = end; continue;
    }
    if (wk) {
      if (!flush) { pendingFrom = i; break; }
      items.push({ kind: "instruction", text: tokens.slice(i + wk).join(" "),
                   s: i, e: tokens.length });
      break;
    }
    if (fillers.has(cores[i])) {
      // a hesitation sound renders nothing; a comma attached to it goes too
      items.push({ kind: "filler", s: i, e: i + 1 });
      fillerStop(tokens[i], items, i, i + 1);
      i += 1; continue;
    }
    if (spelling && cores[i] === SPELL_WORD) {
      let [limit, decided] = limitAt(i);
      const head = i + (cores[i + 1] === "that" ? 2 : 1);
      // "spell that" ended its segment: the letters may come after a
      // pause, in the next one. Behind the fence the run reads back
      // exactly as it was committed.
      if (head >= limit) {
        if (i < frozen) { limit = itemEnd(i); decided = true; }
        else if (settled > head) { limit = settled; decided = true; }
        else { limit = tokens.length; decided = flush; }
      }
      const spelled = parseSpelling(tokens.slice(0, limit), cores.slice(0, limit), i, decided);
      if (spelled === PENDING) { pendingFrom = i; break; }
      if (spelled) { items.push(spelled[0]); i = spelled[1]; continue; }
    }
    if (nav && cores[i] in NAV_VERBS) {
      const [limit, decided] = limitAt(i);
      const command = parseNav(cores.slice(0, limit), i, { decided });
      if (command === NAV_PENDING) { pendingFrom = i; break; }
      if (command) {
        const [action, count, end] = command;
        items.push({ kind: "key", text: action, count, s: i, e: end });
        i = end; continue;
      }
    }
    const [entry, used] = matchPhrase(cores, i, fence);
    if (!entry && !flush && i >= frozen && couldExtend(cores, i)) { pendingFrom = i; break; }
    if (entry) {
      const [kind, payload] = entry;
      if (kind === "punct") {
        items.push({ kind: "punct", text: payload[0], mode: payload[1],
                     sentenceEnd: payload[2], s: i, e: i + used });
      } else if (kind === "vocab") {
        items.push({ kind: "word", text: payload, s: i, e: i + used });
        const tail = (tokens[i + used - 1].match(PUNCT_STRIP) || [""])[0];
        for (const ch of tail) if (".,!?;:".includes(ch))
          items.push({ kind: "punct", text: ch, mode: "left",
                       sentenceEnd: ".!?".includes(ch), s: i, e: i + used });
      } else if (payload === "literal") {
        // emit the next token verbatim, bypassing the grammar; a "literal"
        // committed bare (its word never came) reads back bare
        const target = i + used;
        if (target >= tokens.length || (i < frozen && target >= itemEnd(i))) {
          if (i >= frozen && !flush) { pendingFrom = i; break; }
          items.push({ kind: "word", text: tokens[i], s: i, e: i + 1 }); i += 1; continue;
        }
        items.push({ kind: "word", text: tokens[target], s: i, e: target + 1 });
        i = target + 1; continue;
      } else if (payload === "scratch") {
        items.push({ kind: "scratch", s: i, e: i + used });
      } else {  // "\n" | "\n\n"
        items.push({ kind: "break", text: payload, s: i, e: i + used });
      }
      i += used; continue;
    }
    const token = tokens[i];
    if (fillers.size && token.endsWith(",")) {
      // "we should, uh, ship it": the commas were the recognizer's brackets
      // around the hesitation, and go with it; the word and the filler are
      // one item, so they freeze together and read back the same
      const limit = i < frozen ? itemEnd(i) : tokens.length;
      const after = i + 1;
      if (after < limit && fillers.has(cores[after])) {
        items.push({ kind: "word", text: token.slice(0, -1), s: i, e: after + 1 });
        fillerStop(tokens[after], items, i, after + 1);
        i = after + 1; continue;
      }
      if (after >= tokens.length && i >= frozen && i >= settled && !flush) { pendingFrom = i; break; }  // the next word may be a hesitation
    }
    items.push({ kind: "word", text: token, s: i, e: i + 1 });
    i += 1;
  }
  // fold spoken-number runs (held back while still touching the molten tail)
  const numbersOn = (cfg && cfg.numbers === "always") ||
    ((!cfg || cfg.numbers === "auto") && reg.numbersOn);
  const numbersMin = cfg && cfg.numbers === "always" ? 0 : reg.numbersMin;
  if (numbersOn) {
    const out = []; let run = [];
    const nflush = flush || pendingFrom !== null;
    const close = (atTail) => {
      if (!run.length) return;
      if (atTail && !nflush) {
        if (pendingFrom === null) pendingFrom = run[0].s;
        run = []; return;
      }
      const texts = run.map((it) => it.text);
      const conv = convertNumbers(texts, numbersMin);
      if (conv.length === texts.length && conv.every((t, k) => t === texts[k])) out.push(...run);
      else for (const t of conv) out.push({ kind: "word", text: t, s: run[0].s, e: run[run.length - 1].e });
      run = [];
    };
    for (const it of items) {
      if (it.kind === "word" && NUMBER_WORDS.has(core(it.text))) {
        if (run.length && run[0].s < frozen && it.s >= itemEnd(run[0].s)) close(false);  // the committed part folds alone
        run.push(it);
      } else { close(false); out.push(it); }
    }
    close(run.length > 0 && run[0].s >= frozen);  // a run behind the fence was decided when committed
    return { items: out, pendingFrom };
  }
  return { items, pendingFrom };
}

// "spell that <letters>" (replace the previous word) or "spell <letters>"
// (type the spelled word) at `index`. Returns [item, next index]; PENDING
// while the letter run touches the tail and might keep growing; null when
// this "spell" is just a word (an insert needs two letters, so "cast a
// spell" and "spell a …" stay prose).
function parseSpelling(tokens, cores, index, flush) {
  let start = index + 1, mode = "insert";
  if (start < tokens.length && cores[start] === "that") { mode = "replace"; start += 1; }
  if (start >= tokens.length) return flush ? null : PENDING;
  const pieces = [];  // [letters, tokens used]
  let cursor = start, endedOnWord = false, total = 0;
  while (cursor < tokens.length && total < MAX_SPELLED_LETTERS) {
    const [letters, used] = lettersAt(tokens, cursor);
    if (!used) {
      if (!flush && cursor === tokens.length - 1 && couldBeCapital(tokens[cursor])) return PENDING;
      endedOnWord = true; break;
    }
    pieces.push([letters, used]); total += letters.length; cursor += used;
  }
  if (cursor >= tokens.length && !flush) return PENDING;  // the next update may spell more letters
  if (endedOnWord) {
    // a real word follows: trailing "a" / "I" / "one" are likely that
    // sentence's words, not letters ("… x a good one")
    while (pieces.length > 1 && AMBIGUOUS.has(cores[cursor - pieces[pieces.length - 1][1]]))
      cursor -= pieces.pop()[1];
  }
  let word = pieces.map((p) => p[0]).join("");
  if (word.length < (mode === "replace" ? 1 : 2)) return null;
  word = word.slice(0, MAX_SPELLED_LETTERS);
  return [{ kind: "respell", text: word, mode, s: index, e: cursor }, cursor];
}

// ── render: the pure register fold (registers.py render_items) ───────────
// Takes and returns the carried fold state, so frozen text can be built
// append-only and the molten tail folded as its continuation — the
// prefix-stability property the whole engine leans on.
const ENDERS = /[.!?]$/;
function capitalized(t) {
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (/[a-z]/i.test(c)) return t.slice(0, i) + c.toUpperCase() + t.slice(i + 1);
    if (!/[0-9"'([{]/.test(c)) break;
  }
  return t;
}
export function initialState(register) {
  return { atStart: true, glueNext: false, capNext: (register || REGISTERS.prose).smartCaps, pending: "" };
}
// the state a recording starts in when it continues text the previous one
// left at the caret ([flow] rejoin): a space before its first word, and a
// capital only if that text ended a sentence; null when there is nothing
// to continue (no text, or it ended in whitespace or a line break)
export function continuationState(previousTail, register) {
  const tail = previousTail ? previousTail.slice(-1) : "";
  if (!tail || /\s/.test(tail)) return null;
  return { atStart: false, glueNext: false, capNext: !!(register || REGISTERS.prose).smartCaps && ".!?".includes(tail), pending: "" };
}
export function render(items, register, state) {
  const reg = register || REGISTERS.prose;
  const st = state ? { ...state } : initialState(reg);
  if (reg.compiler && COMPILERS[reg.compiler]) return compileCode(items, st, COMPILERS[reg.compiler]);
  const out = [];
  const emit = (text, glueLeft) => {
    if (!st.atStart && !st.glueNext && !glueLeft) out.push(" ");
    out.push(text); st.atStart = false; st.glueNext = false;
  };
  for (const it of items) {
    if (it.kind === "break") { out.push(it.text); st.atStart = false; st.glueNext = true; st.capNext = reg.smartCaps; }
    else if (it.kind === "punct") {
      if (it.mode === "left") emit(it.text, true);
      else if (it.mode === "right") { emit(it.text, false); st.glueNext = true; }
      else if (it.mode === "both") { emit(it.text, true); st.glueNext = true; }
      else emit(it.text, false);
      if (it.sentenceEnd && reg.smartCaps) st.capNext = true;
    } else if (it.kind === "word") {
      let t = it.text;
      if (st.capNext && reg.smartCaps) t = capitalized(t);
      emit(t, false);
      st.capNext = reg.smartCaps && ENDERS.test(t.trimEnd());
    }
    // scratch/instruction/key render nothing; the engine acts on them
  }
  return { text: out.join(""), st };
}

// char-counted backspacing over `text` may not match how the focused app
// groups grapheme clusters (astral plane, combining marks, ZWJ sequences)
export function riskyBackspace(text) {
  return /[\u{10000}-\u{10FFFF}̀-ͯ‍️︎]/u.test(text);
}

// ═══ THE MOLTEN ENGINE — one dictation, from first sound to the stop ═════
// A port of the daemon's engine.py (minus the pause reviewer, which needs
// a recognizer that punctuates pauses). One engine lives for one
// recording; every utterance the recognizer closes is a final segment
// inside it. It keeps the single source of truth for what should be on
// screen:
//
//   committed  — text frozen on screen; repairs never cross it
//   molten     — parsed but still revisable; rendered as preview
//   pending    — trailing tokens held back (an incomplete phrase, a
//                growing number run, a wake-word instruction)
//
// A molten item commits when the provider finalizes it, when it survives
// the stability window, or eagerly when it contains non-ASCII (never
// repair across a clipboard-pasted run). Commits are monotonic: the only
// way committed text shrinks is the user's own "scratch that", which
// rewinds to a segment snapshot, or "spell that", which swaps one word.
//
// A navigation command ("select previous word") is a barrier: it fires
// only when it is a whole final segment of its own — said with a pause
// before and after — and then everything before it must be on screen
// before its keys are pressed. The engine stops committing at the
// barrier; the page converges the window, presses the keys on the board,
// and calls completeAction(), which starts a fresh segment: the caret has
// moved, so nothing before the command can be repaired or scratched.
const LAST_WORD = /(\S+?)([.,!?;:)\]}"'»”’]*)(\s*)$/;
const FINAL_ONLY = new Set(["respell", "key"]);  // rewrite committed text: never on a stability guess
// a pause's next word counts as settled after surviving an update or this long
const PAUSE_SETTLE_MS = 600;

export function moltenLine({ register, cfg, state } = {}) {
  const reg = register || REGISTERS.prose;
  const stabMs = () => (cfg && cfg.stabilityMs === Infinity) ? Infinity : Math.max(200, (cfg && cfg.stabilityMs) || 1500);
  const stabilityUpdates = Math.max(1, (cfg && cfg.stabilityUpdates) || 2);
  const adaptive = !cfg || cfg.adaptive !== false;
  const maxMolten = (cfg && cfg.maxMoltenChars) || 160;
  let revDepth = 0;  // adaptive: observed provider revision depth, decaying
  // widen the horizon automatically when the provider revises deeply
  const required = () => (adaptive ? Math.max(stabilityUpdates, Math.min(6, Math.ceil(revDepth))) : stabilityUpdates);
  let tokens = [], meta = [];          // meta[i]: {since, stable}
  let items = [], pendingFrom = null, flushPending = false;
  let committedTokens = 0, committedItems = 0;
  let committedRender = "", renderState = state || initialState(reg);
  let finalTokens = 0;
  let snapshots = [{ len: 0, st: renderState }];
  let segmentMarks = [], segmentBounds = new Set([0]);
  let instruction = "", scratches = 0, corrections = [];
  let barrier = null, lastAction = null, typedBefore = "", finalizing = false;
  let lastRepair = false;
  // punctuation where the speaker paused (pauses.js): "off" keeps the
  // recognizer's periods; "rules" settles the clear cases and keeps the
  // recognizer's call for the rest (the daemon's [llm] reviewer is not on
  // this page, so "llm" and "auto" read as "rules" here)
  const pauseMode = (cfg && cfg.pauseReview === "off") || !(cfg && cfg.pauseReview) ? "off" : "rules";
  const pauseMap = new Map();   // index of the first token after a pause -> {decision, provisional}
  const lowerSeen = new Set();  // words seen lowercase mid-sentence
  const pauseLog = [];          // each pause the rules changed: [before, after], for the status line

  // ── inputs ─────────────────────────────────────────────────────────────
  function update(raw, now, { final = false } = {}) {
    let next = raw.trim() ? raw.trim().split(/\s+/) : [];
    if (next.length < committedTokens) next = tokens.slice(0, committedTokens);  // the floor wins
    const oldMolten = tokens.slice(committedTokens), newMolten = next.slice(committedTokens);
    let prefix = 0;
    while (prefix < oldMolten.length && prefix < newMolten.length &&
           oldMolten[prefix].toLowerCase() === newMolten[prefix].toLowerCase()) prefix++;
    lastRepair = oldMolten.length > prefix;  // a revision landed, or the tail shrank
    if (oldMolten.length > prefix) revDepth = Math.max(revDepth, oldMolten.length - prefix);
    flushPending = false;
    const merged = newMolten.map((t, i) => {
      if (i < prefix) { const m = meta[committedTokens + i]; m.stable++; return m; }
      return { since: now, stable: 0 };
    });
    tokens = tokens.slice(0, committedTokens).concat(newMolten);
    meta = meta.slice(0, committedTokens).concat(merged);
    for (let i = Math.max(1, committedTokens + prefix); i < tokens.length; i++)
      if (/^\p{Ll}/u.test(tokens[i])) lowerSeen.add(pauses.core(tokens[i]));
    if (final) { finalTokens = Math.max(finalTokens, tokens.length); segmentBounds.add(tokens.length); notePause(); }
    revDepth *= 0.98;
    reparse();
    settlePauses(now);
    commitReady(now);
    if (final) markSegmentBoundary();
    return view();
  }
  // time-based commits between provider updates, plus holdback expiry so a
  // trailing half-phrase can't stall dictation forever
  function tick(now) {
    if (pendingFrom !== null && !pendingIsInstruction()) {
      if (now - meta[pendingFrom].since >= 2 * stabMs()) { flushPending = true; reparse(); }
    }
    revDepth *= 0.995;
    settlePauses(now);
    commitReady(now);
    return view();
  }
  function finalize(raw, now) {
    update(raw, now, { final: true });
    flushPending = true;
    reparse();
    // every pause gets its answer now: one nobody decided falls to the rules
    for (const [index, pause] of [...pauseMap].sort((a, b) => a[0] - b[0]))
      if (pause.decision === null) pause.decision = ruleDecisionAt(index)[0];
    reparse();
    finalizing = true;
    return commitRest();
  }
  // at finalize: commit everything, stopping at a navigation barrier (the
  // page resumes with completeAction)
  function commitRest() {
    while (barrier === null && committedItems < items.length) commitItem(items[committedItems]);
    return result();
  }
  function result() {
    return { text: committedRender, instruction: barrier === null ? instruction : "", scratches,
             corrections: [...corrections], action: barrier, typedBefore, ...view() };
  }
  // the page pressed the barrier's keys — or refused them (pressed=false:
  // the caret never moved, so nothing changes). Either way dictation
  // resumes; after a press it starts a fresh segment.
  function completeAction(now, { pressed = true } = {}) {
    const action = barrier;
    if (action === null) return finalizing ? commitRest() : null;
    barrier = null;
    if (!pressed) { if (finalizing) return commitRest(); commitReady(now); return null; }
    lastAction = action;
    typedBefore += committedRender;
    committedRender = "";
    let st = renderState;
    if (FRESH_FIELD.has(action.action)) st = initialState(reg);  // tab / escape / a page away: likely another field
    else if (GLUED.some((g) => action.action.startsWith(g)) || action.action.endsWith(":start"))
      st = { ...st, glueNext: true };  // the next word fills a selection or a gap, or starts a line
    renderState = st;
    snapshots = [{ len: 0, st }];
    if (finalizing) return commitRest();
    commitReady(now);
    return null;
  }
  // the human did something at the keyboard (pressed Enter, clicked): the
  // text typed so far is no longer ours — a fresh segment starts at the
  // caret, as after a navigation command, on a fresh line
  function detach() {
    typedBefore += committedRender;
    committedRender = "";
    renderState = initialState(reg);
    snapshots = [{ len: 0, st: renderState }];
  }
  // the page's one liberty: a wake-word instruction that closes an
  // utterance is taken there (the daemon waits for the stop). Returns the
  // instruction, consuming its tokens, or "".
  function takeInstruction() {
    if (barrier !== null || !pendingIsInstruction() || tokens.length > finalTokens) return "";
    flushPending = true; reparse(); flushPending = false;
    const it = items[committedItems];
    if (!it || it.kind !== "instruction" || committedItems !== items.length - 1) { reparse(); return ""; }
    commitItem(it);
    const text = instruction; instruction = "";
    return text;
  }
  // the rewrite landed: the committed text is now `text`
  function rewrite(text) {
    committedRender = text;
    renderState = { ...renderState, atStart: text.length === 0, glueNext: false,
                    capNext: reg.smartCaps && (text.length === 0 || /[.!?]$/.test(text.trimEnd())) };
    snapshots = [{ len: text.length, st: renderState }];
  }

  // ── outputs ────────────────────────────────────────────────────────────
  function view() {
    const molten = barrier !== null ? "" : render(previewItems(), reg, { ...renderState }).text;
    let instr = "";
    if (pendingIsInstruction()) instr = tokens.slice(pendingFrom + 1).join(" ") || " ";
    const captionTail = viewTokens().slice(committedTokens).join(" ");
    return { frozen: committedRender, molten, instr: instr.trim(), repair: lastRepair, action: barrier, caption: captionTail,
             pauseLog };
  }

  // ── internal ───────────────────────────────────────────────────────────
  function previewItems() {
    const out = [];
    for (const it of items.slice(committedItems)) {
      if (it.kind === "word" || it.kind === "punct" || it.kind === "break") out.push(it);
      else if (it.kind === "respell" && it.mode === "insert") out.push({ kind: "word", text: it.text, s: it.s, e: it.e });
    }
    return out;
  }
  function pendingIsInstruction() {
    if (pendingFrom === null || pendingFrom >= tokens.length) return false;
    return wakeAt(tokens.map(core), pendingFrom, (cfg && cfg.wakeWord) || "vk") > 0;
  }
  // ── pauses ─────────────────────────────────────────────────────────────
  // the tokens as they should read: each pause's punctuation (and the
  // capital after it) as decided, or as provisionally ruled
  function viewTokens() {
    if (!pauseMap.size) return tokens;
    const v = [...tokens];
    for (const [index, pause] of [...pauseMap].sort((a, b) => a[0] - b[0])) {
      const d = pause.decision || pause.provisional;
      if (d && index > 0 && index < v.length) [v[index - 1], v[index]] = pauses.apply(v[index - 1], v[index], d);
    }
    return v;
  }
  // a final segment ending in a provider period: remember the pause
  function notePause() {
    const index = tokens.length;
    if (pauseMode === "off" || !reg.smartCaps || !reg.grammar || pauseMap.has(index)) return;
    if (index - 1 < committedTokens || !pauses.reviewable(tokens[index - 1])) return;
    pauseMap.set(index, { decision: null, provisional: null });
  }
  const itemAt = (index) => items.find((it) => it.s <= index && index < it.e) || null;
  // the rules' call for the pause before token `index`: [decision, confident]
  function ruleDecisionAt(index) {
    if (index >= tokens.length) return [pauses.decision(".", false, ""), true];  // nothing followed
    let right = tokens[index];
    let it = itemAt(index);
    if (it && it.kind === "filler") {
      // "project. Um, and how": the word after the hesitation decides
      let nxt = index + 1;
      while (nxt < tokens.length) { const f = itemAt(nxt); if (!f || f.kind !== "filler") break; nxt++; }
      if (nxt >= tokens.length || !itemAt(nxt)) return [pauses.keep(right), false];
      right = tokens[nxt]; it = itemAt(nxt);
    }
    if (it && it.s < index) return [pauses.decision("", false, pauses.core(right)), true];  // one phrase spans the pause
    return pauses.ruleDecision(tokens[index - 1], right, { rightKind: it ? it.kind : "word", lowerSeen });
  }
  // show each open pause per the rules, and settle it once the word after
  // it has settled
  function settlePauses(now) {
    let changed = false;
    for (const [index, pause] of [...pauseMap].sort((a, b) => a[0] - b[0])) {
      if (pause.decision !== null) continue;
      if (index >= tokens.length) { if (pause.provisional) { pause.provisional = null; changed = true; } continue; }
      if (!itemAt(index)) continue;  // a phrase still forming after the pause
      const [d] = ruleDecisionAt(index);
      if (!pauses.same(d, pause.provisional)) { pause.provisional = d; changed = true; }
      const m = meta[index];
      const settled = index < finalTokens || m.stable >= 1 || now - m.since >= PAUSE_SETTLE_MS;
      if (settled) {
        pause.decision = d;  // rules mode: the rules decide every pause
        if (d.punct !== ".") {
          const [l, r] = pauses.apply(tokens[index - 1], tokens[index], d);
          pauseLog.push([tokens[index - 1] + " " + tokens[index], l + " " + r]);
        }
      }
    }
    if (changed) reparse();
  }
  // true while an undecided pause sits right after this span
  const holdsPause = (start, end) => [...pauseMap].some(([index, p]) => p.decision === null && start < index && index <= end);

  function reparse() {
    const r = parse(viewTokens(), { flush: flushPending, frozen: committedTokens, settled: finalTokens,
                              bounds: [...segmentBounds].sort((a, b) => a - b),
                              commits: items.slice(0, committedItems).map((it) => it.e), register: reg, cfg });
    items = r.items; pendingFrom = r.pendingFrom;
  }
  function commitReady(now) {
    const horizon = stabMs();
    while (barrier === null && committedItems < items.length) {
      const it = items[committedItems];
      if (it.kind === "instruction") break;  // consumed at finalize, never mid-stream
      if (holdsPause(it.s, it.e)) break;  // the words after the pause will decide its period
      let committable = it.e <= finalTokens;
      if (!committable && FINAL_ONLY.has(it.kind)) break;
      if (!committable) {
        const metas = meta.slice(it.s, it.e);
        const need = required();
        committable = metas.every((m) => m.stable >= need && now - m.since >= horizon);
        if (!committable && it.kind === "word" && /[^\x00-\x7F]/.test(it.text))
          committable = metas.every((m) => m.stable >= 1);  // never repair across a pasted run
      }
      if (!committable) break;
      commitItem(it);
    }
    // safety valve: an endlessly-revising provider must not grow the
    // repairable tail without bound
    while (barrier === null && committedItems < items.length &&
           items[committedItems].kind !== "instruction" && !FINAL_ONLY.has(items[committedItems].kind)) {
      if (render(previewItems(), reg, { ...renderState }).text.length <= maxMolten) break;
      commitItem(items[committedItems]);
    }
  }
  function commitItem(it) {
    for (const [index, pause] of pauseMap)
      if (pause.decision === null && it.s < index && index <= it.e)
        pause.decision = pause.provisional || pauses.keep(index < tokens.length ? tokens[index] : "");  // forced out: what shows, stays
    if (it.kind === "scratch") applyScratch();
    else if (it.kind === "respell" && it.mode === "replace") applyRespell(it.text);
    else if (it.kind === "respell") append([{ kind: "word", text: it.text, s: it.s, e: it.e }]);
    else if (it.kind === "key") commitKey(it);
    else if (it.kind === "instruction") { if (it.text) instruction = it.text; }
    else append([it]);
    committedTokens = Math.max(committedTokens, it.e);
    committedItems += 1;
    takeSnapshots();
  }
  function append(list) {
    const r = render(list, reg, renderState);
    committedRender += r.text; renderState = r.st;
  }
  function applyScratch() {
    let target = null;
    for (let i = snapshots.length - 1; i >= 0; i--) if (snapshots[i].len < committedRender.length) { target = snapshots[i]; break; }
    if (!target) return;
    if (riskyBackspace(committedRender.slice(target.len))) return;
    committedRender = committedRender.slice(0, target.len);
    renderState = target.st;
    while (snapshots.length && snapshots[snapshots.length - 1].len > target.len) snapshots.pop();
    scratches++;
  }
  // a navigation command fires only as a whole final segment; said
  // mid-sentence it was dictation after all, and types as words
  function commitKey(it) {
    if (segmentBounds.has(it.s) && segmentBounds.has(it.e)) { barrier = { action: it.text, count: it.count }; return; }
    append(tokens.slice(it.s, it.e).map((t, k) => ({ kind: "word", text: t, s: it.s + k, e: it.s + k + 1 })));
  }
  // "spell that …": swap the last committed word for the spelled one,
  // keeping its trailing punctuation and its capital
  function applyRespell(spelled) {
    const m = LAST_WORD.exec(committedRender);
    if (!m) {
      if (lastAction && lastAction.action.startsWith("select:")) append([{ kind: "word", text: spelled }]);  // typed over the selection
      return;
    }
    const token = m[1];
    const heard = token.replace(/^["'([{«“‘]+/, "");  // an opening quote stays put
    const start = m.index + token.length - heard.length;
    if (!heard) return;
    if (riskyBackspace(committedRender.slice(start))) return;
    if (/^[A-Z]/.test(heard) && spelled === spelled.toLowerCase()) spelled = spelled[0].toUpperCase() + spelled.slice(1);
    committedRender = committedRender.slice(0, start) + spelled + m[2] + m[3];
    const shift = spelled.length - heard.length;
    snapshots = snapshots.map((s) => (s.len <= start ? s : { len: s.len + shift, st: s.st }));
    if (heard !== spelled) corrections.push([heard, spelled]);
  }
  function markSegmentBoundary() {
    const mark = tokens.length;
    if (!segmentMarks.length || segmentMarks[segmentMarks.length - 1] !== mark) segmentMarks.push(mark);
    takeSnapshots();
  }
  // snapshot each segment end once all of its words are committed
  function takeSnapshots() {
    while (segmentMarks.length && segmentMarks[0] <= committedTokens) {
      segmentMarks.shift();
      if (snapshots.length && snapshots[snapshots.length - 1].len === committedRender.length) continue;
      snapshots.push({ len: committedRender.length, st: renderState });
    }
  }

  return {
    update, tick, finalize, completeAction, takeInstruction, rewrite, result, detach,
    flush: (now = 0) => finalize(tokens.join(" "), now),
    peek: view,
    pendingAction: () => barrier,
    committed: () => committedRender,
    state: () => renderState,
    register: reg,
  };
}

// ═══ scripted playback — chips and the autopilot speak through this ══════
// Compiles a sentence into interim snapshots the engine replays: words land
// one by one, and a planned mis-hear repairs itself two beats later — the
// shape of a real streaming session, deterministic and honestly labeled.
export function compileScript(text, { revise = null, wpm = 320 } = {}) {
  const words = text.split(/\s+/);
  const beat = 60000 / wpm;
  const steps = [];
  let t = 0;
  for (let n = 1; n <= words.length; n++) {
    const shown = words.slice(0, n).map((w, i) =>
      (revise && i === revise.at && n < Math.min(words.length, revise.at + 3)) ? revise.wrong : w);
    t += beat * (0.7 + ((n * 7919) % 13) / 18);  // human-ish cadence, seedless
    steps.push({ t: Math.round(t), text: shown.join(" ") });
  }
  return { steps, final: text, dur: Math.round(t) };
}

// ═══ the page's [llm] stand-in — deterministic, labeled, local ═══════════
// The daemon sends "<wake>, <instruction>" plus the just-typed text to your
// configured [llm] (grok-4-fast by default; any OpenAI-compatible server).
// This page applies a small deterministic rewrite instead — nothing leaves.
const FORMAL = [
  [/\bi think\b/gi, "I believe"], [/\bworks now\b/gi, "functions correctly"],
  [/\bfixed\b/gi, "resolved"],
  [/\bfix\b/gi, "resolve"], [/\ba bunch of\b/gi, "several"],
  [/\bbugs?\b/gi, (m) => m.length > 3 ? "defects" : "defect"],
  [/\bworks\b/gi, "functions"], [/\bwork\b/gi, "function"],
  [/\bgonna\b/gi, "going to"], [/\bwanna\b/gi, "want to"],
  [/\bgotta\b/gi, "have to"], [/\bkind of\b/gi, "somewhat"],
  [/\bship\b/gi, "release"], [/\bshipped\b/gi, "released"],
  [/\bokay?\b/gi, "acceptable"], [/\bstuff\b/gi, "material"],
  [/\bpretty\b/gi, "rather"], [/\breally\b/gi, "considerably"],
  [/\bhuge\b/gi, "substantial"], [/\bbroke\b/gi, "failed"],
  [/\bweird\b/gi, "unusual"],
  [/\bdoesn't\b/gi, "does not"], [/\bdon't\b/gi, "do not"],
  [/\bcan't\b/gi, "cannot"], [/\bwon't\b/gi, "will not"],
  [/\bit's\b/gi, "it is"], [/\bwe're\b/gi, "we are"], [/\bi'm\b/gi, "I am"],
];
export function pageRewrite(text, instruction) {
  const instr = (instruction || "").toLowerCase();
  let out = text.trim();
  if (/upper ?case|all caps|shout/.test(instr)) out = out.toUpperCase();
  else if (/title ?case|title/.test(instr))
    out = out.toLowerCase().replace(/(^|\s)(\S)/g, (_, s, c) => s + c.toUpperCase());
  else {  // the stand-in's one real trick: formal
    for (const [re, sub] of FORMAL) out = out.replace(re, sub);
    out = out.replace(/\s+/g, " ").trim();
    out = capitalized(out);
    if (!/[.!?…"']$/.test(out)) out += ".";
  }
  return out;
}
