// ═══ FLOW — the molten dictation engine, scaled to one tab ════════════════
// A faithful JS port of the daemon's flow pipeline (voice_keyboard/flow/):
// the same spoken grammar, the same punctuation table, the same register
// fold, the same molten/frozen mechanic. Words arrive molten, repair in
// place while the transcript firms up, and freeze once they survive the
// stability window. Frozen output is append-only — the page treats it the
// way the daemon treats keystrokes already typed into the field. Pure
// logic, no DOM; the demo wires it to the windows the way the daemon wires
// it to uinput.

// ── the spoken grammar (grammar.py, verbatim defaults) ────────────────────
const PUNCT_STRIP = /[.,!?;:]+$/;
const core = (t) => t.toLowerCase().replace(PUNCT_STRIP, "");

const COMMANDS = {
  "scratch that": "scratch", "delete that": "scratch",
  "new line": "\n", "new paragraph": "\n\n",
};
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

// ── spoken cardinals -> digits (numbers.py, the working subset) ──────────
const UNITS = { zero:0, one:1, two:2, three:3, four:4, five:5, six:6, seven:7,
  eight:8, nine:9, ten:10, eleven:11, twelve:12, thirteen:13, fourteen:14,
  fifteen:15, sixteen:16, seventeen:17, eighteen:18, nineteen:19 };
const TENS = { twenty:20, thirty:30, forty:40, fifty:50, sixty:60, seventy:70,
  eighty:80, ninety:90 };
const NUMBER_WORDS = new Set([...Object.keys(UNITS), ...Object.keys(TENS),
  "hundred", "and", "point"]);

function parseCardinal(words) {
  if (!words.length) return null;
  let total = 0, current = 0, seen = false;
  for (const w of words) {
    if (w === "and") { if (!seen) return null; continue; }
    if (w in UNITS) {
      const v = UNITS[w];
      if (v === 0) { if (seen || words.length > 1) return null; }
      else if (v >= 10) { if (current % 100 !== 0) return null; current += v; }
      else { if (current % 10 !== 0 || (current % 100 >= 10 && current % 100 < 20)) return null; current += v; }
      seen = true;
    } else if (w in TENS) {
      if (current % 100 !== 0) return null;
      current += TENS[w]; seen = true;
    } else if (w === "hundred") {
      if (!seen || current === 0 || current > 9) return null;
      current *= 100;
    } else return null;
  }
  return seen ? total + current : null;
}
function convertNumbers(words, minValue) {
  // digit run: three+ single digits ("one two seven" -> "127")
  if (words.length >= 3 && words.every((w) => w in UNITS && UNITS[w] <= 9))
    return [words.map((w) => UNITS[w]).join("")];
  // decimal: "<cardinal> point <digits...>"
  const pi = words.indexOf("point");
  if (pi > 0 && pi < words.length - 1) {
    const whole = parseCardinal(words.slice(0, pi));
    const frac = words.slice(pi + 1);
    if (whole !== null && frac.every((w) => w in UNITS && UNITS[w] <= 9))
      return [whole + "." + frac.map((w) => UNITS[w]).join("")];
    return null;
  }
  const n = parseCardinal(words);
  if (n === null) return null;
  if (words.length === 1 && n < minValue) return null;  // "no one knows" survives
  return [String(n)];
}

// ── registers (registers.py) ──────────────────────────────────────────────
export const REGISTERS = {
  prose:    { name: "prose",    smartCaps: true,  grammar: true,  numbersOn: false, numbersMin: 10 },
  terminal: { name: "terminal", smartCaps: false, grammar: true,  numbersOn: true,  numbersMin: 0 },
  verbatim: { name: "verbatim", smartCaps: false, grammar: false, numbersOn: false, numbersMin: 10 },
};

// ── parse: raw tokens -> items, with the frozen fence (grammar.py) ────────
// items: {kind: word|punct|break|scratch|instruction, text, mode,
//         sentenceEnd, s, e}  — s/e are [start, end) raw-token indices
export function parse(tokens, { flush = false, frozen = 0, register, cfg }) {
  const reg = register || REGISTERS.prose;
  if (!reg.grammar) {
    return { items: tokens.map((t, i) => ({ kind: "word", text: t, s: i, e: i + 1 })),
             pendingFrom: null };
  }
  const wake = ((cfg && cfg.wakeWord) || "furion").toLowerCase();
  const cores = tokens.map(core);
  const items = [];
  let pendingFrom = null, i = 0;
  while (i < tokens.length) {
    const fence = i < frozen ? frozen - i : tokens.length;
    // wake word: everything after it is an instruction, never typed —
    // it resolves only at finalize; until then it holds the tail back
    if (i >= frozen && wake && cores[i] === wake) {
      if (!flush) { pendingFrom = i; break; }
      items.push({ kind: "instruction", text: tokens.slice(i + 1).join(" "),
                   s: i, e: tokens.length });
      break;
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
        // emit the next token verbatim, bypassing the grammar
        if (i + used >= tokens.length) {
          if (i >= frozen && !flush) { pendingFrom = i; break; }
          items.push({ kind: "word", text: tokens[i], s: i, e: i + 1 }); i += 1; continue;
        }
        items.push({ kind: "word", text: tokens[i + used], s: i, e: i + used + 1 });
        i += used + 1; continue;
      } else if (payload === "scratch") {
        items.push({ kind: "scratch", s: i, e: i + used });
      } else {  // "\n" | "\n\n"
        items.push({ kind: "break", text: payload, s: i, e: i + used });
      }
      i += used; continue;
    }
    items.push({ kind: "word", text: tokens[i], s: i, e: i + 1 });
    i += 1;
  }
  // fold spoken-number runs (held back while still touching the molten tail)
  const numbersOn = (cfg && cfg.numbers === "always") ||
    ((!cfg || cfg.numbers === "auto") && reg.numbersOn);
  if (numbersOn) {
    const out = []; let run = [];
    const close = (atTail) => {
      if (!run.length) return;
      if (atTail && !flush) {
        if (pendingFrom === null) pendingFrom = run[0].s;
        run = []; return;
      }
      const conv = convertNumbers(run.map((it) => core(it.text)), reg.numbersMin);
      if (conv) for (const t of conv)
        out.push({ kind: "word", text: t, s: run[0].s, e: run[run.length - 1].e });
      else out.push(...run);
      run = [];
    };
    for (const it of items) {
      if (it.kind === "word" && it.s >= frozen && NUMBER_WORDS.has(core(it.text))) run.push(it);
      else { close(false); out.push(it); }
    }
    close(true);
    return { items: out, pendingFrom };
  }
  return { items, pendingFrom };
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
  return { atStart: true, glueNext: false, capNext: (register || REGISTERS.prose).smartCaps };
}
export function render(items, register, state) {
  const reg = register || REGISTERS.prose;
  const st = state ? { ...state } : initialState(reg);
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
    // scratch/instruction render nothing; the engine acts on them
  }
  return { text: out.join(""), st };
}

// ═══ THE MOLTEN LINE — one utterance, from first sound to freeze ═════════
// The daemon's engine.py, reduced to what a window can show. Tokens carry
// a stability clock; once a rendered item's tokens all survive the window
// (stabilityMs, 2 updates) it is folded into the committed text and its
// tokens are CONSUMED — never parsed again, exactly like keystrokes the
// daemon has already typed. Commitment lands on item boundaries only, so
// a phrase, number run, or vocabulary match can never be split by the
// fence. Provider revisions repair the molten tail; revisions to consumed
// tokens are ignored — frozen text keeps its form. Only the user's own
// "scratch that" may take committed text back.
export function moltenLine({ register, cfg }) {
  const stab = () => Math.max(200, (cfg && cfg.stabilityMs) || 1500);
  let all = [];          // the full transcript view, tokenized
  let track = [];        // per-global-index stability clocks
  let committed = 0;     // tokens consumed into the fold
  let fold = { text: "", st: initialState(register) };
  let lastRendered = { frozen: "", molten: "", instr: "", repair: false };

  function update(rawText, now) {
    const next = rawText.trim() ? rawText.trim().split(/\s+/) : [];
    let repair = false;
    for (let i = 0; i < next.length; i++) {
      if (track[i] && track[i].text !== next[i]) {
        if (i >= committed && i < all.length) repair = true;  // a revision landed
        track[i] = { text: next[i], since: now, updates: 1 };
      } else if (track[i]) track[i].updates++;
      else track[i] = { text: next[i], since: now, updates: 1 };
    }
    track.length = next.length;
    if (next.length < all.length && all.length > committed) repair = true;
    all = next;
    if (committed > all.length) committed = all.length;  // interim collapse
    return compose(false, now, repair);
  }

  function compose(flush, now, repair) {
    let retracts = 0;
    let parsed;
    // resolve scratches leftmost-first, consuming their tokens so each
    // applies exactly once across updates
    for (;;) {
      const tail = all.slice(committed);
      parsed = parse(tail, { flush, frozen: 0, register, cfg });
      const visTo = parsed.pendingFrom === null ? parsed.items.length :
        parsed.items.findIndex((it) => it.s >= parsed.pendingFrom);
      const vis = visTo === -1 ? parsed.items : parsed.items.slice(0, visTo);
      const k = vis.findIndex((it) => it.kind === "scratch");
      if (k === -1) { parsed = { ...parsed, vis }; break; }
      const spoken = vis.slice(0, k).some(
        (it) => it.kind === "word" || it.kind === "punct" || it.kind === "break");
      if (!spoken) {
        if (fold.text) fold = { text: "", st: initialState(register) };  // backspace the typed segment
        else retracts++;                        // nothing here: eat the previous line
      }
      committed += vis[k].e;                    // consume dropped words + the phrase
    }
    const vis = parsed.vis;
    const live = vis.filter((it) => it.kind !== "instruction");
    const instrIt = vis.find((it) => it.kind === "instruction");
    // stability: how many tail tokens have survived the window
    const tailLen = all.length - committed;
    const limit = flush ? tailLen :
      (parsed.pendingFrom === null ? tailLen : parsed.pendingFrom);
    let stable = 0;
    while (stable < limit) {
      const t = track[committed + stable];
      if (!flush && !(t && now - t.since >= stab() && t.updates >= 2)) break;
      stable++;
    }
    // commit whole items only — the fence can never split a phrase
    let cut = 0, consumed = 0;
    for (const it of live) {
      if (it.e <= stable) { cut++; consumed = it.e; } else break;
    }
    if (flush) { cut = live.length; consumed = tailLen; }
    if (cut > 0) {
      const r = render(live.slice(0, cut), register, fold.st);
      fold = { text: fold.text + r.text, st: r.st };
      committed += consumed;
    }
    const moltenR = render(live.slice(cut), register, { ...fold.st });
    // the wake word holds the tail back while an instruction is forming —
    // surface it so the caption can show instruction-listening state
    const wake = ((cfg && cfg.wakeWord) || "furion").toLowerCase();
    let instr = instrIt ? instrIt.text : "";
    if (!instr && !flush) {
      const tailNow = all.slice(committed);
      const p2 = parse(tailNow, { flush: false, frozen: 0, register, cfg });
      if (p2.pendingFrom !== null && core(tailNow[p2.pendingFrom] || "") === wake)
        instr = tailNow.slice(p2.pendingFrom).join(" ");
    }
    lastRendered = {
      frozen: fold.text, molten: moltenR.text, instr,
      isFinalInstr: !!instrIt, repair: !!repair, retracts,
    };
    return lastRendered;
  }

  return {
    update,
    flush: () => compose(true, 0, false),
    peek: () => lastRendered,
    reset() {
      all = []; track = []; committed = 0;
      fold = { text: "", st: initialState(register) };
      lastRendered = { frozen: "", molten: "", instr: "", repair: false };
    },
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
