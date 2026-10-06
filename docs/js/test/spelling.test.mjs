import { test } from "node:test";
import assert from "node:assert/strict";
import { lettersAt, couldBeCapital } from "../spelling.js";
import { parse, REGISTERS } from "../flow.js";

test("letters in every spoken shape", () => {
  const cases = [
    [["N"], ["n", 1]], [["x."], ["x", 1]], [["november"], ["n", 1]], [["X-ray"], ["x", 1]], [["eight"], ["8", 1]],
    [["capital", "k"], ["K", 2]], [["Capital", "kilo"], ["K", 2]], [["N-G-I-N-X"], ["nginx", 1]],
    [["hello"], ["", 0]], [["capital"], ["", 0]], [["capital", "eight"], ["", 0]],
  ];
  for (const [tokens, want] of cases) assert.deepEqual(lettersAt(tokens, 0), want, tokens.join(" "));
  assert.ok(couldBeCapital("Capital,"));
});
test("the grammar strings them together", () => {
  const cfg = { spelling: true };
  const r = parse("hello wrold spell that w o r l d okay".split(" "), { cfg, register: REGISTERS.prose });
  const respell = r.items.filter((i) => i.kind === "respell");
  assert.deepEqual([respell[0].text, respell[0].mode, respell[0].s, respell[0].e], ["world", "replace", 2, 9]);
  assert.equal(r.items.at(-1).text, "okay");
  assert.equal(parse("hello spell that n g".split(" "), { cfg, register: REGISTERS.prose }).pendingFrom, 1);
  assert.equal(parse("spell that n capital".split(" "), { cfg, register: REGISTERS.prose }).pendingFrom, 0);
  assert.ok(parse("cast a spell on them".split(" "), { flush: true, cfg, register: REGISTERS.prose }).items.every((i) => i.kind === "word"));
  assert.ok(parse("spell that n g".split(" "), { flush: true, cfg: { spelling: false }, register: REGISTERS.prose }).items.every((i) => i.kind === "word"));
});
