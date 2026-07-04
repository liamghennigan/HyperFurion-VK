"""Building the brain's prompt from context, and reading its reply.

The local-LLM path needs an explicit system prompt; the realtime voice
agent carries its own persona from the Voice Agent Builder, so it gets a
lean version. When the brain is allowed to act ([assistant] can_act), the
prompt teaches it the ACTION grammar — one command line, on its own line,
that the daemon TYPES but never runs.
"""

from __future__ import annotations

from voice_keyboard.assistant.models import ContextChunk

_BASE_TEMPLATE = (
    "You are {name}, Liam's private assistant (part of HyperFurion). Be "
    "conversational, concise, and useful. Never speak source citations, "
    "URLs, or file paths aloud unless explicitly asked; the client shows "
    "sources separately."
)

_ACTION_RULE = (
    "If — and only if — the user is asking you to DO something on their "
    "computer, you may propose exactly one shell command by ending your "
    "reply with a line of the form:\n"
    "ACTION: <the command>\n"
    "The command is TYPED at the user's terminal prompt and never run by "
    "you; the user reviews it and presses Enter. Prefer safe, read-only "
    "commands. Never propose an action unless clearly asked to act."
)

# The daemon parses this exact prefix out of the reply.
ACTION_PREFIX = "ACTION:"


def build_prompt(
    user_text: str,
    *,
    context: list[ContextChunk],
    privacy_mode: str = "local",
    web_enabled: bool = True,
    can_act: bool = False,
    include_persona: bool = True,
    name: str = "Kai",
) -> str:
    parts: list[str] = []
    if include_persona:
        parts.append(_BASE_TEMPLATE.format(name=name.strip() or "Kai"))
        if can_act:
            parts.append(_ACTION_RULE)

    memory = [c for c in context if c.kind in {"memory", "interaction"}]
    selection = [c for c in context if c.kind == "selection"]
    files = [c for c in context if c.kind == "file"]

    if memory:
        parts.append("Relevant memory (things you know about the user):")
        parts.extend(f"- {c.text}" for c in memory)
    if selection:
        parts.append("The user's current selection:")
        parts.extend(c.text for c in selection)
    if web_enabled:
        parts.append("Use your web/search capability when current information helps.")
    else:
        parts.append("Do not use web search for this answer.")
    if files:
        if privacy_mode == "cloud":
            parts.append("Read-only local files the user pointed at:")
            for index, chunk in enumerate(files, 1):
                parts.append(f"[Local {index}] {chunk.title} ({chunk.uri})\n{chunk.text}")
        else:
            parts.append(
                "The user mentioned local files, but privacy mode is local; "
                "do not infer contents from names. Ask them to switch to cloud "
                "mode to share file contents."
            )

    parts.append(f"User request:\n{user_text.strip()}")
    return "\n\n".join(parts)


def split_action(reply: str) -> tuple[str, str]:
    """Separate the spoken answer from a trailing ACTION line.

    Returns (spoken_text, command). command is "" when the reply proposes
    no action. Only the LAST line is considered, so answers that merely
    discuss the word "action" are unaffected."""
    text = reply.rstrip()
    lines = text.splitlines()
    if not lines:
        return text, ""
    last = lines[-1].strip()
    if last.upper().startswith(ACTION_PREFIX):
        command = last[len(ACTION_PREFIX):].strip().strip("`").strip()
        spoken = "\n".join(lines[:-1]).strip()
        return spoken, command
    return text, ""


def strip_visual_sources(text: str) -> str:
    for marker in ("\nSources:", "\nVISUAL SOURCES:", "\nVisual sources:"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip()
