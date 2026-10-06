// What a real recognizer writes — capitals, its own stops, hyphenated
// numbers, quotes, "Vk" for the wake word — through the page's engine.
// The engine's results are held to the daemon's by parity.test.mjs
// (tests/flow_corpus.json); these cover the page-only lanes around it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { moltenLine, REGISTERS, wakeAt, splitCompound } from "../flow.js";
import { intentRequest, pageCommand, VERBS } from "../intent.js";

const cfg = { wakeWord: "vk", numbers: "auto", spelling: true, nav: true, stabilityMs: 1500, stabilityUpdates: 2,
              adaptive: true, pauseReview: "rules" };

test("the wake word as recognizers spell it, never a name or a sound-alike", () => {
  const at = (text) => wakeAt(text.split(" ").map((t) => t.toLowerCase().replace(/^[.,!?;:]+|[.,!?;:]+$/g, "")), 0, "vk");
  for (const heard of ["VK,", "Vk", "vk.", "V.K.,", "V-K", "Veekay,"]) assert.equal(at(heard + " run"), 1, heard);
  assert.equal(at("V K, run"), 2);
  for (const heard of ["Vicky", "Vikki", "Veek", "Bk", "Dk", "Decay", "The k", "V"]) assert.equal(at(heard + " run"), 0, heard);
});

test("hyphenated numbers split into the words a speaker said", () => {
  assert.deepEqual(splitCompound("Twenty-five."), ["Twenty", "five."]);
  assert.deepEqual(splitCompound("four-oh-two"), ["four", "oh", "two"]);
  assert.equal(splitCompound("twenty-first"), null);
  assert.equal(splitCompound("one-on-one"), null);
});

test("“VK, run find every todo” as the page's recognizer writes it drafts the TODO search", () => {
  for (const heard of ["Vk run find every to do.", "VK, run find every todo.", "V K, run find every to-do in this repo."]) {
    const line = moltenLine({ register: REGISTERS.shell, cfg });
    line.update(heard, 1000, { final: true });
    const instruction = line.takeInstruction();
    const request = intentRequest(instruction, VERBS);
    assert.ok(request, heard);
    assert.deepEqual(pageCommand(request), { command: "grep -rn TODO .", known: true }, heard);
    assert.equal(line.result().text, "", heard);  // nothing typed but the drafted command
  }
});

test("a name heard where the wake word should be is typed, never taken as an instruction", () => {
  const line = moltenLine({ register: REGISTERS.prose, cfg });
  line.update("Bk run find every to do.", 1000, { final: true });
  assert.equal(line.takeInstruction(), "");
  assert.equal(line.finalize("Bk run find every to do.", 2000).text, "Bk run find every to do.");
});

test("the chips' examples as a recognizer writes them", () => {
  const say = (register, segments, extra = {}) => {
    const line = moltenLine({ register: REGISTERS[register], cfg: { ...cfg, ...extra } });
    let merged = "", now = 0;
    for (const s of segments) {
      merged = (merged + " " + s).trim(); now += 1000;
      line.update(merged, now, { final: true });
      while (line.pendingAction()) line.completeAction(now, { pressed: true });
    }
    let r = line.finalize(merged, now + 1000);
    while (r.action) r = line.completeAction(now + 1000, { pressed: true });
    return r.typedBefore + r.text;
  };
  assert.equal(say("prose", ["Twenty-five percent by October sixth."]), "25% by October 6.");
  assert.equal(say("prose", ["The launch is on Monday period.", "We ship at noon period.", "Correct Monday to Friday."]),
    "The launch is on Friday. We ship at noon.");
  assert.equal(say("prose", ["I'll send it to Shivon.", "Spell that S I O B H A N."]), "I'll send it to Siobhan.");
  assert.equal(say("prose", ["i'll send it to Shivon", "spell that s i o b h a n"]), "I'll send it to Siobhan");
  assert.equal(say("prose", ["We launched today.", "Emoji rocket."]), "We launched today. 🚀");
  assert.equal(say("prose", ["The fix lands on Thursday.", "Scratch that.", "The fix lands on Friday period."]), "The fix lands on Friday.");
  assert.equal(say("terminal", ["Twenty-three failed tests, rerun the flaky ones."]), "23 failed tests, rerun the flaky ones");
  assert.equal(say("python", ["For i in range ten colon."]), "for i in range(10):");
  assert.equal(say("shell", ["List files pipe grep dash i error."]), "list files | grep -i error");
  assert.equal(say("javascript", ["Const total equals count plus one."]), "const total = count + 1");
});
