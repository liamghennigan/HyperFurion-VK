// ═══ INTENT — "VK, run …": type a command line, never run it ══════════════
// The daemon's [intent] channel sends the spoken request to your [llm]
// with a few-shot prompt (voice_keyboard/llm.py INTENT_SYSTEM_PROMPT) and
// types the ONE command line it answers with — Enter refused inside the
// keystroke injector. This page has no model, so it answers the prompt's
// own examples and a handful of everyday requests from a table, and says
// so. Anything it does not know is typed as a comment line: a draft that
// could never run, which is the point.
const TABLE = [
  [/^list files sorted by size$/, () => "ls -lS"],
  [/^list (the )?files( here)?$/, () => "ls -la"],
  [/^find (every|all) (.+?) in (this|the) (repo|project|directory|folder)$/, (m) => `grep -rn ${term(m[2])} .`],
  [/^find (every|all) (.+)$/, (m) => `grep -rn ${term(m[2])} .`],
  [/^show running docker containers$/, () => "docker ps"],
  [/^undo my last commit but keep the changes$/, () => "git reset --soft HEAD~1"],
  [/^make a new branch called (\S+) and switch to it$/, (m) => `git checkout -b ${m[1]}`],
  [/^(show )?(the )?git status$/, () => "git status"],
  [/^show (the )?last (\w+) commits$/, (m) => `git log --oneline -${count(m[2])}`],
  [/^(show )?(the )?disk usage$|^how much disk (space )?(is left|do i have)$/, () => "df -h"],
  [/^what'?s listening on port (\w+)$|^what is listening on port (\w+)$/, (m) => `lsof -i :${count(m[1] || m[2])}`],
  [/^count (the )?lines in (\S+)$/, (m) => `wc -l ${m[2]}`],
  [/^(show )?(the )?running processes$/, () => "ps aux"],
  [/^(run )?(the )?tests$/, () => "pytest -q"],
];
const WORDS = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  twenty: 20, fifty: 50, hundred: 100, eighty: 80 };
const count = (w) => (/^\d+$/.test(w) ? w : String(WORDS[w] || w));
// "todo" and "fixme" are what people grep for in capitals — however the
// recognizer wrote them: "to do", "to-dos", "fix me"
const term = (t) => {
  const w = t.trim();
  const marker = w.toLowerCase().replace(/[\s-]+/g, "").replace(/s$/, "");
  return /^(todo|fixme|hack|xxx)$/.test(marker) ? marker.toUpperCase() : JSON.stringify(w);
};

export const VERBS = ["run", "command", "execute"];
// the daemon: an instruction whose first word is an [intent] verb is a
// request for a command; the verb (and a following "the") is stripped
export function intentRequest(instruction, verbs = VERBS) {
  const parts = instruction.trim().split(/\s+/);
  if (!parts.length || !verbs.includes(parts[0].toLowerCase().replace(/[,.:;!?]+$/, ""))) return null;
  let rest = parts.slice(1).join(" ").trim();
  if (!rest) return null;
  return rest;
}
// one command line for a request, or a comment line when the table has no
// answer; `known` says which
export function pageCommand(request) {
  const r = request.trim().toLowerCase().replace(/[.!?,]+$/g, "");
  for (const [re, make] of TABLE) {
    const m = re.exec(r);
    if (m) return { command: make(m), known: true };
  }
  return { command: "# " + request.trim() + "  (the page has no model; the daemon asks your [llm])", known: false };
}
