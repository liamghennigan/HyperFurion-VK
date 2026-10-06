import { test } from "node:test";
import assert from "node:assert/strict";
import { createBuffer } from "../buffer.js";

const type = (b, s) => { for (const ch of s) b.insert(ch); };

test("an editor: words, selection, typing over it", () => {
  const b = createBuffer();
  type(b, "Hello world");
  b.press(["shift", "ctrl", "left"]);
  assert.deepEqual([b.selected(), b.caret, b.anchor], ["world", 6, 11]);
  type(b, "planet");
  assert.equal(b.text, "Hello planet");
  b.press(["home"]); type(b, "Update: ");
  assert.equal(b.text, "Update: Hello planet");
  b.press(["ctrl", "end"]); b.press(["ctrl", "backspace"]);
  assert.equal(b.text, "Update: Hello ");
});
test("an editor: lines", () => {
  const b = createBuffer();
  type(b, "line one\nline two");
  b.press(["up"]); assert.equal(b.caret, 8);
  b.press(["home"]); b.press(["shift", "end"]); assert.equal(b.selected(), "line one");
  b.press(["backspace"]); assert.equal(b.text, "\nline two");
  b.press(["ctrl", "a"]); assert.equal(b.selected(), "\nline two");
  b.press(["escape"]); assert.equal(b.selection(), null);
});
test("a terminal: readline keys, no selection", () => {
  const b = createBuffer({ terminal: true });
  type(b, "pytest -x tests/test_flow.py");
  b.press(["ctrl", "w"]); assert.equal(b.text, "pytest -x ");
  type(b, "tests/test_nav.py");
  b.press(["alt", "b"]); assert.equal(b.caret, "pytest -x tests/test_nav.".length);
  b.press(["ctrl", "a"]); assert.equal(b.caret, 0);
  b.press(["ctrl", "e"]); assert.equal(b.caret, b.text.length);
  assert.equal(b.press(["shift", "ctrl", "left"]), true);
  assert.equal(b.selection(), null);
  b.press(["ctrl", "e"]); b.press(["ctrl", "u"]); assert.equal(b.text, "");
  assert.equal(b.press(["tab"]), false);
});
test("a Mac editor: option words, command ends", () => {
  const b = createBuffer();
  type(b, "one two three");
  b.press(["shift", "alt", "left"]); assert.equal(b.selected(), "three");
  b.press(["cmd", "left"]); assert.equal(b.caret, 0);
  b.press(["cmd", "right"]); assert.equal(b.caret, b.text.length);
  b.press(["alt", "backspace"]); assert.equal(b.text, "one two ");
  b.press(["cmd", "a"]); assert.equal(b.selected(), "one two ");
});
test("a Mac terminal: Esc b is Meta-b", () => {
  const b = createBuffer({ terminal: true });
  type(b, "git commit -m");
  b.press(["escape"]); b.press(["b"]); assert.equal(b.caret, "git commit -".length);
  b.press(["escape"]); b.press(["d"]); assert.equal(b.text, "git commit -");
  type(b, "x"); assert.equal(b.text, "git commit -x");
});
test("Enter is never a key the buffer takes", () => {
  const b = createBuffer();
  type(b, "draft");
  assert.equal(b.press(["enter"]), false);
  assert.equal(b.text, "draft");
});
test("a long document is trimmed from the top, caret kept", () => {
  const b = createBuffer({ max: 40 });
  type(b, "aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii jjjj kkkk");
  assert.ok(b.text.length <= 40);
  assert.equal(b.caret, b.text.length);
});

test("undo and redo step back a run of typing at a time, like an editor", () => {
  const b = createBuffer();
  for (const ch of "hello world") b.insert(ch);
  b.press(["ctrl", "backspace"]);
  assert.equal(b.text, "hello ");
  assert.ok(b.press(["ctrl", "z"]));
  assert.equal(b.text, "hello world");
  assert.ok(b.press(["ctrl", "z"]));
  assert.equal(b.text, "");
  assert.ok(b.press(["ctrl", "shift", "z"]));
  assert.equal(b.text, "hello world");
  assert.ok(b.press(["ctrl", "y"]));
  assert.equal(b.text, "hello ");
  assert.equal(b.press(["ctrl", "y"]), false);  // nothing left to redo
  const t = createBuffer({ terminal: true });
  for (const ch of "ls") t.insert(ch);
  assert.equal(t.press(["ctrl", "z"]), false);  // a terminal's ctrl+z is not ours to show
});
