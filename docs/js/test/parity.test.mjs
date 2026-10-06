// The page's engine against the daemon's: tests/flow_corpus.json is
// written by scripts/flow_corpus.py from the Python FlowEngine; this
// replays every case through flow.js and expects the same screens, the
// same actions, the same final result. Run: node --test docs/js/test
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { moltenLine, REGISTERS, continuationState } from "../flow.js";

const corpus = JSON.parse(readFileSync(fileURLToPath(new URL("../../../tests/flow_corpus.json", import.meta.url)), "utf8"));

const S = 1000;  // the corpus clock is in seconds; the page's in ms
function makeEngine({ register, options }) {
  const cfg = { wakeWord: "vk", numbers: options.numbers || "auto", spelling: options.spelling !== false,
                nav: !!options.nav, stabilityMs: 1500, stabilityUpdates: 2, adaptive: true,
                pauseReview: options.pause_review || "off", ...(options.fillers ? { fillers: options.fillers } : {}) };
  const state = options.continues ? continuationState(options.continues, REGISTERS[register]) : null;
  return moltenLine({ register: REGISTERS[register], cfg, state });
}
function runStream(c) {
  const engine = makeEngine(c);
  const screen = () => { const v = engine.peek(); return v.frozen + v.molten; };
  const events = [];
  let last = "";
  for (const step of c.steps) {
    const now = step[0] * S;
    if (step[1] === "tick") engine.tick(now);
    else if (step[1] === "press") {
      const a = engine.pendingAction();
      events.push(["action", a ? a.action : null, a ? a.count : 0, screen()]);
      engine.completeAction(now, { pressed: true });
    } else { last = step[1]; engine.update(last, now, { final: step[2] === "final" }); }
    events.push(["screen", screen()]);
  }
  const r = engine.finalize(last, (c.steps.at(-1)[0] + 1) * S);
  events.push(["final", r.text, r.typedBefore, r.instruction, r.scratches, r.corrections]);
  return events;
}
function runCase(c) {
  if (c.steps) return runStream(c);
  const { segments } = c;
  const engine = makeEngine(c);
  const screen = () => { const v = engine.peek(); return v.frozen + v.molten; };
  let merged = "", now = 0;
  const events = [];
  for (const segment of segments) {
    merged = (merged + " " + segment).trim();
    now += 1;
    engine.update(merged, now, { final: true });
    while (engine.pendingAction()) {
      const a = engine.pendingAction();
      events.push(["action", a.action, a.count, screen()]);
      engine.completeAction(now, { pressed: true });
    }
    events.push(["screen", screen()]);
  }
  let r = engine.finalize(merged, now + 1);
  while (r.action) {
    events.push(["action", r.action.action, r.action.count, r.text]);
    r = engine.completeAction(now + 1, { pressed: true });
  }
  events.push(["final", r.text, r.typedBefore, r.instruction, r.scratches, r.corrections]);
  return events;
}

for (const c of corpus) {
  test("parity · " + c.name, () => {
    assert.deepEqual(runCase(c), c.events);
  });
}
test("the corpus covers the page's lanes", () => {
  assert.ok(corpus.length >= 40);
  assert.ok(corpus.some((c) => c.steps), "streaming cases");
  assert.ok(corpus.some((c) => c.options.continues), "rejoin cases");
  assert.ok(corpus.some((c) => c.options.pause_review), "pause cases");
  assert.ok(corpus.some((c) => c.options.nav), "navigation cases");
  assert.ok(corpus.some((c) => c.events.some((e) => e[0] === "final" && e[5].length)), "spelling corrections");
});
