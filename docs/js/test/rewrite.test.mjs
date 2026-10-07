// The page's stand-in for the app's language model: it does the three
// rewrites it knows and nothing else — an instruction it doesn't know
// leaves the visitor's words exactly as they were.
import { test } from "node:test";
import assert from "node:assert/strict";
import { pageRewrite, pageRewriteKind } from "../flow.js";

test("the three rewrites it knows", () => {
  assert.equal(pageRewrite("i think it works now", "make that formal"), "I believe it functions correctly.");
  assert.equal(pageRewrite("i think it works now", "make it more professional."), "I believe it functions correctly.");
  assert.equal(pageRewrite("i think it works now", "make that polite"), "I believe it functions correctly.");
  assert.equal(pageRewrite("ship it on friday", "make that upper case"), "SHIP IT ON FRIDAY");
  assert.equal(pageRewrite("ship it on friday", "uppercase that"), "SHIP IT ON FRIDAY");
  assert.equal(pageRewrite("ship it on friday", "make that all caps"), "SHIP IT ON FRIDAY");
  assert.equal(pageRewrite("the quick brown fox", "make that title case"), "The Quick Brown Fox");
  assert.equal(pageRewrite("the quick brown fox", "turn that into a title"), "The Quick Brown Fox");
});

test("anything else changes nothing", () => {
  for (const instr of ["translate that to Spanish", "make that shorter", "fix the spelling", "make it less formal",
    "make that informal", "make it casual", "not so formal please", "summarize that", "lowercase that", ""]) {
    assert.equal(pageRewriteKind(instr), null, instr);
    assert.equal(pageRewrite("i think it works now", instr), null, instr);
  }
  assert.equal(pageRewriteKind("write that in formal English"), "formal");
});
