// The page's engine against the daemon's: tests/flow_corpus.json is
// written by scripts/flow_corpus.py from the Python FlowEngine; this
// replays every case through flow.js and expects the same screens, the
// same actions, the same final result. Run: node --test docs/js/test
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { moltenLine, REGISTERS } from "../flow.js";

const corpus = JSON.parse(readFileSync(fileURLToPath(new URL("../../../tests/flow_corpus.json", import.meta.url)), "utf8"));

function runCase({ register, segments, options }) {
  const cfg = { wakeWord: "vk", numbers: options.numbers || "auto", spelling: options.spelling !== false,
                nav: !!options.nav, stabilityMs: 1500, stabilityUpdates: 2 };
  const engine = moltenLine({ register: REGISTERS[register], cfg });
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
  assert.ok(corpus.some((c) => c.options.nav), "navigation cases");
  assert.ok(corpus.some((c) => c.events.some((e) => e[0] === "final" && e[5].length)), "spelling corrections");
});
