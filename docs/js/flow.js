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
// a lowercase recognizer's "i", "i'm", "i'll": the pronoun is always a capital
// days and months a lowercase recognizer writes small; not "may"/"march" (verbs)
const PROPER_WORDS = new Set(("monday tuesday wednesday thursday friday saturday sunday january february " +
  "april june july august september october november december").split(" "));
// initialisms a lowercase recognizer writes small; never ones that are words ("us", "it")
const ACRONYMS = new Set("ok tv usa uk faq pdf url api ai ceo eta asap fyi diy gps html css json sql usb".split(" "));
const PRONOUN_I = /^i(?:['\u2019](?:m|ll|d|ve))?[.,!?;:]*$/;
const core = (t) => t.toLowerCase().replace(PUNCT_STRIP, "").replace(/^[.,!?;:]+/, "");  // both ends, as grammar.py _core

const COMMANDS = {
  // "undo that", "strike that" are everyday words ("I can't undo that
  // decision"); "scratched that" is how recognizers write the command
  "scratch that": "scratch", "scratched that": "scratch", "delete that": "scratch",
  "new line": "\n", "new paragraph": "\n\n", "new bullet": "bullet", "new number": "number",
  "new heading": "heading", "new subheading": "subheading", "new checkbox": "checkbox",
  // recase the last utterance, said on its own (grammar.py _RECASE)
  "cap that": "recase:title", "capitalize that": "recase:title",
  "uppercase that": "recase:upper", "lowercase that": "recase:lower",
};
const MARKERS = { bullet: "- ", heading: "# ", subheading: "## ", checkbox: "- [ ] " };  // grammar.py _MARKERS
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
  "asterisk": ["*", "none", false], "hash sign": ["#", "right", false],
  "less than sign": ["<", "none", false],
  "greater than sign": [">", "none", false], "caret sign": ["^", "none", false],
};
// [flow.vocabulary] — the documented example ships live on this page
const VOCAB = { "hyper furion": "HyperFurion" };

// phrase table: tokens -> ["punct", spec] | ["cmd", action] | ["vocab", text]
const PHRASES = new Map();
for (const [p, spec] of Object.entries(PUNCT)) PHRASES.set(p, ["punct", spec]);
for (const [p, act] of Object.entries(COMMANDS)) PHRASES.set(p, ["cmd", act]);
PHRASES.set("literal", ["cmd", "literal"]);
// "emoji thumbs up" -> 👍 (grammar.py DEFAULT_EMOJI): one code point each
const EMOJI = { "emoji thumbs up": "👍", "emoji thumbs down": "👎", "emoji smile": "🙂", "emoji grin": "😁", "emoji laughing": "😂", "emoji wink": "😉", "emoji sad": "😢", "emoji crying": "😭", "emoji thinking": "🤔", "emoji heart eyes": "😍", "emoji fire": "🔥", "emoji party": "🎉", "emoji check mark": "✅", "emoji cross mark": "❌", "emoji eyes": "👀", "emoji pray": "🙏", "emoji rocket": "🚀", "emoji clap": "👏", "emoji hundred": "💯", "emoji shrug": "🤷", "emoji wave": "👋", "emoji sparkles": "✨", "emoji star": "⭐", "emoji skull": "💀", "emoji facepalm": "🤦", "emoji ok hand": "👌", "emoji muscle": "💪" };
for (const [p, r] of Object.entries(EMOJI)) PHRASES.set(p, ["vocab", r, "emoji"]);
for (const [p, r] of Object.entries(VOCAB)) PHRASES.set(p, ["vocab", r]);
const MAX_PHRASE = 3;

// code: terminals and code registers get no emoji (grammar.py: a character
// with no key is pasted, and a terminal must never be sent a paste)
function matchPhrase(cores, i, maxLen, code = false) {
  const limit = Math.min(MAX_PHRASE, cores.length - i, maxLen);
  for (let len = limit; len >= 1; len--) {
    const entry = PHRASES.get(cores.slice(i, i + len).join(" "));
    if (entry && !(code && entry[2] === "emoji")) return [entry, len];
  }
  return [null, 0];
}
function couldExtend(cores, i, code = false) {
  const tail = cores.slice(i).join(" ");
  if (!tail || cores.length - i >= MAX_PHRASE) return false;
  for (const [p, entry] of PHRASES)
    if (p.length > tail.length && p.startsWith(tail + " ") && !(code && entry[2] === "emoji")) return true;
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
// Prose keeps spoken numbers as words, except right before a unit that
// makes the reading certain: "twenty five percent" -> "25%", "five dollars"
// -> "$5", "three thirty pm" -> "3:30 PM" (numbers.py fold_unit).
const UNIT_WORDS = new Set(["percent", "dollar", "dollars", "euro", "euros", "yen", "am", "pm", "a.m", "p.m"]);
const SCALE_WORDS = new Set(["million", "billion", "trillion"]);  // "$3.2 billion"
const CURRENCY = { dollar: "$", dollars: "$", euro: "€", euros: "€", yen: "¥" };  // not "pounds" (weight)
const MERIDIEM = { am: "AM", "a.m": "AM", pm: "PM", "p.m": "PM" };
function clock(words) {
  if (!words.length || !(words[0] in UNITS) || UNITS[words[0]] < 1 || UNITS[words[0]] > 12) return null;
  const hour = UNITS[words[0]];
  if (words.length === 1) return String(hour);
  if (words.length === 3 && words[1] === "oh" && words[2] in DIGITS) return hour + ":0" + DIGITS[words[2]];
  const minutes = parseCardinal(words.slice(1));
  if (minutes === null || minutes < 10 || minutes > 59 || words[1] === "and") return null;
  return hour + ":" + String(minutes).padStart(2, "0");
}
export function foldUnit(words, unit, scale = false) {
  words = words.map((w) => w.toLowerCase()); unit = unit.toLowerCase();
  if (unit in MERIDIEM) { const c = clock(words); return c ? c + " " + MERIDIEM[unit] : null; }
  let amount;
  if (words.length === 1 && /^\d+(\.\d+)?$/.test(words[0])) amount = words[0];
  else if (words.includes("point") || parseCardinal(words) !== null) amount = parseNumberRun(words);
  else return null;
  if (amount === null) return null;
  if (unit === "percent") return amount + "%";
  let [whole, frac] = amount.split(".");
  if (whole.length > 3 && !whole.startsWith("0")) whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");  // "$1,500", "¥2,000"
  if (frac !== undefined && frac.length === 1 && !scale && "$€".includes(CURRENCY[unit])) frac += "0";  // "$1.50", but "$3.2 billion"
  return CURRENCY[unit] + whole + (frac !== undefined ? "." + frac : "");
}
// "october sixth" -> "October 6": a month, then an ordinal day (numbers.py
// parse_day); never "may"/"march" (verbs), never a cardinal ("in june
// twenty people came" stays words)
const DATE_MONTHS = new Set("january february april june july august september october november december".split(" "));
const ORDINALS = { first: 1, second: 2, third: 3, fourth: 4, fifth: 5, sixth: 6, seventh: 7, eighth: 8, ninth: 9,
  tenth: 10, eleventh: 11, twelfth: 12, thirteenth: 13, fourteenth: 14, fifteenth: 15, sixteenth: 16,
  seventeenth: 17, eighteenth: 18, nineteenth: 19, twentieth: 20, thirtieth: 30 };
export function parseDay(words) {
  words = words.map((w) => w.toLowerCase());
  if (words.length === 1 && words[0] in ORDINALS) return ORDINALS[words[0]];
  if (words.length === 2 && (words[0] === "twenty" || words[0] === "thirty") && words[1] in ORDINALS && ORDINALS[words[1]] <= 9) {
    const day = TENS[words[0]] + ORDINALS[words[1]];
    return day <= 31 ? day : null;
  }
  return null;
}
const MONTH_DAYS = { january: 31, february: 29, april: 30, june: 30, july: 31, august: 31,
  september: 30, october: 31, november: 30, december: 31 };
// an ordinal that starts a noun phrase, not a day: "september second graders"
const NOT_A_DAY_AFTER = new Set("grader graders grade half quarter time times place round floor class year".split(" "));
const PUNCT_HEAD = /^[.,!?;:]+/;
const clean = (t) => !PUNCT_TAIL.test(t) && !PUNCT_HEAD.test(t);  // nothing attached: "five," "...five"
function dateAt(items, at, checkAfter = true) {
  const month = items[at].text;
  if (!clean(month) || !DATE_MONTHS.has(month.toLowerCase())) return null;
  for (const size of [2, 1]) {
    const last = at + size;
    if (last >= items.length || items.slice(at + 1, last + 1).some((x) => x.kind !== "word")) continue;
    const words = items.slice(at + 1, last + 1).map((x) => x.text);
    if (words.slice(0, -1).some((w) => !clean(w)) || PUNCT_HEAD.test(words[words.length - 1])) continue;
    const day = parseDay(words.map(core));
    if (day === null || day > (MONTH_DAYS[month.toLowerCase()] || 31)) continue;
    const after = last + 1 < items.length ? items[last + 1] : null;
    if (checkAfter && after && after.kind === "word" && clean(words[words.length - 1]) && NOT_A_DAY_AFTER.has(core(after.text))) return null;
    const suffix = (words[words.length - 1].match(PUNCT_TAIL) || [""])[0];
    return [month.slice(0, 1).toUpperCase() + month.slice(1) + " " + day + suffix, last];
  }
  return null;
}
// "and fifty cents" at `at` -> [".50", index of "cents"], or null
function centsAt(items, at) {
  if (at >= items.length || items[at].kind !== "word" || items[at].text.toLowerCase() !== "and") return null;
  let end = at + 1;
  while (end < items.length && items[end].kind === "word" && clean(items[end].text) &&
         NUMBER_WORDS.has(core(items[end].text)) && !["and", "point", "hundred", "thousand"].includes(core(items[end].text))) end += 1;
  if (end === at + 1 || end >= items.length || items[end].kind !== "word") return null;
  if (PUNCT_HEAD.test(items[end].text)) return null;
  if (core(items[end].text) !== "cent" && core(items[end].text) !== "cents") return null;
  const value = parseCardinal(items.slice(at + 1, end).map((x) => core(x.text)));
  return value !== null && value >= 1 && value <= 99 ? ["." + String(value).padStart(2, "0"), end] : null;
}
// seven or more digits read one by one -> "555-1234", ten -> "555-123-4567"
// (numbers.py fold_digits); fewer stay words, and so does counting
// a year read in pairs (numbers.py fold_year): "nineteen eighty four" ->
// "1984"; a 20xx year needs all three words and stops at 2039
// a count, not a year (numbers.py NOT_A_YEAR_AFTER)
const NOT_A_YEAR_AFTER = new Set(("people persons hours hour minutes minute seconds second page pages times items things " +
  "dollars dollar euros euro yen percent pounds miles feet meters kilometers points words " +
  "users students copies units calories kids years days weeks months").split(" "));
export function foldYear(words) {
  words = words.map((w) => w.toLowerCase());
  if ((words.length !== 2 && words.length !== 3) || (words[0] !== "nineteen" && words[0] !== "twenty")) return null;
  const century = words[0] === "nineteen" ? 19 : 20;
  const rest = words.slice(1);
  let year;
  if (rest[0] === "oh") {
    if (rest.length !== 2 || !Object.hasOwn(DIGITS, rest[1]) || !DIGITS[rest[1]]) return null;
    year = DIGITS[rest[1]];
  } else if (Object.hasOwn(TENS, rest[0])) {
    year = TENS[rest[0]];
    if (rest.length === 2) {
      if (!Object.hasOwn(DIGITS, rest[1]) || !DIGITS[rest[1]]) return null;
      year += DIGITS[rest[1]];
    }
  } else return null;
  if (century === 20 && (words.length !== 3 || year > 39)) return null;
  return String(century) + String(year).padStart(2, "0");
}
export function foldDigits(words) {
  words = words.map((w) => w.toLowerCase());
  if (words.length < 7 || words[0] === "oh") return null;
  if (words.some((w) => !(w in DIGITS) && w !== "oh")) return null;  // "oh" reads as zero inside a number
  const values = words.map((w) => DIGITS[w] || 0);
  const steps = new Set(values.slice(1).map((v, k) => v - values[k]));
  if (steps.size === 1 && (steps.has(1) || steps.has(-1))) return null;  // counting, not a number
  const d = values.join("");
  if (d.length === 7) return d.slice(0, 3) + "-" + d.slice(3);
  if (d.length === 10) return d.slice(0, 3) + "-" + d.slice(3, 6) + "-" + d.slice(6);
  return d;
}
// Prose folds of a spoken number (grammar.py _fold_units): a unit after it,
// a month before an ordinal, seven or more digits. A run that starts behind
// the fence ends with the committed item it starts in; at the molten tail a
// run is held while it might still grow or meet its unit.
function foldUnits(items, frozen, itemEnd, pendingFrom, flush, settled) {
  const out = [];
  const n = items.length;
  const molten = (at) => !flush && at >= frozen && at >= settled;
  let i = 0;
  let afterDate = -1;  // the index right after a date with nothing attached ("June 5")
  const yearComma = (at, s, e) => { if (at === afterDate) out.push({ kind: "punct", text: ",", mode: "left", sentenceEnd: false, s, e }); };
  while (i < n) {
    const it = items[i];
    const committed = it.s < frozen;
    const limit = committed ? itemEnd(it.s) : null;
    const inside = (x) => limit === null || x.e <= limit;
    const date = it.kind === "word" ? dateAt(items, i, !committed) : null;  // a committed date was decided when typed
    if (date && !committed && date[1] === n - 1 && molten(items[date[1]].s)) return { items: out, pendingFrom: it.s };  // the next word says date or noun
    if (date && inside(items[date[1]])) {
      out.push({ kind: "word", text: date[0], s: it.s, e: items[date[1]].e });
      i = date[1] + 1; afterDate = clean(items[date[1]].text) ? i : -1; continue;
    }
    // the run: clean number words, never starting on "and"/"oh", never ending on glue, never crossing the fence
    let end = i;
    while (end < n && items[end].kind === "word" && clean(items[end].text) && inside(items[end]) &&
           (NUMBER_WORDS.has(core(items[end].text)) || (end > i && core(items[end].text) === "oh"))) end += 1;
    if (end > i && core(it.text) === "and") end = i;
    while (end > i && ["and", "point", "oh"].includes(core(items[end - 1].text))) end -= 1;
    if (end === i && it.kind === "word" && clean(it.text) && /^[0-9]/.test(it.text)) end = i + 1;  // "25 percent"
    if (end === i) { out.push(it); i += 1; continue; }
    const before = out.length ? out[out.length - 1] : null;
    if (before && before.kind === "word" && NUMBER_WORDS.has(core(before.text)) && core(before.text) !== "and") {
      out.push(...items.slice(i, end)); i = end; continue;  // the rest of a number with something attached: half a number never folds
    }
    const words = items.slice(i, end).map((x) => core(x.text));
    const scale = end < n && items[end].kind === "word" ? items[end] : null;
    if (scale && clean(scale.text) && SCALE_WORDS.has(core(scale.text)) && inside(scale)) {
      if (end + 1 === n && !committed && (molten(scale.s) || (pendingFrom !== null && scale.e === pendingFrom)))
        return { items: out, pendingFrom: it.s };  // "two million" may yet be dollars
      const money = end + 1 < n && items[end + 1].kind === "word" ? items[end + 1] : null;
      if (money && Object.hasOwn(CURRENCY, core(money.text)) && inside(money) && !PUNCT_HEAD.test(money.text)) {
        const folded = foldUnit(words, core(money.text), true);
        if (folded !== null) {
          out.push({ kind: "word", text: folded + " " + core(scale.text) + (money.text.match(PUNCT_TAIL) || [""])[0], s: it.s, e: money.e });
          i = end + 2; continue;
        }
      }
    }
    const unit = end < n && items[end].kind === "word" ? items[end] : null;
    const uc = unit ? core(unit.text) : "";
    if (unit && UNIT_WORDS.has(uc) && inside(unit) && !PUNCT_HEAD.test(unit.text)) {
      let verb = false;
      if (uc === "am" && !committed) {
        const after = end + 1 < n ? items[end + 1] : null;
        if (!after && molten(unit.s)) return { items: out, pendingFrom: it.s };  // the next word decides: time or verb
        verb = !!after && after.kind === "word" && core(after.text) === "i";
      }
      let folded = verb ? null : foldUnit(words, uc);
      if (folded !== null) {
        let last = unit, lastAt = end;
        const cents = "$€".includes(folded[0]) && !folded.includes(".") ? centsAt(items, end + 1) : null;
        if (cents && inside(items[cents[1]])) { folded += cents[0]; last = items[cents[1]]; lastAt = cents[1]; }  // "$5.50"
        if (uc !== "a.m" && uc !== "p.m") folded += (last.text.match(PUNCT_TAIL) || [""])[0];  // "percent." keeps its period
        out.push({ kind: "word", text: folded, s: it.s, e: last.e });
        i = lastAt + 1; continue;
      }
    }
    if (end === n && !committed && pendingFrom !== null && items[end - 1].e === pendingFrom)
      return { items: out, pendingFrom: it.s };  // its unit may be what is pending
    if (end === n && molten(it.s) && end - i >= 2 && words.every((w) => w in DIGITS || w === "oh"))
      return { items: out, pendingFrom: it.s };  // a number read digit by digit may still grow
    let year = null;
    if (words[0] === "nineteen" || words[0] === "twenty") {
      // a year: its last word may carry the stop ("…four."), after an "oh" the run trimmed ("nineteen oh five.")
      let at = end, tail = [];
      if (at < n && items[at].kind === "word" && clean(items[at].text) && core(items[at].text) === "oh" && inside(items[at])) { at += 1; tail = ["oh"]; }
      const closer = at < n && items[at].kind === "word" && inside(items[at]) ? items[at] : null;
      const attached = !!closer && !clean(closer.text);
      if (attached && !PUNCT_HEAD.test(closer.text)) {
        year = foldYear([...words, ...tail, core(closer.text)]);
        if (year !== null) {
          yearComma(i, it.s, closer.e);
          out.push({ kind: "word", text: year + (closer.text.match(PUNCT_TAIL) || [""])[0], s: it.s, e: closer.e });
          i = at + 1; continue;
        }
      }
      if (at === n && molten(it.s) && (words.length + tail.length <= 2 || foldYear(words) !== null))
        return { items: out, pendingFrom: it.s };  // a year may still grow, or the next word says count
      year = tail.length ? null : foldYear(words);
      const after = end < n && items[end].kind === "word" && inside(items[end]) ? items[end] : null;
      if (year !== null && after && !committed) {
        const lead = after.text.toLowerCase().match(/^[a-z]+/);
        if (lead && lead[0] !== after.text.toLowerCase() && (NUMBER_WORDS.has(lead[0]) || lead[0] === "oh")) year = null;  // half a year never folds
        else if (NOT_A_YEAR_AFTER.has(core(after.text))) year = null;  // a count: "nineteen forty people"
      }
    }
    let digits = foldDigits(words);
    if (digits === null && year !== null) { digits = year; yearComma(i, it.s, items[end - 1].e); }
    if (digits !== null) out.push({ kind: "word", text: digits, s: it.s, e: items[end - 1].e });
    else out.push(...items.slice(i, end));  // a run that did not fold stays words, all of it
    i = end;
  }
  return { items: out, pendingFrom };
}
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
    // the longest prefix of the run that reads as a number: "one thousand
    // hundred" is 1000 then "hundred", the split it gets when "one thousand"
    // was typed before "hundred" was heard (numbers.py convert_numbers)
    let parsed = null;
    while (end > i) {
      const run = tokens.slice(i, end).map(core);
      if (!GLUE.has(run[run.length - 1])) {
        parsed = parseNumberRun(run);
        if (parsed !== null && (end - i > 1 || Math.abs(parseFloat(parsed)) >= minValue)) break;
        parsed = null;
      }
      end -= 1;
    }
    if (parsed !== null) {
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
  javascript: { name: "javascript", smartCaps: false, grammar: true, numbersOn: true, numbersMin: 0, compiler: "javascript" },
};

// ── the semantic compilers (code.py) — pure, prefix-stable folds ──────────
// "for i in range ten colon" -> `for i in range(10):`; "pipe grep dash i
// error" -> `| grep -i error`. Deterministic tables only; anything unknown
// falls through as a plain word. All carried context lives in the fold
// state's `pending` slot, so the molten commit/preview split stays exact.
const PY_GLYPHS = { dot: [".", "both"], equals: ["=", "none"], plus: ["+", "none"],
  minus: ["-", "none"], times: ["*", "none"], modulo: ["%", "none"], arrow: ["->", "none"] };
const SH_GLYPHS = { pipe: ["|", "none"], dot: [".", "both"], star: ["*", "right"], slash: ["/", "both"], plus: ["+", "right"] };  // "chmod plus x"
const SH_PAIRS = { "greater than": ">", "less than": "<", "and and": "&&" };
// a path after one of these starts a new argument: "cd slash etc" -> "cd /etc"
const SH_COMMANDS = new Set(("cd ls cat tail head less more vim vi nano code rm cp mv mkdir rmdir touch chmod chown " +
  "find grep source open sudo echo tree du df stat ln tar unzip zip scp rsync").split(" "));
const PY_CALLABLES = new Set(["range", "print", "len", "str", "int", "float", "input",
  "enumerate", "sorted", "reversed", "abs", "min", "max", "sum", "type", "repr"]);
// Python's constants said in lowercase ("is not none" -> None); string
// prefixes a quote glues to (f"…"); shell glues = and : ("FOO=bar", "8080:80")
const PY_CONSTANTS = { none: "None", true: "True", false: "False" };
const STRING_PREFIXES = new Set(["f", "r", "b", "rb", "br", "fr", "rf", "u"]);
const SH_GLUED = { "=": "both", ":": "both" };
// two spoken words, one operator; the first is held until the next says
// whether it was half an operator ("if not x" types "not")
const PY_PAIRS = { "double equals": "==", "not equals": "!=", "double equal": "==", "not equal": "!=", "less than": "<", "greater than": ">", "value error": "ValueError", "type error": "TypeError", "key error": "KeyError", "index error": "IndexError", "runtime error": "RuntimeError", "attribute error": "AttributeError", "import error": "ImportError", "name error": "NameError", "assertion error": "AssertionError", "lookup error": "LookupError", "permission error": "PermissionError", "timeout error": "TimeoutError", "connection error": "ConnectionError", "os error": "OSError", "memory error": "MemoryError", "recursion error": "RecursionError", "stop iteration": "StopIteration", "keyboard interrupt": "KeyboardInterrupt" };  // operators and builtin exceptions (code.py _PYTHON_PAIRS)
const AUGMENTED = new Set(["+", "-", "*", "/", "%", "<", ">", "!", "=", "//", "**"]);
// JavaScript / TypeScript (code.py compile_javascript)
const JS_GLYPHS = { dot: [".", "both"], equals: ["=", "none"], plus: ["+", "none"], minus: ["-", "none"],
  times: ["*", "none"], modulo: ["%", "none"], arrow: ["=>", "none"] };
const JS_PAIRS = { "triple equals": "===", "double equals": "==", "not equals": "!==", "not equal": "!==",
  "less than": "<", "greater than": ">", "and and": "&&", "or or": "||", "fat arrow": "=>" };
const JS_KEYWORDS = new Set(("await break case catch class const continue debugger default delete do else export extends " +
  "finally for function if import in instanceof let new of return super switch this throw try " +
  "typeof var void while with yield async static get set").split(" "));
const COMPILERS = {
  python: { glyphs: PY_GLYPHS, callables: PY_CALLABLES, dashHold: false, glueCalls: true, constants: PY_CONSTANTS, pairs: PY_PAIRS, prefixes: STRING_PREFIXES },
  javascript: { glyphs: JS_GLYPHS, callables: new Set(), dashHold: false, glueCalls: true, pairs: JS_PAIRS, keywords: JS_KEYWORDS },
  shell:  { glyphs: SH_GLYPHS, callables: new Set(), dashHold: true, glued: SH_GLUED, pairs: SH_PAIRS, dotHold: true },
};
// a name an opening paren or bracket glues to ("get_user (" -> get_user();
// keywords keep their space ("if (", "in [")
const PY_KEYWORDS = new Set(("False None True and as assert async await break class continue def del elif else except " +
  "finally for from global if import in is lambda nonlocal not or pass raise return try while with yield _ case match type").split(" "));
const isName = (t, keywords = PY_KEYWORDS) => /^[A-Za-z_][\w.]*$/.test(t) && !keywords.has(t);
function compileCode(items, state, { glyphs, callables, dashHold, glueCalls, constants = null, glued = null, pairs = null, dotHold = false, keywords = PY_KEYWORDS, prefixes = new Set() }) {
  const out = [];
  let atStart = state.atStart, glueNext = state.glueNext, pending = state.pending || "", afterName = !!state.afterName;
  let openCalls = state.openCalls || 0, innerParens = state.innerParens || 0, lastAtom = state.lastAtom || "";
  const firsts = new Set(Object.keys(pairs || {}).map((k) => k.split(" ")[0]));
  const emit = (text, glueLeft, name = false) => {
    if (text === "=" && glueCalls && AUGMENTED.has(lastAtom)) glueLeft = true;  // "+=", "==", "<=", "!="
    if (!atStart && !glueNext && !glueLeft) out.push(" ");
    out.push(text); atStart = false; glueNext = false; afterName = name; lastAtom = text;
  };
  const emitMode = (glyph, mode) => {
    if (mode === "left") emit(glyph, true);
    else if (mode === "right") { emit(glyph, false); glueNext = true; }
    else if (mode === "both") { emit(glyph, true); glueNext = true; }
    else emit(glyph, false);
  };
  const word = (text, plain = false) => {
    const c = text.toLowerCase();
    if (pending === "call-open") pending = "call";
    const g = Object.hasOwn(glyphs, c) ? glyphs[c] : null;
    if (g) {
      if (dotHold && g[0] === "/" && (SH_COMMANDS.has(lastAtom) || lastAtom[0] === "-")) { emit("/", false); glueNext = true; return; }  // "cd slash etc"
      emitMode(g[0], g[1]); return;
    }
    if (callables.has(c) && lastAtom !== "->" && !plain) { emit(text + "(", false); glueNext = true; pending = "call-open"; openCalls += 1; return; }  // calls nest; after "->" it is a type
    if (constants && Object.hasOwn(constants, c)) { emit(constants[c], false); return; }
    emit(text, false, !!glueCalls && isName(text, keywords));
  };
  const flushDash = () => {
    if (pending === "dash" || pending === "dashes") { emit(pending === "dashes" ? "--" : "-", false); pending = ""; }
    else if (pending.startsWith("hold:")) { const held = pending.slice(5); pending = ""; word(held); }  // the held word was a word
    else if (pending === "dot") { pending = ""; emit(".", false); }  // "find dot dash name": the dot is a path
    else if (pending.startsWith("dothold:")) { const held = pending.slice(8); pending = ""; gluedDot(held); word(held); }
  };
  // "file dot txt" glues; after a command ("source dot venv") or before a
  // slash ("dot slash run") the dot starts a path
  const gluedDot = (following) => {
    const f = following.toLowerCase();
    const slash = Object.hasOwn(glyphs, f) && glyphs[f][0] === "/";
    emit(".", !slash && !SH_COMMANDS.has(lastAtom)); glueNext = true;
  };
  for (const it of items) {
    if (it.kind === "flush") { flushDash(); continue; }  // the dictation ended: a hold is typed as said
    if (it.kind === "break" && (it.mode === "bullet" || it.mode === "number")) continue;  // a list item means nothing in code
    if (it.kind === "break") { flushDash(); pending = ""; openCalls = 0; innerParens = 0; out.push(it.text); atStart = false; glueNext = true; afterName = false; }
    else if (it.kind === "punct") {
      if (pending === "dot" && it.mode === "left") { pending = ""; emit(".", true); }  // "done dot unquote": the dot ends it
      if (pending.startsWith("hold:") || pending.startsWith("dothold:") || pending === "dot") flushDash();  // the held word was a word ("type (" opens its call)
      if (pending === "call-open" && it.text === "(" && it.mode === "right") { pending = "call"; continue; }  // the callable opened it
      if (openCalls && it.text === ":") { emit(")".repeat(innerParens + openCalls) + ":", true); pending = ""; openCalls = innerParens = 0; continue; }  // a colon closes every open call
      if (dashHold && it.text === "-" && it.mode === "none") {  // the next word becomes a flag: "-i", "--rm"
        if (pending === "dash") { pending = "dashes"; continue; }
        flushDash(); pending = "dash"; continue;
      }
      flushDash();
      if (prefixes.has(lastAtom.toLowerCase()) && it.text === '"' && it.mode === "right") { emit('"', true); glueNext = true; continue; }  // f"…"
      if (pending === "call-open") pending = openCalls ? "call" : "";
      if (it.text === ")" && innerParens) innerParens -= 1;  // closes a paren said inside the call
      else if (openCalls && it.text === ")") { openCalls -= 1; if (!openCalls) pending = ""; }
      else if (openCalls && it.text === "(") innerParens += 1;
      if (glueCalls && afterName && "([".includes(it.text) && it.mode === "right") { emit(it.text, true); glueNext = true; continue; }
      emitMode(it.text, glued && Object.hasOwn(glued, it.text) ? glued[it.text] : it.mode);
    } else if (it.kind === "word") {
      const c = it.text.toLowerCase();
      if (dotHold && c === "dot") {  // held: "file dot txt" glues, "cd dot dot" is "..", a dot before a flag or the end is a path
        if (pending === "dot") { pending = ""; emit("..", false); continue; }
        flushDash(); pending = "dot"; continue;
      }
      if (pending.startsWith("dothold:")) {
        const held = pending.slice(8), key = held.toLowerCase() + " " + c;
        if (pairs && Object.hasOwn(pairs, key)) { pending = ""; emit(".", false); emit(pairs[key], false); continue; }  // "git add dot and and": a path, then &&
        flushDash();  // "style dot less": an extension after all
      }
      if (pending === "dot") {
        pending = "";
        if (firsts.has(c)) { pending = "dothold:" + it.text; continue; }  // "dot and and" or "dot less"?
        gluedDot(it.text);
      }
      if (pending === "dash" || pending === "dashes") { emit((pending === "dashes" ? "--" : "-") + it.text, false); pending = ""; continue; }
      if (pending.startsWith("hold:")) {
        const key = pending.slice(5).toLowerCase() + " " + c;
        if (pairs && Object.hasOwn(pairs, key)) { pending = ""; emit(pairs[key], false, !!glueCalls && isName(pairs[key], keywords)); continue; }  // "double equals" -> "=="
        const held = pending.slice(5); pending = "";
        word(held, Object.hasOwn(glyphs, c) && glyphs[c][1] === "none");  // before an operator a callable is a name: "type = 5"
      }
      if (firsts.has(c)) { if (pending === "call-open") pending = openCalls ? "call" : ""; pending = "hold:" + it.text; continue; }
      word(it.text);
    }
  }
  return { text: out.join(""), st: { ...state, atStart, glueNext, capNext: false, pending, afterName, openCalls, innerParens, lastAtom } };
}
// at the end of a dictation (code.py flush_code): a held dash or half an
// operator is typed as said
function flushCode(st, reg) {
  const p = st.pending || "";
  if (!reg.compiler || !COMPILERS[reg.compiler] || !(p === "dash" || p === "dashes" || p === "dot" || p.startsWith("hold:") || p.startsWith("dothold:"))) return { text: "", st };
  return compileCode([{ kind: "flush" }], st, COMPILERS[reg.compiler]);
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
// words a speaker restarts on (grammar.py STUTTER_WORDS); never ones a
// sentence can say twice ("had had", "that that")
const STUTTER_WORDS = new Set("the a an i to and we you my in of for on at with i'm it's i'll we're they".split(" "));
// hesitation sounds a streaming recognizer writes down: dropped ([flow] fillers)
export const DEFAULT_FILLERS = ["um", "umm", "uh", "uhh", "uhm", "erm"];
const SENTENCE_STOPS = ".?!";
// spoken addresses: "docs dot python dot org" -> docs.python.org, "liam at
// example dot com" -> liam@example.com; a run only becomes an address when
// it ends in one of these, so "meet at the office" stays prose
// never English words ("in", "at", "it", "to", "so", "me", "no", "be", "us")
const TLDS = new Set(("com org net io dev ai app co edu gov uk de fr ca info biz xyz sh gg tv " +
  "eu nl se es jp au nz ch ly cc").split(" "));
const ADDRESS_GLUE = { dot: ".", at: "@", slash: "/" };
const isGlue = (c) => Object.hasOwn(ADDRESS_GLUE, c);
const addressPart = (c) => !!c && /^[a-z0-9-]+$/.test(c) && /[a-z0-9]/.test(c);
// a spoken domain or email at `index`, read no further than `limit`:
// [text, end]; PENDING while an undecided tail could still become one; null
function addressAt(cores, index, limit, decided, fillers) {
  if (index + 1 >= limit) return null;  // a lone word at the tail is just a word, for now
  const first = cores[index];
  if (!addressPart(first) || isGlue(first) || fillers.has(first) || pauses.COMMON_LOWER.has(first)) return null;  // "the dot com bubble"
  if (!isGlue(cores[index + 1])) return null;
  const parts = [first], seps = [];
  let cursor = index + 1, best = null;
  for (;;) {
    if (cursor >= limit) return decided ? best : PENDING;  // the run touches an open tail
    const sep = cores[cursor];
    if (sep === "slash" && (best === null || seps.includes("@"))) return best;  // a path only follows a whole domain
    if (!isGlue(sep) || (sep === "at" && (seps.includes("@") || seps.includes("/")))) return best;
    if (cursor + 1 >= limit) return decided ? best : PENDING;
    const part = cores[cursor + 1];
    if (!addressPart(part) || (sep === "dot" && seps.includes("/") && pauses.COMMON_LOWER.has(part))) return best;  // "slash docs dot then": a word
    seps.push(ADDRESS_GLUE[sep]); parts.push(part);
    cursor += 2;
    const at = seps.indexOf("@");
    if (seps.includes("/") || (seps[seps.length - 1] === "." && TLDS.has(part) && (at === -1 || at < seps.length - 1)))
      best = [parts.map((p, k) => p + (seps[k] || "")).join(""), cursor];
  }
}
// spoken case formatters: "snake case user id" -> user_id; the formatter
// takes the plain words after it up to punctuation, a command or a pause
const FORMATTERS = new Map([
  ["camel case", "camel"], ["pascal case", "pascal"], ["snake case", "snake"], ["kebab case", "kebab"],
  ["constant case", "constant"], ["title case", "title"], ["all caps", "caps"], ["no space", "smash"], ["dot case", "dot"],
]);
const FORMATTER_FIRST = new Set([...FORMATTERS.keys()].map((k) => k.split(" ")[0]));
const MAX_FORMATTED_WORDS = 8;
// operator words the code registers compile end a run; in a code register
// so do keywords and spoken callables
const FORMATTER_STOPS = new Set("equals plus minus times modulo arrow dot pipe star slash".split(" "));
const CODE_FORMATTER_STOPS = new Set([...FORMATTER_STOPS, ...("in is not and or if elif else for while return import from as with def class " +
  "lambda yield await async try except finally raise pass break continue global " +
  "range print len str int float input enumerate sorted reversed abs min max sum type repr").split(" ")]);
const TITLE_SMALL = new Set("a an and as at but by for in nor of on or the to vs via".split(" "));
const cap1 = (w) => w.slice(0, 1).toUpperCase() + w.slice(1);
export function formatWords(style, words) {
  words = words.filter(Boolean);
  if (style === "camel") return words[0] + words.slice(1).map(cap1).join("");
  if (style === "pascal") return words.map(cap1).join("");
  if (style === "snake") return words.join("_");
  if (style === "kebab") return words.join("-");
  if (style === "constant") return words.join("_").toUpperCase();
  if (style === "title") return words.map((w, k) => (k && TITLE_SMALL.has(w) ? w : cap1(w))).join(" ");
  if (style === "caps") return words.join(" ").toUpperCase();
  if (style === "dot") return words.join(".");
  return words.join("");
}
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
  const codeReg = !!(register && (register.compiler || register.terminal));
  const reg = register || REGISTERS.prose;
  if (!reg.grammar) {
    return { items: tokens.map((t, i) => ({ kind: "word", text: t, s: i, e: i + 1 })),
             pendingFrom: null };
  }
  const wake = ((cfg && cfg.wakeWord) || "vk").toLowerCase();
  const spelling = !cfg || cfg.spelling !== false;
  const addressOn = !cfg || cfg.addresses !== false;
  // formatters: "code" (default) in code and terminal registers only,
  // where "no space" is never prose; "everywhere"; "off"
  const fmode = !cfg || cfg.formatters === undefined || cfg.formatters === true ? "code" : cfg.formatters === false ? "off" : cfg.formatters;
  const formattersOn = fmode === "everywhere" || (fmode === "code" && !!(reg.compiler || reg.terminal));
  const stops = reg.compiler ? CODE_FORMATTER_STOPS : FORMATTER_STOPS;
  // "camel case get user name" at i: [[items], next]; PENDING while the
  // words could still continue; null when this is not a formatter
  const parseFormatter = (index, limit, decided) => {
    if (index + 1 >= limit) return decided ? null : PENDING;  // "snake" may become "snake case"
    const style = FORMATTERS.get(cores[index] + " " + cores[index + 1]);
    if (!style) return null;
    let cursor = index + 2, stop = "";
    const words = [];
    while (cursor < limit && words.length < MAX_FORMATTED_WORDS) {
      const c = cores[cursor];
      if (!c || wakeAt(cores, cursor, wake) || fillers.has(c) || (words.length && stops.has(c))) break;
      if (FORMATTERS.has(c + " " + (cores[cursor + 1] || ""))) break;  // the next formatter starts
      if (c === "unquote" || (c === "end" && cores[cursor + 1] === "quote")) break;  // a quote closes
      if (matchPhrase(cores, cursor, limit - cursor, codeReg)[0]) break;
      words.push(c);
      stop = (tokens[cursor].match(PUNCT_STRIP) || [""])[0];
      cursor += 1;
      if (stop) break;  // "user id," — the comma ends the run
    }
    if (cursor >= limit && !decided && !stop && words.length < MAX_FORMATTED_WORDS) return PENDING;
    if (!words.length) return null;
    const out = [{ kind: "word", text: formatWords(style, words), mode: "verbatim", s: index, e: cursor }];
    for (const ch of (tokens[cursor - 1].match(PUNCT_STRIP) || [""])[0]) if (".,!?;:".includes(ch))
      out.push({ kind: "punct", text: ch, mode: "left", sentenceEnd: ".!?".includes(ch), s: index, e: cursor });
    return [out, cursor];
  };
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
  const closeQuote = new Map();  // where an "unquote" closes, and its length
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
    const prev = items.length ? items[items.length - 1] : null;
    if (fillers.size && !codeReg && i > 0 && STUTTER_WORDS.has(cores[i]) && tokens[i - 1].toLowerCase() === tokens[i].toLowerCase() &&
        clean(tokens[i]) && prev && prev.s === i - 1 && prev.e === i && (prev.kind === "word" || prev.kind === "filler")) {
      items.push({ kind: "filler", s: i, e: i + 1 });  // "the the meeting", "I I think": the repeat renders nothing
      i += 1; continue;
    }
    if (fillers.has(cores[i])) {
      // a hesitation sound renders nothing; a comma attached to it goes too
      items.push({ kind: "filler", s: i, e: i + 1 });
      fillerStop(tokens[i], items, i, i + 1);
      i += 1; continue;
    }
    if (formattersOn && FORMATTER_FIRST.has(cores[i])) {
      const [limit, decided] = limitAt(i);
      const f = parseFormatter(i, limit, decided);
      if (f === PENDING) { pendingFrom = i; break; }
      if (f) { items.push(...f[0]); i = f[1]; continue; }
    }
    // "quote ... unquote" / "quote ... end quote": the words between in
    // quotation marks, only with words between, within one utterance
    if (closeQuote.has(i)) {
      const e = i + closeQuote.get(i);
      items.push({ kind: "punct", text: '"', mode: "left", sentenceEnd: false, s: i, e });
      const tail = (tokens[e - 1].match(PUNCT_STRIP) || [""])[0];
      for (const ch of tail) if (".,!?;:".includes(ch))
        items.push({ kind: "punct", text: ch, mode: "left", sentenceEnd: ".!?".includes(ch), s: i, e });
      i = e; continue;
    }
    if (cores[i] === "quote") {
      if (cores[i + 1] === "unquote") {  // "his quote unquote friend": both stay words
        items.push({ kind: "word", text: tokens[i], s: i, e: i + 1 }, { kind: "word", text: tokens[i + 1], s: i + 1, e: i + 2 });
        i += 2; continue;
      }
      let end = tokens.length, decided = flush;
      if (i < settled) { decided = true; end = settled; for (const b of bounds) if (b > i) { end = b; break; } }
      if (!decided) { pendingFrom = i; break; }  // the closer may still come, or go
      let close = null;
      for (let k = i + 2; k < end && !close; k++) {
        if (cores[k] === "quote") break;  // another quote opens first: this one stays a word
        if (cores[k] === "unquote") close = [k, 1];
        else if (cores[k] === "end" && k + 1 < end && cores[k + 1] === "quote") close = [k, 2];
      }
      if (close) {
        closeQuote.set(close[0], close[1]);
        items.push({ kind: "punct", text: '"', mode: "right", sentenceEnd: false, s: i, e: i + 1 });
        i += 1; continue;
      }
    }
    if (spelling && cores[i] === "correct" && bounds.includes(i)) {
      // "correct monday to friday", said on its own: the engine swaps the
      // last "monday" it typed; decided at the end of the segment
      const [limit, decided] = limitAt(i);
      if (!decided) { pendingFrom = i; break; }
      const rest = cores.slice(i + 1, limit);
      let split = null;
      for (let k = 1; k < rest.length - 1; k++) if (rest[k] === "to") { split = k; break; }
      // "Correct. Go to the store.": an answer, not a command — the command
      // has no punctuation on "correct" or in what it corrects
      const clean = (t) => !/[.,!?;:]/.test(t[0] || "") && !/[.,!?;:]$/.test(t);
      if (split !== null && tokens.slice(i, i + 2 + split).every(clean)) {
        const old = tokens.slice(i + 1, i + 1 + split).map((t) => t.toLowerCase()).join(" ");
        const neu = tokens.slice(i + 2 + split, limit).join(" ").trim();  // as spoken: the engine renders it
        if (old && neu) { items.push({ kind: "correct", text: neu, mode: old, s: i, e: limit }); i = limit; continue; }
      }
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
    const [entry, used] = matchPhrase(cores, i, fence, codeReg);
    if (!entry && !flush && i >= frozen && couldExtend(cores, i, codeReg)) { pendingFrom = i; break; }
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
      } else if (payload.startsWith("recase:")) {
        items.push({ kind: "recase", text: tokens.slice(i, i + used).join(" "), mode: payload.slice(7), s: i, e: i + used });
      } else if (payload in MARKERS) {
        // "- ", "# ", … on a line of its own: the renderer adds the line
        // break unless the caret is already at a line start
        items.push({ kind: "break", text: MARKERS[payload], mode: "bullet", s: i, e: i + used });
      } else if (payload === "number") {
        items.push({ kind: "break", text: "", mode: "number", s: i, e: i + used });  // "1. ", "2. ": the renderer counts
      } else {  // "\n" | "\n\n"
        items.push({ kind: "break", text: payload, s: i, e: i + used });
      }
      i += used; continue;
    }
    {
      const limit = i < frozen ? itemEnd(i) : tokens.length;
      const address = addressOn ? addressAt(cores, i, limit, i < settled || flush || limit < tokens.length, fillers) : null;
      if (address === PENDING) { pendingFrom = i; break; }  // "liam at" may still become an address
      if (address) {
        const [text, end] = address;
        items.push({ kind: "word", text, mode: "verbatim", s: i, e: end });  // never auto-capitalized
        const tail = (tokens[end - 1].match(PUNCT_STRIP) || [""])[0];
        for (const ch of tail) if (".,!?;:".includes(ch))
          items.push({ kind: "punct", text: ch, mode: "left", sentenceEnd: ".!?".includes(ch), s: i, e: end });
        i = end; continue;
      }
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
        const before = out.length ? out[out.length - 1] : null;
        if (!run.length && Object.hasOwn(DIGITS, core(it.text)) && before && before.kind === "punct" && before.text === "-" && before.mode === "none") {
          run.push(it); close(false); continue;  // "kill dash nine one two three four": a flag is one digit (-9 1234)
        }
        run.push(it);
      } else { close(false); out.push(it); }
    }
    close(run.length > 0 && run[0].s >= frozen);  // a run behind the fence was decided when committed
    return { items: out, pendingFrom };
  }
  const code = !!(reg.compiler || reg.terminal);
  if ((!cfg || !cfg.numbers || cfg.numbers === "auto") && !code) return foldUnits(items, frozen, itemEnd, pendingFrom, flush, settled);
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
  return { atStart: true, glueNext: false, capNext: (register || REGISTERS.prose).smartCaps, pending: "", lineStart: false };
}
// the state a recording starts in when it continues text the previous one
// left at the caret ([flow] rejoin): a space before its first word, and a
// capital only if that text ended a sentence; null when there is nothing
// to continue (no text, or it ended in whitespace or a line break)
export function continuationState(previousTail, register) {
  const tail = previousTail ? previousTail.slice(-1) : "";
  if (!tail || /\s/.test(tail)) return null;
  return { atStart: false, glueNext: false, capNext: !!(register || REGISTERS.prose).smartCaps && ".!?".includes(tail), pending: "", lineStart: false };
}
export function render(items, register, state) {
  const reg = register || REGISTERS.prose;
  // in a terminal a line break is Enter, and Enter runs the command: a
  // spoken "new line" renders as nothing there — only a hand sends
  if (reg.terminal) items = items.filter((it) => it.kind !== "break");
  const st = state ? { ...state } : initialState(reg);
  if (reg.compiler && COMPILERS[reg.compiler]) return compileCode(items, st, COMPILERS[reg.compiler]);
  const out = [];
  if (st.lineStart === undefined) st.lineStart = false;
  const emit = (text, glueLeft) => {
    if (!st.atStart && !st.glueNext && !glueLeft) out.push(" ");
    out.push(text); st.atStart = false; st.glueNext = false; st.lineStart = false;
  };
  for (const it of items) {
    if (it.kind === "break") {
      if (it.mode === "number") { st.listNumber = (st.listNumber || 0) + 1; out.push((st.lineStart ? "" : "\n") + st.listNumber + ". "); }
      else if (it.mode === "bullet") { out.push((st.lineStart ? "" : "\n") + it.text); if (it.text.startsWith("#")) st.listNumber = 0; }  // a heading starts a new list
      else { out.push(it.text); if (it.text.includes("\n\n")) st.listNumber = 0; }  // a new paragraph starts a new list
      st.lineStart = it.mode !== "bullet" && it.mode !== "number";
      st.atStart = false; st.glueNext = true; st.capNext = reg.smartCaps;
    }
    else if (it.kind === "punct") {
      if (it.mode === "left") emit(it.text, true);
      else if (it.mode === "right") { emit(it.text, false); st.glueNext = true; }
      else if (it.mode === "both") { emit(it.text, true); st.glueNext = true; }
      else emit(it.text, false);
      if (it.sentenceEnd && reg.smartCaps) st.capNext = true;
      if (it.text === "#" || it.text === "@") st.capNext = false;  // #tags and @mentions stay as said
    } else if (it.kind === "word") {
      let t = it.text;
      const tc = t.replace(PUNCT_STRIP, "");
      if (reg.smartCaps && it.mode !== "verbatim" && ACRONYMS.has(tc.toLowerCase())) t = tc.toUpperCase() + t.slice(tc.length);
      if (reg.smartCaps && it.mode !== "verbatim" && (st.capNext || PRONOUN_I.test(t) || PROPER_WORDS.has(t.replace(/[.,!?;:'\u2019s]+$/, "").toLowerCase()))) t = capitalized(t);
      emit(t, false);
      st.capNext = reg.smartCaps && ENDERS.test(t.trimEnd());
    }
    // scratch/instruction/key render nothing; the engine acts on them
  }
  return { text: out.join(""), st };
}

// char-counted backspacing over `text` may not match how the focused app
// groups grapheme clusters (astral plane, combining marks, ZWJ sequences)
// the spoken words of a phrase as typed text: case-insensitive, whole words,
// any punctuation or spacing between them (engine.py _phrase_pattern)
function phrasePattern(phrase) {
  const words = phrase.split(/\s+/).filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  // never inside a word ("don’t"), never across a line break or list marker
  return new RegExp("(?<![\\p{L}\\p{N}_'’])" + words.join("[^\\p{L}\\p{N}\\n]+") + "(?![\\p{L}\\p{N}_'’])", "giu");
}
// "title": each word's first character a capital; "upper"; "lower" (engine.py recase)
export function recase(text, mode) {
  if (mode === "upper") return text.toUpperCase();
  if (mode === "lower") return text.toLowerCase();
  return text.replace(/(^|\s)(\S)/g, (m, a, b) => a + b.toUpperCase());
}
export function riskyBackspace(text) {
  return /[\u{10000}-\u{10FFFF}\p{M}‍️︎]/u.test(text);  // as engine.py: astral, any mark, joiners
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
const LAST_WORD = /(\S+?)([\u202f.,!?;:)\]}"'»”’“]*)(\s*)$/;
const FINAL_ONLY = new Set(["respell", "key", "recase", "correct"]);  // rewrite committed text: never on a stability guess
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
  let selectState = null;  // the fold state before the utterance "select that" covers
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
    // a "quote" waits for its utterance to close, never for the expiry: committed
    // as a word, it could not become an opening mark when the closer came
    const quoteWaits = pendingFrom !== null && tokens.slice(pendingFrom).some((t) => core(t) === "quote");  // anywhere in the held tail
    if (pendingFrom !== null && !pendingIsInstruction() && !quoteWaits) {
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
    if (barrier === null) settleHold();
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
    else if (action.action === "select:that" && selectState) st = selectState;  // the whole utterance is selected: pick up from before it
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
    // a recognizer's period on a command ("Undo that.") is not a pause to review: never hold it
    for (const [index, pause] of [...pauseMap]) {
      const it = pause.decision === null ? itemAt(index - 1) : null;
      if (it && (it.kind === "key" || it.kind === "recase" || it.kind === "scratch")) pauseMap.delete(index);
    }
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
  // type what a code compiler still holds (a dash, a dot, half an operator)
  function settleHold() { const f = flushCode(renderState, reg); committedRender += f.text; renderState = f.st; }
  function commitItem(it) {
    // an edit or a key acts on what is on screen: a held word is typed first
    if (["scratch", "respell", "key", "recase", "correct"].includes(it.kind)) settleHold();
    for (const [index, pause] of pauseMap)
      if (pause.decision === null && it.s < index && index <= it.e)
        pause.decision = pause.provisional || pauses.keep(index < tokens.length ? tokens[index] : "");  // forced out: what shows, stays
    if (it.kind === "scratch") applyScratch();
    else if (it.kind === "respell" && it.mode === "replace") applyRespell(it.text);
    else if (it.kind === "respell") append([{ kind: "word", text: it.text, s: it.s, e: it.e }]);
    else if (it.kind === "key") commitKey(it);
    else if (it.kind === "recase") commitRecase(it);
    else if (it.kind === "correct") commitCorrect(it);
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
  // characters "select that" must cover: the last segment typed in this
  // recording, without its leading space; 0 (refused) when there is none
  // or shift+left can't count it (engine.py _last_utterance_length)
  function lastUtteranceLength() {
    let target = null;
    for (let i = snapshots.length - 1; i >= 0; i--) if (snapshots[i].len < committedRender.length) { target = snapshots[i]; break; }
    if (!target) return 0;
    const said = committedRender.slice(target.len);
    if (riskyBackspace(said)) return 0;
    selectState = target.st;
    return said.length;
  }
  // "correct monday to friday" as a whole segment: the last "monday" typed
  // in this recording becomes "friday", its capitals kept; mid-sentence, or
  // with nothing to correct, it was dictation (engine.py _commit_correct)
  function commitCorrect(it) {
    const whole = segmentBounds.has(it.s) && segmentBounds.has(it.e);
    const matches = whole ? [...committedRender.matchAll(phrasePattern(it.mode))] : [];
    if (!matches.length) {
      append(tokens.slice(it.s, it.e).map((t, k) => ({ kind: "word", text: t, s: it.s + k, e: it.s + k + 1 })));
      return;
    }
    const m = matches[matches.length - 1], heard = m[0], at = m.index;
    if (riskyBackspace(committedRender.slice(at))) return;
    // Y as dictation: spoken punctuation, vocabulary, numbers
    const spoken = parse(it.text.split(/\s+/).filter(Boolean), { flush: true, register: reg, cfg }).items;
    let meant = render(spoken, reg, { ...initialState(reg), capNext: false }).text.trim();
    if (!meant) return;
    if (heard.length > 1 && heard === heard.toUpperCase() && heard !== heard.toLowerCase()) meant = meant.toUpperCase();
    else if (/^\p{Lu}/u.test(heard) && !/^i(['’](m|ll|d|ve))?$/i.test(heard)) meant = meant.slice(0, 1).toUpperCase() + meant.slice(1);
    committedRender = committedRender.slice(0, at) + meant + committedRender.slice(at + heard.length);
    const shift = meant.length - heard.length;
    // a boundary inside the replaced words falls back to its start
    snapshots = snapshots.map((sn) => (sn.len <= at ? sn : sn.len < at + heard.length ? { len: committedRender.slice(0, at).trimEnd().length, st: sn.st } : { len: sn.len + shift, st: sn.st }));
    if (heard !== meant) corrections.push([heard, meant]);
  }
  // "cap that" / "uppercase that" / "lowercase that" as a whole segment
  // recases the last utterance in place; mid-sentence it types as words
  function commitRecase(it) {
    if (!(segmentBounds.has(it.s) && segmentBounds.has(it.e))) {
      append(tokens.slice(it.s, it.e).map((t, k) => ({ kind: "word", text: t, s: it.s + k, e: it.s + k + 1 })));
      return;
    }
    let target = null;
    for (let i = snapshots.length - 1; i >= 0; i--) if (snapshots[i].len < committedRender.length) { target = snapshots[i]; break; }
    if (!target) return;
    const said = committedRender.slice(target.len), recased = recase(said, it.mode);
    if (riskyBackspace(said) || recased.length !== said.length) return;  // "straße" -> "STRASSE" would skew the snapshots
    committedRender = committedRender.slice(0, target.len) + recased;
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
    if (segmentBounds.has(it.s) && segmentBounds.has(it.e)) {
      barrier = { action: it.text, count: it.text === "select:that" ? lastUtteranceLength() : it.count };
      return;
    }
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
    const heard = token.slice(Math.max(...[..."([{"].map((c) => token.lastIndexOf(c))) + 1)
      .replace(/^["'([{«“‘„¿¡]+/, "");  // an opening mark stays put, and so does a call it ends: "print(value"
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
      settleHold();  // a hold never crosses a segment end
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
