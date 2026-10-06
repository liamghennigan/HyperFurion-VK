#!/usr/bin/env python3
"""The flow parity corpus: the daemon's engine and the landing page's.

The page at docs/ runs a JavaScript port of voice_keyboard/flow/ — the
same grammar, registers, compilers, spelling, navigation and molten
engine. "The same engine" is a claim this corpus turns into a test: every
case here is run through the Python FlowEngine, and the result is written
to tests/flow_corpus.json; tests/test_flow_corpus.py checks that Python
still produces it, and docs/js/test/parity.test.mjs checks that the port
does too. Both run in CI.

Each case feeds its segments as final transcripts (one engine, the
segments appended the way streaming finals accumulate), walks every
navigation barrier as if the keys were pressed, and records what the
engine wanted on screen after each segment, each action it raised, and
the finalize result.

    python scripts/flow_corpus.py          # rewrite tests/flow_corpus.json
    python scripts/flow_corpus.py --check  # exit 1 if the file is stale
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice_keyboard.flow.engine import FlowConfig, FlowEngine  # noqa: E402
from voice_keyboard.flow.grammar import Grammar  # noqa: E402
from voice_keyboard.flow.registers import REGISTERS  # noqa: E402

CORPUS = ROOT / "tests" / "flow_corpus.json"

# (name, register, segments, options)
CASES = [
    # the grammar and the registers
    ("prose caps and punctuation", "prose", ["hello world period this is a test comma right question mark"], {}),
    ("prose keeps small numbers as words", "prose", ["no one knows the nine lives"], {}),
    ("terminal digits", "terminal", ["twenty three failed tests comma rerun the flaky ones"], {}),
    ("digit runs and decimals", "terminal", ["ping one two seven dot zero dot zero dot one", "pi is three point one four"], {}),
    ("python compiles", "python", ["for i in range ten colon"], {}),
    ("python print", "python", ["print x close paren"], {}),
    ("shell compiles flags", "shell", ["pipe grep dash i error"], {}),
    ("shell path", "shell", ["pytest dash x tests slash test underscore flow dot py"], {}),
    ("verbatim is words", "verbatim", ["scratch that period new line"], {}),
    ("new line and paragraph", "prose", ["first line new line second line new paragraph third"], {}),
    ("quotes and dashes", "prose", ["open quote hyper furion close quote em dash a voice keyboard"], {}),
    ("literal", "prose", ["say literal period to type the word"], {}),
    ("vocabulary with trailing punctuation", "prose", ["hyper furion, is open source period"], {}),
    # scratch that
    ("scratch mid-utterance", "prose", ["fixed the race addition scratch that fixed the race condition period"], {}),
    ("scratch as its own segment", "prose", ["hello world period", "scratch that", "goodbye period"], {}),
    ("scratch twice", "prose", ["one", "two", "three", "scratch that", "scratch that"], {}),
    ("scratch with nothing typed", "prose", ["scratch that"], {}),
    # the wake word
    ("instruction at the end", "prose", ["i think it works now vk make that formal"], {}),
    ("instruction alone", "prose", ["hello", "vk make that formal"], {}),
    # spelling
    ("spell that replaces keeping punctuation", "prose", ["I run wrold.", "spell that w o r l d"], {}),
    ("spell that keeps a capital", "prose", ["Ngnix spell that n g i n x"], {}),
    ("spell that with nato and capital", "prose", ["the host is engine-x", "spell that capital n golf india november x-ray"], {}),
    ("spell that hyphenated", "prose", ["use ngnix", "spell that n-g-i-n-x"], {}),
    ("spell inserts", "terminal", ["kubectl spell k eight s"], {}),
    ("spell letters follow in the next segment", "prose", ["hello wrold", "spell that", "w o r l d"], {}),
    ("spell then more words", "prose", ["hello wrold spell that w o r l d then more"], {}),
    ("spell ambiguous tail stays prose", "prose", ["mark it x spell that y a good one"], {}),
    ("spell nothing to replace", "prose", ["spell that a b"], {}),
    ("cast a spell stays prose", "prose", ["cast a spell on them"], {}),
    ("spelling off", "prose", ["wrold spell that w o r l d"], {"spelling": False}),
    ("scratch after respell rewinds the shifted boundary", "prose",
     ["first part", "ngnix", "spell that n g i n x", "more words scratch that"], {}),
    # navigation
    ("select previous word then type over", "prose", ["hello world", "select previous word", "planet"], {"nav": True}),
    ("go to end of line keeps spacing", "prose", ["one two", "go to end of line", "three"], {"nav": True}),
    ("go to start of line glues", "prose", ["one", "go to start of line", "two"], {"nav": True}),
    ("several commands", "prose", ["alpha", "go left", "beta", "press tab", "gamma"], {"nav": True}),
    ("count and unit", "prose", ["a b c d", "select the next four words", "x"], {"nav": True}),
    ("delete line", "prose", ["old line", "delete the line", "fresh text"], {"nav": True}),
    ("press escape twice", "prose", ["hello", "press escape twice", "world"], {"nav": True}),
    ("mid-sentence command is typed", "prose", ["please go left three words now"], {"nav": True}),
    ("command split across segments is words", "prose", ["select", "previous word"], {"nav": True}),
    ("spelling split across three segments", "prose", ["hello wrold", "spell that w o r", "l d"], {}),
    ("spell held then flushed by time is not regrown", "prose", ["hello wrold spell that w o r", "l d"], {}),
    ("scratch cannot cross a command", "prose", ["keep this", "go left", "oops scratch that"], {"nav": True}),
    ("nav off types the words", "prose", ["hello", "select previous word"], {}),
    ("terminal navigation", "shell", ["pytest dash x tests slash test underscore flow dot py", "delete previous word", "tests slash test underscore nav dot py"], {"nav": True}),
    ("go left twenty words", "prose", ["many words here", "go left twenty words", "front"], {"nav": True}),
    ("out of range count is prose", "prose", ["go left fifty words"], {"nav": True}),
    ("spell after a selection", "prose", ["hello wrold", "select previous word", "spell that w o r l d"], {"nav": True}),
    ("numbers always in prose", "prose", ["nine lives and twenty three cats"], {"numbers": "always"}),
    ("number glue and thousands", "terminal", ["two thousand twenty six and counting", "one hundred and five"], {}),
    ("digit pairs and attached punctuation", "terminal", ["version one two period then four. five six"], {}),
    ("twenty hundred and zero", "terminal", ["twenty hundred then zero then zero zero"], {}),
    ("numbers off", "terminal", ["twenty three"], {"numbers": "off"}),
]


def make_engine(register_name: str, opts: dict) -> FlowEngine:
    register = REGISTERS[register_name]
    grammar = Grammar(
        enabled=register.grammar_enabled,
        # [flow.vocabulary]'s documented example, which the page ships live
        vocabulary={"hyper furion": "HyperFurion"},
        wake_word="vk",
        numbers=opts.get("numbers", "auto"),
        numbers_on=register.numbers_on,
        numbers_min=register.numbers_min,
        spelling=opts.get("spelling", True),
        nav=opts.get("nav", False),
    )
    return FlowEngine(FlowConfig(adaptive=False), grammar, register)


def run_case(register_name: str, segments: list[str], opts: dict) -> list:
    engine = make_engine(register_name, opts)
    merged = ""
    now = 0.0
    events: list = []
    for segment in segments:
        merged = (merged + " " + segment).strip()
        now += 1.0
        engine.on_transcript(merged, is_final=True, now=now)
        while engine.pending_action() is not None:
            action = engine.pending_action()
            events.append(["action", action.action, action.count, engine.desired_text()])
            engine.complete_action(now, pressed=True)
        events.append(["screen", engine.desired_text()])
    result = engine.finalize(merged, now=now + 1.0)
    while result.action is not None:
        events.append(["action", result.action.action, result.action.count, result.text])
        result = engine.complete_action(now + 1.0, pressed=True)
    events.append([
        "final", result.text, result.typed_before, result.instruction, result.scratches,
        [list(pair) for pair in result.corrections],
    ])
    return events


def build() -> list[dict]:
    out = []
    for name, register, segments, opts in CASES:
        out.append({
            "name": name, "register": register, "segments": segments, "options": opts,
            "events": run_case(register, segments, opts),
        })
    return out


def main() -> int:
    corpus = build()
    text = json.dumps(corpus, indent=1, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not CORPUS.exists() or CORPUS.read_text() != text:
            print(f"{CORPUS} is stale: run scripts/flow_corpus.py", file=sys.stderr)
            return 1
        print(f"{CORPUS}: {len(corpus)} cases, up to date")
        return 0
    CORPUS.write_text(text)
    print(f"wrote {CORPUS}: {len(corpus)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
