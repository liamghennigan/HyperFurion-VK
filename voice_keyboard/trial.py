"""`voice-keyboard try [register:] <words…>`: what the keyboard would type.

A `|` is a pause between utterances: commands said on their own ("correct
monday to friday", "select that", "cap that") need one before them.

The words go through the same grammar and engine the daemon builds — your
[flow] vocabulary, commands, punctuation, language, numbers and
formatters — as one utterance, without a microphone or a daemon. Handy
for testing a command, a snippet name, or a config change.
"""

from voice_keyboard.flow.engine import FlowConfig, FlowEngine
from voice_keyboard.flow.grammar import DEFAULT_FILLERS, Grammar
from voice_keyboard.flow.registers import REGISTERS


def grammar_for(config: dict, register) -> Grammar:
    flow = config.get("flow", {})
    return Grammar(
        enabled=bool(flow.get("grammar", True)) and register.grammar_enabled,
        commands=flow.get("commands") or {},
        punctuation=flow.get("punctuation") or {},
        vocabulary=flow.get("vocabulary") or {},
        wake_word=str(flow.get("wake_word", "vk")),
        numbers=str(flow.get("numbers", "auto")).lower(),
        numbers_on=register.numbers_on,
        numbers_min=register.numbers_min,
        spelling=bool(flow.get("spelling", True)),
        fillers=flow.get("fillers", DEFAULT_FILLERS),
        addresses=bool(flow.get("addresses", True)),
        formatters=str(flow.get("formatters", "code")).lower(),
        code=bool(register.compiler) or register.terminal,
        nav=bool(config.get("nav", {}).get("enabled", False)),
        language=str(flow.get("language", "en")).lower(),
    )


def run(config: dict, words: list[str]) -> str:
    """The typed result, with any caret command or wake-word instruction
    the utterance carries noted after it."""
    register = REGISTERS["prose"]
    if words and words[0].endswith(":") and words[0][:-1].lower() in REGISTERS:
        register = REGISTERS[words[0][:-1].lower()]
        words = words[1:]
    utterances = [u.strip() for u in " ".join(words).split("|") if u.strip()]
    if not utterances:
        raise ValueError("say something: voice-keyboard try [register:] <words…> [| <words…>]")
    engine = FlowEngine(FlowConfig(), grammar_for(config, register), register)
    notes = []
    said, now = "", 0.0
    for utterance in utterances:
        said = (said + " " + utterance).strip()
        now += 1.0
        engine.on_transcript(said, is_final=True, now=now)
        while engine.pending_action() is not None:
            action = engine.pending_action()
            notes.append(f"[keys: {action.action.replace(':', ' ')}"
                         + (f" ×{action.count}" if action.count > 1 else "") + "]")
            engine.complete_action(now, pressed=True)
    result = engine.finalize(said, now=now + 1.0)
    while result.action is not None:
        notes.append(f"[keys: {result.action.action.replace(':', ' ')}"
                     + (f" ×{result.action.count}" if result.action.count > 1 else "") + "]")
        result = engine.complete_action(1.0, pressed=True)
    text = (result.typed_before + result.text) if result.typed_before else result.text
    if result.instruction:
        notes.append(f"[instruction: {result.instruction}]")
    if result.scratches:
        notes.append(f"[scratched {result.scratches}]")
    return text + ("\n" + " ".join(notes) if notes else "")
