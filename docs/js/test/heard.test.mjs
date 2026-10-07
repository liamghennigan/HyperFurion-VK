// The in-tab model's usual mishearings of "VK" and "spell" are read as
// meant only where nothing else fits; ordinary sentences stay as said.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readAsMeant } from "../heard.js";
import { moltenLine, REGISTERS } from "../flow.js";

test("the wake word, as the small model writes it, before an instruction verb", () => {
  const cases = [
    ["The K make that formal.", "VK, make that formal."],
    ["Dk run find every to do", "VK, run find every to do"],
    ["Decay, run the tests.", "VK, run the tests."],
    ["The K. Make that formal.", "VK, Make that formal."],
    ["I think it works now. The K make that formal.", "I think it works now. VK, make that formal."],
  ];
  for (const [heard, meant] of cases) assert.equal(readAsMeant(heard), meant, heard);
});

test("spell, as the small model writes it, before that and spelled letters", () => {
  const cases = [
    ["Fill that s, I, o, b, h, a, n.", "spell that s, I, o, b, h, a, n."],
    ["Send the notes to Shiv Bell that s I o b h a n", "Send the notes to Shiv spell that s I o b h a n"],
    ["Build that N-G-I-N-X", "spell that N-G-I-N-X"],
  ];
  for (const [heard, meant] of cases) assert.equal(readAsMeant(heard), meant, heard);
});

test("ordinary sentences stay as said", () => {
  for (const said of ["The key makes sense.", "The K key is broken.", "Press the K make it work.", "Decay turns leaves brown.",
    "Fill that out.", "Fill that in please.", "I need to fill that a.s.a.p.", "Fill that a b", "Ring the bell that I hear.",
    "The fix lands on Friday."])
    assert.equal(readAsMeant(said), said, said);
});

test("read as meant, the engine takes the command", () => {
  const cfg = { stabilityMs: Infinity, stabilityUpdates: 2, numbers: "auto", wakeWord: "vk", spelling: true, nav: true,
    intent: { enabled: true, verbs: ["run", "command", "execute"] } };
  const line = moltenLine({ register: REGISTERS.prose, cfg });
  line.update("I'll send it to Shivon.", 0, { final: true });
  const r = line.finalize("I'll send it to Shivon. " + readAsMeant("Fill that s, I, o, b, h, a, n."), 1);
  assert.equal(r.text, "I'll send it to Siobhan.");
  const l2 = moltenLine({ register: REGISTERS.prose, cfg });
  l2.update("I think it works now. " + readAsMeant("The K make that formal."), 0, { final: true });
  assert.equal(l2.takeInstruction().replace(/[.]$/, ""), "make that formal");
});
