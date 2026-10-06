"""Differential fuzzing: random dictations through the Python engine, then
the same streams through the page's port (docs/js/flow.js), screen by
screen.

    python scripts/flow_fuzz_parity.py [count] [first-seed]

Writes the streams with the Python engine's events to a temporary corpus
and runs docs/js/test/parity.test.mjs on it (VK_CORPUS). Any failing test
names its seed; add the stream to STREAMS in flow_corpus.py to keep it.
"""

import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_corpus  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VOCAB = (
    "the the a we ship it on friday monday october sixth twenty five nineteen eighty one two three four seven oh "
    "percent dollars and fifty cents pm am i quote unquote end said period comma new line "
    "bullet number heading scratch that spell n g x correct to cap uppercase select undo "
    "go left word emoji rocket point hundred thousand euros june first second graders "
    "literal snake case user id dot com at example slash million dollars people double equals not less than value error type and greater than um"
).split()


def random_steps(rng: random.Random) -> list:
    steps, final, current, now = [], [], [], 0.0
    for _ in range(rng.randint(3, 30)):
        # Times on a 0.137 s grid: no difference lands exactly on a window
        # (0.6, 1.5, 3.0 s), where float seconds and the page's float
        # milliseconds may round to opposite sides.
        now = round(now + 0.137 * rng.choice((1, 2, 5, 12)), 3)
        roll = rng.random()
        if roll < 0.55:
            current.append(rng.choice(VOCAB))
        elif roll < 0.65 and current:
            current[rng.randrange(len(current))] = rng.choice(VOCAB)
        elif roll < 0.7 and current:
            current.pop()
        elif roll < 0.8:
            steps.append([now, "tick"])
            continue
        elif roll < 0.85:
            steps.append([now, "press"])
            continue
        elif current:
            final += current
            current = []
            steps.append([now, " ".join(final), "final"])
            continue
        if final or current:
            steps.append([now, " ".join(final + current), "interim"])
    return steps or [[0.0, "the", "final"]]


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    first = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    cases = []
    for seed in range(first, first + count):
        rng = random.Random(seed)
        register = rng.choice(("prose", "prose", "prose", "terminal", "python", "shell"))
        opts = {"nav": rng.random() < 0.5, "pause_review": rng.choice(("off", "rules"))}
        steps = random_steps(rng)
        try:
            events = flow_corpus.run_stream(register, steps, opts)
        except Exception as exc:  # the Python engine itself failed
            print(f"seed {seed}: python engine raised {exc!r}")
            return 1
        cases.append({"name": f"seed {seed}", "register": register, "steps": steps,
                      "options": opts, "events": events})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(cases, handle, ensure_ascii=False)
    env = dict(os.environ, VK_CORPUS=handle.name)
    proc = subprocess.run(
        ["node", "--test", "--test-reporter=tap", "docs/js/test/parity.test.mjs"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    failed = [line.split("parity · ")[1] for line in proc.stdout.splitlines() if line.startswith("not ok")]
    if failed:
        print(f"{len(failed)} of {count} streams differ: " + ", ".join(failed))
    else:
        print(f"{count} random streams: page and daemon agree")
    os.unlink(handle.name)
    return proc.returncode or (1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
