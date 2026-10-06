import { test } from "node:test";
import assert from "node:assert/strict";
import { parseNav, chordsFor, keymap, EDITOR, LINUX_TERMINAL, MAC_EDITOR, MAC_TERMINAL, WINDOWS_TERMINAL, PENDING, PRESS_KEYS, label } from "../nav.js";

const parse = (s, decided = true) =>
  parseNav(s.toLowerCase().split(/\s+/).map((t) => t.replace(/^[.,!?;:]+|[.,!?;:]+$/g, "")), 0, { decided });

test("commands parse like nav.py", () => {
  const cases = [
    ["go left", ["move:char:left", 1, 2]], ["go left three words", ["move:word:left", 3, 4]],
    ["move right 2 characters", ["move:char:right", 2, 4]], ["go back a word", ["move:word:left", 1, 4]],
    ["go up two lines", ["move:line:up", 2, 4]], ["go to end of line", ["move:line:end", 1, 5]],
    ["go to the start of the document", ["move:doc:start", 1, 7]], ["Go to the beginning of the line.", ["move:line:start", 1, 7]],
    ["select previous word", ["select:word:left", 1, 3]], ["select the next four words", ["select:word:right", 4, 5]],
    ["select previous line", ["select:line:up", 1, 3]], ["select all", ["select:all", 1, 2]], ["select this line", ["select:line:here", 1, 3]],
    ["delete previous word", ["delete:word:left", 1, 3]], ["delete the line", ["delete:line:here", 1, 3]],
    ["press tab", ["press:tab", 1, 2]], ["press escape twice", ["press:escape", 2, 3]], ["press page down", ["press:pagedown", 1, 3]],
    ["press down three times", ["press:down", 3, 4]],
    ["go to the store", null], ["press the button", null], ["select previous", null], ["delete that", null],
    ["move left line", null], ["go up three words", null], ["delete previous line", null], ["press enter", null],
    ["go left 50 words", null], ["go left twenty words", ["move:word:left", 20, 4]],
  ];
  for (const [spoken, want] of cases) assert.deepEqual(parse(spoken), want, spoken);
});
test("an open tail waits; a complete one does not", () => {
  for (const s of ["go", "go left", "go to end of", "select previous", "go left three", "press page"]) assert.equal(parse(s, false), PENDING, s);
  assert.deepEqual(parse("select previous word", false), ["select:word:left", 1, 3]);
});
test("chords: editors, readline terminals, repeats, refusals", () => {
  assert.deepEqual(chordsFor("select:word:left", 2, EDITOR), [["shift", "ctrl", "left"], ["shift", "ctrl", "left"]]);
  assert.deepEqual(chordsFor("select:all", 5, EDITOR), [["ctrl", "a"]]);
  assert.deepEqual(chordsFor("press:tab", 3, EDITOR), [["tab"], ["tab"], ["tab"]]);
  assert.deepEqual(chordsFor("move:word:left", 1, LINUX_TERMINAL), [["alt", "b"]]);
  assert.deepEqual(chordsFor("delete:word:left", 1, LINUX_TERMINAL), [["ctrl", "w"]]);
  assert.equal(chordsFor("select:word:left", 1, LINUX_TERMINAL), null);
  assert.equal(chordsFor("move:line:up", 1, LINUX_TERMINAL), null);
  assert.deepEqual(keymap({ terminal: true }), LINUX_TERMINAL);
});
test("the visitor's platform picks the keys", () => {
  assert.deepEqual(chordsFor("move:word:left", 1, keymap({ terminal: false, platform: "mac" })), [["alt", "left"]]);
  assert.deepEqual(chordsFor("move:line:end", 1, keymap({ terminal: false, platform: "mac" })), [["cmd", "right"]]);
  assert.deepEqual(chordsFor("move:word:left", 1, keymap({ terminal: true, platform: "mac" })), [["escape"], ["b"]]);
  assert.deepEqual(chordsFor("move:word:left", 1, keymap({ terminal: true, platform: "windows" })), [["ctrl", "left"]]);
  assert.equal(chordsFor("delete:line:here", 1, keymap({ terminal: true, platform: "windows" })), null);
  assert.deepEqual(keymap({ terminal: false, platform: "windows" }), EDITOR);
});
test("no table ever presses Enter", () => {
  const tables = [EDITOR, LINUX_TERMINAL, MAC_EDITOR, MAC_TERMINAL, WINDOWS_TERMINAL];
  const actions = new Set([...tables.flatMap((t) => Object.keys(t)), ...[...PRESS_KEYS.values()].map((k) => "press:" + k)]);
  for (const table of tables) for (const a of actions) for (const chord of chordsFor(a, 1, table) || []) {
    assert.ok(!chord.some((n) => ["enter", "return", "kpenter"].includes(n)), a);
    assert.ok(!(chord.includes("ctrl") && (chord.includes("j") || chord.includes("m"))), a);
  }
  assert.equal(chordsFor("press:enter", 1, EDITOR), null);
});
test("labels read like the overlay", () => {
  assert.equal(label("select:word:left", 2), "select word left ×2");
  assert.equal(label("move:line:end", 1), "line end");
});
