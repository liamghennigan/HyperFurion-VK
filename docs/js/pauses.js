// ═══ PAUSES — a recognizer's sentence end at a pause is a guess ═══════════
// A port of voice_keyboard/flow/pauses.py. Streaming recognizers cut the
// audio wherever the speaker pauses and punctuate each chunk as if it were
// a whole sentence: thinking out loud — "so I was thinking about the
// project … and how we could make it simpler" — comes back as "So I was
// thinking about the project. And how we could make it simpler." The
// engine keeps the period that ends a chunk (and the capital that starts
// the next one) revisable until the words after the pause show whether
// the sentence goes on. These rules settle the clear cases; the daemon can
// also ask its [llm] about the rest — this page keeps the recognizer's
// punctuation there, as the daemon does without a model.
// Pure logic: the engine owns the state.

export const ALLOWED_PUNCT = ["", ",", ".", "?", "!", ";", ":"];
const SENTENCE_END = [".", "?", "!"];
const STRIP = /^[.,!?;:"'()\[\]{}“”‘’…*`]+|[.,!?;:"'()\[\]{}“”‘’…*`]+$/gu;

// a plain word with exactly one trailing period: "project." but not
// "U.S.", "3.5.", "..." or "etc." — those periods aren't the pause's
const WORD_PERIOD = /^[\p{L}\p{N}][\p{L}\p{N}'’-]*\.$/u;
const ABBREVIATIONS = new Set(["mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "etc", "inc", "ltd",
  "prof", "approx", "dept", "est", "fig", "vol", "co", "corp"]);
const FIRST_PERSON = new Set(["i", "i'm", "i'll", "i've", "i'd", "i’m", "i’ll", "i’ve", "i’d"]);
// the next word continues the sentence: no mark before it …
const JOIN_NEXT = new Set(["and", "or", "nor", "because", "than", "whereas"]);
// … or a comma before it
const COMMA_NEXT = new Set(["but"]);
// words a sentence (practically) never ends on: a pause after one is the
// speaker thinking, not a full stop
const OPEN_ENDINGS = new Set(["a", "an", "the", "my", "your", "our", "their", "its", "every",
  "and", "or", "but", "nor", "because", "although", "whereas", "whether",
  "if", "than", "of", "to", "for", "with", "from", "at", "about", "into",
  "onto", "between", "very", "i", "we", "they", "he", "she"]);
// everyday words that are lowercase mid-sentence — used by the rules to
// un-capitalize the word after a pause they join; names, days and months
// are deliberately absent
export const COMMON_LOWER = new Set(`
a about above actually after again against all almost also although always
am an and another any anyone anything are around as ask at away back bad
basically be because been before being below best better between big bit
both bring build but by call came can could did do does doing done down
during each easy either else enough even ever every everyone everything
fast feel few find first fix for found from get gets getting give go goes
going gone good got great had has have having he her here him his how
however i if in instead into is it its just keep kind know last later least
less let like little long look lot made make makes making many may maybe me
might more most much must my need needs never new next no not nothing now of
off often old on once one only or other our out over own part people
perhaps please point pretty probably put quite rather really right run said
same say see seem seems set she should show since so some someone something
sometimes soon start still stuff such sure take than that the their them
then there these they thing things think this those though through time to
today together too tomorrow try trying two under until up us use used using
very want wanted wants was way we well went were what whatever when where
whether which while who whole why will with within without work working
would yeah yes yet you your
`.split(/\s+/).filter(Boolean));

export const core = (token) => token.replace(STRIP, "").toLowerCase();

// a pause after `token` gets a second look: a plain word whose single
// trailing period the recognizer added (not an abbreviation's)
export const reviewable = (token) => WORD_PERIOD.test(token) && !ABBREVIATIONS.has(core(token));

// capitalized only as a sentence start: "And", not "I", "API" or "McKinsey"
export function lowerable(token) {
  const letters = [...token].filter((ch) => /\p{L}/u.test(ch));
  if (!letters.length || letters[0] !== letters[0].toUpperCase() || letters[0] === letters[0].toLowerCase()) return false;
  if (FIRST_PERSON.has(core(token))) return false;
  return letters.slice(1).every((ch) => ch === ch.toLowerCase());
}

// a decision: { punct, lowerNext, nextCore }
export const decision = (punct, lowerNext, nextCore) => ({ punct, lowerNext, nextCore });
export const same = (a, b) => !!a && !!b && a.punct === b.punct && a.lowerNext === b.lowerNext && a.nextCore === b.nextCore;

// the two tokens around a pause, rewritten per the decision
export function apply(left, right, d) {
  if (left.endsWith(".")) left = left.slice(0, -1) + d.punct;
  if (d.lowerNext && !SENTENCE_END.includes(d.punct) && core(right) === d.nextCore && lowerable(right))
    right = right[0].toLowerCase() + right.slice(1);
  return [left, right];
}
// the recognizer's call: a sentence ends at the pause
export const keep = (right) => decision(".", false, core(right));

// Decide a pause from the words around it: [decision, confident]. An
// unconfident call is the recognizer's own period.
export function ruleDecision(left, right, { rightKind = "word", lowerSeen = new Set() } = {}) {
  const nxt = core(right), prev = core(left);
  if (rightKind === "punct") return [decision("", false, nxt), true];  // spoken punctuation replaces the guess
  if (rightKind !== "word") return [keep(right), true];  // a command: the sentence ended where the speaker paused
  if (COMMA_NEXT.has(nxt)) return [decision(",", lowerable(right), nxt), true];
  if (JOIN_NEXT.has(nxt)) return [decision("", lowerable(right), nxt), true];
  if (OPEN_ENDINGS.has(prev)) {
    const lower = lowerable(right) && (COMMON_LOWER.has(nxt) || lowerSeen.has(nxt));
    return [decision("", lower, nxt), true];
  }
  return [keep(right), false];
}
