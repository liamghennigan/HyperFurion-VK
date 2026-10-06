import { test } from "node:test";
import assert from "node:assert/strict";
import { intentRequest, pageCommand } from "../intent.js";

test("an instruction that starts with an intent verb is a request", () => {
  assert.equal(intentRequest("run find every todo"), "find every todo");
  assert.equal(intentRequest("Run, list files"), "list files");
  assert.equal(intentRequest("make that formal"), null);
  assert.equal(intentRequest("run"), null);
});
test("the prompt's own examples, and a few more, compile without a model", () => {
  const cases = [
    ["list files sorted by size", "ls -lS"], ["find every todo in this repo", "grep -rn TODO ."],
    ["find all fixmes", "grep -rn FIXME ."], ["find every deprecation warning in this project", 'grep -rn "deprecation warning" .'],
    ["show running docker containers", "docker ps"], ["undo my last commit but keep the changes", "git reset --soft HEAD~1"],
    ["make a new branch called test and switch to it", "git checkout -b test"], ["git status", "git status"],
    ["show the last five commits", "git log --oneline -5"], ["what's listening on port eighty", "lsof -i :80"],
    ["disk usage", "df -h"], ["count lines in main.py", "wc -l main.py"],
  ];
  for (const [req, cmd] of cases) assert.deepEqual(pageCommand(req), { command: cmd, known: true }, req);
});
test("an unknown request becomes a comment line, never a command", () => {
  const r = pageCommand("deploy to production right now");
  assert.equal(r.known, false);
  assert.ok(r.command.startsWith("# deploy to production right now"));
});
test("a marker the recognizer wrote in words or with a hyphen is still the marker", () => {
  for (const req of ["find every to do", "find every to do.", "find every To-Do in this repo", "find all to dos", "find every todos"])
    assert.deepEqual(pageCommand(req), { command: "grep -rn TODO .", known: true }, req);
  assert.deepEqual(pageCommand("find all fix me"), { command: "grep -rn FIXME .", known: true });
  assert.deepEqual(pageCommand("find every to do list"), { command: 'grep -rn "to do list" .', known: true });
});
