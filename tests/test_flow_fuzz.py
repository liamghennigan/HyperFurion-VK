"""Random dictations through the engine: whatever is said, revised, ticked
or finalized, text already typed must read back exactly as it was typed
(no "committed items changed under reparse"), and nothing may crash.

A fixed set of seeds keeps it deterministic; widen SEEDS locally to hunt.
"""

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import flow_corpus  # noqa: E402

SEEDS = range(400)

# Words that start commands, folds, quotes and lists, mixed with plain ones.
VOCAB = (
    "the a we ship it on friday monday october sixth twenty five one two three four seven oh "
    "percent dollars and fifty cents pm am i quote unquote end said period comma new line "
    "bullet number heading scratch that spell n g x correct to cap uppercase select undo "
    "go left word emoji rocket point hundred thousand euros june first second graders "
    "literal snake case user id dot com at example um"
).split()


def _dictate(rng: random.Random, register: str, opts: dict) -> None:
    engine = flow_corpus.make_engine(register, opts)
    now, final, current = 0.0, [], []
    for _ in range(rng.randint(3, 40)):
        roll = rng.random()
        now += rng.choice((0.1, 0.3, 0.6, 1.6))
        if roll < 0.55:
            current.append(rng.choice(VOCAB))
        elif roll < 0.65 and current:
            current[rng.randrange(len(current))] = rng.choice(VOCAB)  # a revision
        elif roll < 0.7 and current:
            current.pop()
        elif roll < 0.8:
            engine.on_tick(now=now)
            continue
        elif current:
            final += current
            current = []
            engine.on_transcript(" ".join(final), is_final=True, now=now)
            while engine.pending_action() is not None:
                engine.complete_action(now, pressed=rng.random() < 0.8)
            continue
        engine.on_transcript(" ".join(final + current), is_final=False, now=now)
        while engine.pending_action() is not None:
            engine.complete_action(now, pressed=True)
    result = engine.finalize(" ".join(final + current), now=now + 1.0)
    while result.action is not None:
        result = engine.complete_action(now + 1.0, pressed=True)


@pytest.mark.parametrize("chunk", range(4))
def test_random_dictations_keep_the_fence(chunk: int, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="voice_keyboard.flow.engine"):
        for seed in SEEDS[chunk::4]:
            rng = random.Random(seed)
            register = rng.choice(("prose", "prose", "prose", "terminal", "python"))
            opts = {"nav": rng.random() < 0.5, "pause_review": rng.choice(("off", "rules"))}
            caplog.clear()
            _dictate(rng, register, opts)
            changed = [r for r in caplog.records if "committed items changed" in r.getMessage()]
            assert not changed, f"seed {seed} ({register}, {opts})"
