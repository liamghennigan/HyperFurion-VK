// ═══ NAV — hands-free navigation: spoken caret commands and their chords ══
// A port of voice_keyboard/flow/nav.py.
//
//     go left three words        select previous word       delete next word
//     go to end of line          select all / select line   delete line
//     move up two lines          press tab / press escape twice
//
// parseNav reads one command at a token index (pure, deterministic — the
// grammar's prefix property holds). chordsFor turns a command into the key
// chords for the kind of app: editors and prose fields share one map;
// terminals get readline's keys (this page is the Linux daemon: alt+b,
// ctrl+a, ctrl+w). Selection is refused in terminals — there is no text
// selection to extend. Enter is never a navigation key: no command
// produces it, and nothing in these tables may name it.

export const MAX_COUNT = 20;
export const PENDING = "pending";

export const VERBS = { go: "move", move: "move", select: "select", delete: "delete", press: "press" };

const UNITS = { zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
  nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15,
  sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19 };
const TENS = { twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 };

// spoken direction -> [direction, needs an explicit unit]
const DIRECTIONS = {
  left: ["left", false], right: ["right", false],
  back: ["left", false], backward: ["left", false], backwards: ["left", false],
  forward: ["right", false], forwards: ["right", false],
  up: ["up", false], down: ["down", false],
  previous: ["left", true], last: ["left", true], next: ["right", true],
};
const UNIT_WORDS = {
  char: "char", chars: "char", character: "char", characters: "char", letter: "char", letters: "char",
  word: "word", words: "word", line: "line", lines: "line",
};
const EDGES = { start: "start", beginning: "start", top: "start", end: "end", bottom: "end" };
const EDGE_UNITS = { line: "line", document: "doc", file: "doc", page: "doc" };

// spoken key (one or two words) -> key name
export const PRESS_KEYS = new Map([
  ["tab", "tab"], ["escape", "escape"], ["up", "up"], ["down", "down"], ["left", "left"],
  ["right", "right"], ["home", "home"], ["end", "end"], ["page up", "pageup"],
  ["page down", "pagedown"], ["backspace", "backspace"], ["back space", "backspace"],
  ["delete", "delete"], ["space", "space"],
]);
const TIMES = new Set(["times", "time", "x"]);
const REPEAT_WORDS = { once: 1, twice: 2, thrice: 3 };

// chords nothing may ever press: each one submits a line somewhere
const FORBIDDEN_KEYS = new Set(["enter", "return", "kpenter"]);
const FORBIDDEN_CHORDS = [["ctrl", "j"], ["ctrl", "m"], ["ctrl", "o"]];

class NeedMore extends Error {}

function countOf(core) {
  if (core === "a" || core === "an") return 1;  // "go back a word"
  let value;
  if (/^[0-9]+$/.test(core)) value = parseInt(core, 10);
  else if (core in UNITS) value = UNITS[core];
  else if (core in TENS) value = TENS[core];
  else return null;
  return value >= 1 && value <= MAX_COUNT ? value : null;
}
const numberish = (w) => /^[0-9]+$/.test(w) || w in UNITS || w in TENS;

// The navigation command starting at `index`, read from `cores`
// (lowercased, punctuation-stripped tokens; the caller slices them to the
// fence it must not cross). Returns [action, count, end] — action like
// "move:word:left", "select:all", "press:tab" — or PENDING when the tokens
// run out where the command could still continue and the tail is not
// `decided`, or null when this is not a command (the verb is just a word).
export function parseNav(cores, index, { decided }) {
  const verb = index < cores.length ? VERBS[cores[index]] : undefined;
  if (!verb) return null;
  const at = (p) => { if (p >= cores.length) throw new NeedMore(); return cores[p]; };
  const opt = (p) => (p < cores.length ? cores[p] : null);
  try {
    if (verb === "press") return parsePress(cores, index + 1, at, opt, decided);
    return parseMotion(verb, index + 1, at, opt, decided, cores.length);
  } catch (e) {
    if (e instanceof NeedMore) return decided ? null : PENDING;
    throw e;
  }
}

// a complete command; PENDING if it touches the tail and optional words
// (a count, a unit, "times") could still follow
function finish(action, count, end, total, decided, more) {
  if (more && end >= total && !decided) return PENDING;
  return [action, count, end];
}
const hasTwoWordKey = (first) => [...PRESS_KEYS.keys()].some((k) => k.includes(" ") && k.split(" ")[0] === first);

function parsePress(cores, cursor, at, opt, decided) {
  const first = at(cursor);
  const second = opt(cursor + 1);
  let key;
  if (second !== null && PRESS_KEYS.has(first + " " + second)) { key = PRESS_KEYS.get(first + " " + second); cursor += 2; }
  else if (second === null && !decided && hasTwoWordKey(first)) throw new NeedMore();  // "page" may become "page up"
  else if (PRESS_KEYS.has(first)) { key = PRESS_KEYS.get(first); cursor += 1; }
  else return null;
  const action = "press:" + key;
  const word = opt(cursor);
  if (word !== null && word in REPEAT_WORDS) return [action, REPEAT_WORDS[word], cursor + 1];
  const count = word !== null ? countOf(word) : null;
  if (count === null) return finish(action, 1, cursor, cores.length, decided, true);
  cursor += 1;
  if (TIMES.has(opt(cursor))) return [action, count, cursor + 1];
  return finish(action, count, cursor, cores.length, decided, true);
}

function parseMotion(verb, cursor, at, opt, decided, total) {
  let word = at(cursor);
  // select all / select (this) line / delete (the) line
  if (verb === "select" && word === "all") return ["select:all", 1, cursor + 1];
  if (verb === "select" || verb === "delete") {
    const probe = cursor + (["this", "the", "current"].includes(word) ? 1 : 0);
    if (at(probe) === "line") return [verb + ":line:here", 1, probe + 1];
  }
  // go to (the) start/end of (the) line/document
  let probe = cursor + (word === "to" ? 1 : 0);
  if (at(probe) === "the") probe += 1;
  const edge = EDGES[at(probe)];
  if (edge !== undefined) {
    if (verb === "delete" || at(probe + 1) !== "of") return null;
    probe += 2;
    if (at(probe) === "the") probe += 1;
    const unit = EDGE_UNITS[at(probe)];
    if (unit === undefined) return null;
    return [verb + ":" + unit + ":" + edge, 1, probe + 1];
  }
  if (word === "to") return null;
  // go/select/delete (the) <direction> [count] [unit]
  if (word === "the") { cursor += 1; word = at(cursor); }
  const spoken = DIRECTIONS[word];
  if (spoken === undefined) return null;
  let [direction, needsUnit] = spoken;
  cursor += 1;
  let count = 1;
  word = opt(cursor);
  const counted = word !== null ? countOf(word) : null;
  if (counted === null && word !== null && numberish(word)) return null;  // a count out of range: not a command
  if (counted !== null) { count = counted; cursor += 1; word = opt(cursor); }
  let unit = word !== null ? UNIT_WORDS[word] : undefined;
  const explicit = unit !== undefined;
  if (explicit) cursor += 1;
  else if (word === null && !decided && (needsUnit || counted !== null)) throw new NeedMore();  // "select previous" wants a unit
  else if (needsUnit) return null;
  else unit = direction === "up" || direction === "down" ? "line" : "char";
  if (unit === "line" && (direction === "left" || direction === "right")) {
    if (!needsUnit) return null;  // "go left line" is not a thing
    direction = direction === "left" ? "up" : "down";
  }
  if ((direction === "up" || direction === "down") && unit !== "line") return null;
  if (verb === "delete" && unit === "line") return null;
  return finish(verb + ":" + unit + ":" + direction, count, cursor, total, decided, !explicit);
}

// ── keymaps ───────────────────────────────────────────────────────────────
const EDITOR_MOVES = {
  "char:left": ["left"], "char:right": ["right"],
  "word:left": ["ctrl", "left"], "word:right": ["ctrl", "right"],
  "line:up": ["up"], "line:down": ["down"],
  "line:start": ["home"], "line:end": ["end"],
  "doc:start": ["ctrl", "home"], "doc:end": ["ctrl", "end"],
};
export const EDITOR = {
  ...Object.fromEntries(Object.entries(EDITOR_MOVES).map(([k, c]) => ["move:" + k, [c]])),
  ...Object.fromEntries(Object.entries(EDITOR_MOVES).map(([k, c]) => ["select:" + k, [["shift", ...c]]])),
  "select:all": [["ctrl", "a"]],
  "select:line:here": [["home"], ["shift", "end"]],
  "delete:char:left": [["backspace"]], "delete:char:right": [["delete"]],
  "delete:word:left": [["ctrl", "backspace"]], "delete:word:right": [["ctrl", "delete"]],
  "delete:line:here": [["home"], ["shift", "end"], ["backspace"]],
};
const NO_SELECTION = Object.fromEntries(Object.keys(EDITOR).filter((k) => k.startsWith("select:")).map((k) => [k, null]));
// readline / zsh emacs keys: bash, zsh, python, psql, …
export const LINUX_TERMINAL = {
  ...Object.fromEntries(Object.entries(EDITOR).filter(([k]) => k.startsWith("move:"))),
  ...NO_SELECTION,
  "move:word:left": [["alt", "b"]], "move:word:right": [["alt", "f"]],
  "move:line:start": [["ctrl", "a"]], "move:line:end": [["ctrl", "e"]],
  "move:doc:start": null, "move:doc:end": null,
  // up/down in a shell is history, not a line: "press up" says it plainly
  "move:line:up": null, "move:line:down": null,
  "delete:char:left": [["backspace"]], "delete:char:right": [["delete"]],
  "delete:word:left": [["ctrl", "w"]], "delete:word:right": [["alt", "d"]],
  "delete:line:here": [["ctrl", "e"], ["ctrl", "u"]],
};

// commands that leave a selection or a gap: the next dictated word
// replaces or fills it, so it starts glued (no leading space); moves to a
// line/document start glue too. Keys that likely land in another field:
// dictation after them starts fresh (no leading space, a capital in prose).
export const GLUED = ["select:", "delete:"];
export const FRESH_FIELD = new Set(["press:tab", "press:escape", "press:pageup", "press:pagedown"]);

function forbidden(chord) {
  const names = new Set(chord.map((n) => n.trim().toLowerCase()));
  if ([...names].some((n) => FORBIDDEN_KEYS.has(n))) return true;
  return FORBIDDEN_CHORDS.some((bad) => bad.every((n) => names.has(n)));
}
export const keymap = ({ terminal }) => ({ ...(terminal ? LINUX_TERMINAL : EDITOR) });

// The full chord sequence for a command (repeated `count` times), or null
// when this kind of app has no binding for it. Presses are capped and
// Enter is never in the result, whatever the table says.
export function chordsFor(action, count, table) {
  let sequence;
  if (action.startsWith("press:")) sequence = [[action.slice(6)]];
  else sequence = table[action];
  if (!sequence) return null;
  if (sequence.some(forbidden)) return null;
  const repeat = action === "select:all" || action.endsWith(":here") ? 1 : count;
  const out = [];
  for (let i = 0; i < Math.max(1, Math.min(MAX_COUNT, repeat)); i++) for (const c of sequence) out.push([...c]);
  return out;
}

// what the overlay shows as a command fires: "select word left ×2"
export function label(action, count) {
  let text = action.replace(/:/g, " ").replace(/^move /, "");
  if (count > 1) text += " ×" + count;
  return text;
}
