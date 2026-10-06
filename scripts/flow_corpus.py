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
the finalize result. A streaming case instead feeds timed steps —
interim transcripts, finals, and bare ticks on a clock in seconds — and
records the screen after every step: the stability window, the adaptive
horizon, the holdback expiry and the non-ASCII eager commit all run on
that clock in both engines. A case may `continue` text left at the
caret by an earlier recording ([flow] rejoin).

    python scripts/flow_corpus.py          # rewrite tests/flow_corpus.json
    python scripts/flow_corpus.py --check  # exit 1 if the file is stale
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice_keyboard.flow.engine import FlowConfig, FlowEngine  # noqa: E402
from voice_keyboard.flow.grammar import DEFAULT_FILLERS, Grammar  # noqa: E402
from voice_keyboard.flow.registers import REGISTERS, continuation_state  # noqa: E402

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
    ("a spoken open paren after a callable is absorbed", "python", ["print open paren x close paren"], {}),
    ("calls and subscripts glue to the name", "python",
     ["def snake case get user open paren user close paren colon", "items open bracket zero close bracket"], {}),
    ("keywords and operators keep their space", "python",
     ["x equals open paren a plus b close paren", "if open paren x close paren colon", "return open paren a close paren"], {}),
    ("a method call", "python", ["foo dot bar open paren close paren"], {}),
    ("calls nest and a colon closes them all", "python", ["for i in range len xs colon"], {}),
    ("nested calls closed by hand", "python", ["print range ten close paren close paren", "x equals abs y close paren"], {}),
    ("a nested call across segments", "python", ["for i in range", "len xs colon"], {}),
    ("shell compiles flags", "shell", ["pipe grep dash i error"], {}),
    ("shell path", "shell", ["pytest dash x tests slash test underscore flow dot py"], {}),
    ("verbatim is words", "verbatim", ["scratch that period new line"], {}),
    ("new line and paragraph", "prose", ["first line new line second line new paragraph third"], {}),
    ("bullets", "prose", ["shopping list colon new bullet milk new bullet eggs"], {}),
    ("a lowercase recognizer's i is a capital", "prose", ["i think i can and i'm sure i'll say i'd"], {}),
    ("days and months are capitals", "prose", ["see you monday, or friday's meeting in october but you may march"], {}),
    ("initialisms are capitals", "prose", ["ok send the pdf asap, fyi it's for us"], {}),
    ("initialisms in code stay as said", "python", ["ok equals api"], {}),
    ("i inside a word stays", "prose", ["it is in it"], {}),
    ("prose percent", "prose", ["about twenty five percent of users"], {}),
    ("prose percent held while percent might grow", "prose", ["it went up one hundred percent"], {}),
    ("prose dollars", "prose", ["it costs twenty five dollars period"], {}),
    ("emoji", "prose", ["ship it emoji rocket emoji thumbs up.", "nice emoji fire"], {}),
    ("emoji names alone stay words", "prose", ["the emoji was a smile"], {}),
    ("phone numbers", "prose", ["call five five five one two three four or four one five five five five one two one two"], {}),
    ("a short digit count stays words", "prose", ["one two three go"], {}),
    ("dates", "prose", ["due october sixth or june twenty first, not july thirtieth."], {}),
    ("a month and a cardinal stay words", "prose", ["in june twenty people came and you may first check"], {}),
    ("dollars and cents", "prose", ["it costs five dollars and fifty cents and twenty dollars and five cents period"], {}),
    ("dollars and something else", "prose", ["five dollars and some change"], {}),
    ("prose decimal percent", "prose", ["growth was two point five percent"], {}),
    ("prose times", "prose", ["meet at three thirty pm or twelve am"], {}),
    ("which one am I stays words", "prose", ["which one am i"], {}),
    ("prose digits and a unit", "prose", ["up 25 percent"], {}),
    ("a digit string is never an amount", "prose", ["one two three percent"], {}),
    ("prose numbers alone stay words", "prose", ["no one knows the twenty five reasons"], {}),
    ("glue never starts an amount", "prose", ["this and five percent"], {}),
    ("units across a pause stay words", "prose", ["twenty five", "percent"], {}),
    ("numbers off leaves units alone", "prose", ["twenty five percent"], {"numbers": "off"}),
    ("numbered lists count", "prose", ["steps colon new number build new number test new number ship"], {}),
    ("a new paragraph starts a new list", "prose", ["new number a new number b new paragraph new number c"], {}),
    ("scratching a numbered item gives its number back", "prose", ["new number a", "new number b", "scratch that", "new number c"], {}),
    ("a bullet after a line break needs no other", "prose", ["notes new line new bullet one", "new bullet first"], {}),
    ("a bullet in a terminal is nothing", "terminal", ["ls new bullet x"], {}),
    ("a bullet in code is nothing", "python", ["x new bullet equals one"], {}),
    ("scratch after a bullet", "prose", ["todo colon", "new bullet milk", "scratch that", "new bullet eggs"], {}),
    ("a terminal never gets a line break", "terminal", ["rm dash rf build new line", "ls new paragraph"], {}),
    ("a shell never gets a line break", "shell", ["make clean new line make"], {}),
    ("quotes and dashes", "prose", ["open quote hyper furion close quote em dash a voice keyboard"], {}),
    ("literal", "prose", ["say literal period to type the word"], {}),
    ("vocabulary with trailing punctuation", "prose", ["hyper furion, is open source period"], {}),
    # scratch that
    ("scratch mid-utterance", "prose", ["fixed the race addition scratch that fixed the race condition period"], {}),
    ("scratch as its own segment", "prose", ["hello world period", "scratch that", "goodbye period"], {}),
    ("scratch twice", "prose", ["one", "two", "three", "scratch that", "scratch that"], {}),
    ("scratch with nothing typed", "prose", ["scratch that"], {}),
    ("scratch synonyms", "prose", ["one", "delete that", "two", "scratched that", "three"], {}),
    ("everyday words are not commands", "prose", ["I cannot undo that decision", "strike that pose", "the first bullet point is about cost"], {}),
    ("a recognizer's past tense still scratches", "prose", ["keep this", "drop this", "scratched that"], {}),
    # the wake word
    ("instruction at the end", "prose", ["i think it works now vk make that formal"], {}),
    ("instruction alone", "prose", ["hello", "vk make that formal"], {}),
    # spelling
    ("spell that replaces keeping punctuation", "prose", ["I run wrold.", "spell that w o r l d"], {}),
    ("spell that keeps a capital", "prose", ["Ngnix spell that n g i n x"], {}),
    ("spell that with nato and capital", "prose", ["the host is engine-x", "spell that capital n golf india november x-ray"], {}),
    ("spell that hyphenated", "prose", ["use ngnix", "spell that n-g-i-n-x"], {}),
    ("spell inserts", "terminal", ["kubectl spell k eight s"], {}),
    ("spell that heard as one capitalized word", "prose", ["the proxy is engine-x", "spell that NGINX"], {}),
    ("spell a capitalized word inserts", "terminal", ["kubectl spell K8S now"], {}),
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
    # [flow] fillers: hesitation sounds are dropped, with their commas
    ("fillers at the start", "prose", ["Um, so we should ship it."], {}),
    ("fillers bracketed by commas", "prose", ["we should, uh, ship it"], {}),
    ("a filler's sentence end survives", "prose", ["I think so, um.", "Next one."], {}),
    ("a filler alone says nothing", "prose", ["Uh.", "Okay then"], {}),
    ("fillers in a row", "prose", ["well, um, um, yes"], {}),
    ("a filler-like word is a word", "prose", ["the umbrella is red, uhm, blue"], {}),
    ("literal keeps a filler", "prose", ["say literal um please"], {}),
    ("fillers off", "prose", ["we should, uh, ship it"], {"fillers": []}),
    ("a filler after a pause lets the next word decide", "prose",
     ["I was thinking about the project.", "Um, and how it works."], {"pause_review": "rules"}),
    ("a comma word at a segment end keeps its comma", "prose", ["we should,", "uh, ship it"], {}),
    # spoken case formatters
    ("snake and camel case where asked", "prose", ["call camel case get user name, then snake case user id."], {"formatters": "everywhere"}),
    ("prose keeps formatter words", "prose", ["there is no space left on the disk", "we use snake case for names"], {}),
    ("formatters in python", "python", ["for snake case row count in range ten colon"], {}),
    ("a formatter stops at an operator", "python", ["snake case user id equals five"], {}),
    ("pascal and constant", "python", ["class pascal case http client", "constant case max retries equals three"], {}),
    ("title case keeps small words", "prose", ["title case the lord of the rings period"], {"formatters": "everywhere"}),
    ("kebab in a shell", "shell", ["git checkout dash b kebab case fix the login bug"], {}),
    ("all caps and no space", "prose", ["all caps do not merge, then no space data base"], {"formatters": "everywhere"}),
    ("a vocabulary phrase wins over a formatter's words", "prose", ["no space hyper furion"], {"formatters": "everywhere"}),
    ("a formatter word alone is a word", "prose", ["the camel walked all the way", "no one came"], {"formatters": "everywhere"}),
    ("a formatter ends at the pause", "terminal", ["export snake case user id", "equals five"], {}),
    ("explicit parens inside a spoken call", "python", ["for x in sorted foo open paren close paren colon"], {}),
    # spoken addresses
    ("an email", "prose", ["email liam at example dot com please"], {}),
    ("an email starts a sentence in lowercase", "prose", ["liam at gmail dot com.", "next one"], {}),
    ("a dotted mailbox", "prose", ["liam dot hennigan at gmail dot com"], {}),
    ("a domain", "prose", ["see docs dot python dot org."], {}),
    ("the longest domain wins", "prose", ["go to example dot co dot uk now"], {}),
    ("prose with at and dot stays prose", "prose", ["meet at the office", "the dot product", "we are at dot com scale"], {}),
    ("a dot is a dot", "prose", ["put a red dot in the middle", "click the blue dot at the top", "the dot com bubble"], {}),
    ("a domain in a terminal", "terminal", ["ping example dot com"], {}),
    ("an address across segments is words", "prose", ["liam at example", "dot com"], {}),
    # [flow] rejoin: continuing text an earlier recording left at the caret
    ("rejoin after a sentence end", "prose", ["next sentence"], {"continues": "Hello world."}),
    ("rejoin mid-sentence", "prose", ["and more"], {"continues": "Hello world"}),
    ("rejoin then scratch rewinds to the join", "prose", ["oops", "scratch that better"], {"continues": "Hello world"}),
    ("rejoin in a terminal glues", "terminal", ["tmp"], {"continues": "cd"}),
]

# [flow] pause_review = "rules": the recognizer's period at a pause stays
# revisable until the words after the pause decide it
PAUSES = [
    ("a pause before and joins the sentence", ["i was thinking about the project.", "And how we could make it simpler."]),
    ("a pause before but takes a comma", ["we could ship today.", "But the tests are red."]),
    ("a pause after an open ending joins", ["send it to the.", "Team by noon."]),
    ("a pause after an open ending keeps a name's capital", ["send it to.", "Friday is fine."]),
    ("an unclear pause keeps the period", ["that is done.", "Next the docs."]),
    ("spoken punctuation replaces the guess", ["that is done.", "comma then the docs"]),
    ("a command after a pause keeps the sentence end", ["that is done.", "scratch that", "all done."]),
    ("an abbreviation's period is not a pause", ["see fig.", "Two for details."]),
    ("a lowercase word seen earlier loses its capital", ["the furion daemon.", "The.", "Furion overlay too."]),
]
for name, segments in PAUSES:
    CASES.append(("pause · " + name, "prose", segments, {"pause_review": "rules"}))
CASES.append(("pause review off keeps the recognizer's periods", "prose",
              ["i was thinking about the project.", "And how we could make it simpler."], {}))
CASES.append(("pauses are a prose thing", "terminal", ["cd the.", "And then ls"], {"pause_review": "rules"}))

# streaming cases: (name, register, steps, options); a step is
# [t, text, "interim" | "final"] or [t, "tick"]
STREAMS = [
    ("words land and freeze on the window", "prose", [
        [0.0, "fixed the", "interim"], [0.3, "fixed the race", "interim"], [0.6, "fixed the race condition", "interim"],
        [0.9, "fixed the race condition", "interim"], [1.2, "tick"], [2.2, "tick"], [2.5, "fixed the race condition in", "interim"],
        [4.0, "tick"], [4.5, "fixed the race condition in the audio thread", "final"]], {}),
    ("a revision repairs the molten tail", "prose", [
        [0.0, "fixed the race addition", "interim"], [0.3, "fixed the race addition in", "interim"],
        [0.6, "fixed the race condition in the", "interim"], [0.9, "fixed the race condition in the audio", "interim"],
        [2.0, "tick"], [2.6, "tick"], [3.0, "fixed the race condition in the audio thread", "final"]], {}),
    ("a deep revision widens the horizon", "prose", [
        [0.0, "we need to ship the thing today", "interim"], [0.2, "we need to shift the ring to bay", "interim"],
        [0.4, "we need to ship the thing today", "interim"], [0.6, "we need to ship the thing today", "interim"],
        [2.2, "tick"], [2.4, "we need to ship the thing today", "interim"], [2.6, "we need to ship the thing today", "interim"],
        [2.8, "we need to ship the thing today", "interim"], [4.5, "tick"], [6.0, "we need to ship the thing today", "final"]], {}),
    ("a half phrase is held, then let go", "prose", [
        [0.0, "ready open", "interim"], [0.3, "ready open", "interim"], [0.6, "ready open", "interim"],
        [2.0, "tick"], [2.5, "tick"], [3.2, "tick"], [3.6, "ready open", "interim"], [4.0, "ready open quote", "final"]], {}),
    ("an email waits until it can't grow", "prose", [
        [0.0, "mail liam", "interim"], [0.3, "mail liam at", "interim"], [0.6, "mail liam at example", "interim"],
        [0.9, "mail liam at example dot", "interim"], [1.2, "mail liam at example dot com", "interim"],
        [1.5, "mail liam at example dot com", "interim"], [3.5, "tick"], [4.0, "mail liam at example dot com today", "interim"],
        [6.5, "tick"], [7.0, "mail liam at example dot com today", "final"]], {}),
    ("a comma word waits for one more word, then merges with a filler", "prose", [
        [0.0, "we should,", "interim"], [0.3, "we should,", "interim"], [0.6, "we should, uh,", "interim"],
        [0.9, "we should, uh, ship", "interim"], [3.0, "tick"], [3.3, "we should, uh, ship it", "interim"],
        [5.0, "we should, uh, ship it", "final"]], {}),
    ("a held number is let go, then a bigger number follows", "terminal", [
        [0.0, "one", "interim"], [0.3, "one", "interim"], [0.6, "one", "interim"], [3.2, "tick"], [3.4, "tick"],
        [3.6, "one hundred", "interim"], [3.9, "one hundred", "interim"], [4.2, "one hundred", "interim"], [7.0, "tick"],
        [7.5, "one hundred five", "final"]], {}),
    ("words committed one at a time never merge", "prose", [
        [0.0, "say open", "interim"], [0.3, "say open", "interim"], [0.6, "say open", "interim"], [3.2, "tick"],
        [3.5, "say open quote", "interim"], [3.8, "say open quote", "interim"], [4.1, "say open quote", "interim"], [6.5, "tick"],
        [7.0, "say open quote hi close quote", "final"]], {}),
    ("a bare literal stays bare when more words commit", "prose", [
        [0.0, "say literal", "interim"], [0.5, "say literal", "interim"], [1.0, "say literal", "interim"], [4.0, "tick"],
        [5.0, "say literal period now", "final"]], {}),
    ("a non ascii word commits early", "prose", [
        [0.0, "café au lait", "interim"], [0.2, "café au lait", "interim"], [0.4, "café au lait", "interim"],
        [0.6, "tick"], [3.0, "café au lait", "final"]], {}),
    ("a scratch mid-stream", "prose", [
        [0.0, "send it to bob", "interim"], [0.3, "send it to bob", "interim"], [2.0, "tick"],
        [2.2, "send it to bob scratch that", "interim"], [2.5, "send it to bob scratch that send it to alice", "interim"],
        [4.5, "tick"], [5.0, "send it to bob scratch that send it to alice", "final"]], {}),
    ("a long molten tail is forced to commit", "prose", [
        [0.0, " ".join(["word"] * 40), "interim"], [0.1, " ".join(["word"] * 45), "interim"], [0.2, "tick"],
        [1.0, " ".join(["word"] * 45), "final"]], {}),
    ("a pause settles once the next word has", "prose", [
        [0.0, "i was thinking about the project.", "final"], [0.3, "i was thinking about the project. And", "interim"],
        [0.5, "i was thinking about the project. And how", "interim"], [1.5, "tick"], [2.5, "tick"],
        [3.0, "i was thinking about the project. And how we could make it simpler.", "final"]], {"pause_review": "rules"}),
    ("a pause with nothing after it yet", "prose", [
        [0.0, "that is done.", "final"], [1.0, "tick"], [2.0, "tick"], [2.5, "that is done. Next", "interim"], [3.5, "tick"],
        [4.0, "that is done. Next the docs.", "final"]], {"pause_review": "rules"}),
    ("a command waits at a barrier while words keep coming", "prose", [
        [0.0, "hello world", "final"], [1.0, "hello world select previous word", "final"],
        [1.2, "hello world select previous word pla", "interim"], [1.4, "hello world select previous word planet", "interim"],
        [1.6, "press"], [1.8, "hello world select previous word planet", "interim"], [3.5, "tick"],
        [4.0, "hello world select previous word planet", "final"]], {"nav": True}),
]


def make_engine(register_name: str, opts: dict) -> FlowEngine:
    register = REGISTERS[register_name]
    initial = continuation_state(opts["continues"], register) if opts.get("continues") else None
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
        fillers=opts.get("fillers", DEFAULT_FILLERS),
        formatters=opts.get("formatters", "code"),
        code=bool(register.compiler) or register.terminal,
    )
    config = FlowConfig(pause_review=opts.get("pause_review", "off"))
    return FlowEngine(config, grammar, register, initial_state=initial)


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


def run_stream(register_name: str, steps: list, opts: dict) -> list:
    engine = make_engine(register_name, opts)
    events: list = []
    last = ""
    for step in steps:
        now = float(step[0])
        if step[1] == "tick":
            engine.on_tick(now=now)
        elif step[1] == "press":
            action = engine.pending_action()
            events.append(["action", action.action if action else None, action.count if action else 0, engine.desired_text()])
            engine.complete_action(now, pressed=True)
        else:
            last = step[1]
            engine.on_transcript(last, is_final=step[2] == "final", now=now)
        events.append(["screen", engine.desired_text()])
    result = engine.finalize(last, now=float(steps[-1][0]) + 1.0)
    events.append(["final", result.text, result.typed_before, result.instruction, result.scratches,
                   [list(pair) for pair in result.corrections]])
    return events


def build() -> list[dict]:
    out = []
    for name, register, segments, opts in CASES:
        out.append({
            "name": name, "register": register, "segments": segments, "options": opts,
            "events": run_case(register, segments, opts),
        })
    for name, register, steps, opts in STREAMS:
        out.append({
            "name": name, "register": register, "steps": steps, "options": opts,
            "events": run_stream(register, steps, opts),
        })
    return out


def main() -> int:
    corpus = build()
    text = json.dumps(corpus, indent=1, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not CORPUS.exists() or CORPUS.read_text(encoding="utf-8") != text:
            print(f"{CORPUS} is stale: run scripts/flow_corpus.py", file=sys.stderr)
            return 1
        print(f"{CORPUS}: {len(corpus)} cases, up to date")
        return 0
    CORPUS.write_text(text, encoding="utf-8")
    print(f"wrote {CORPUS}: {len(corpus)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
