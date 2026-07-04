"""The assistant: the conversational MIND of HyperFurion.

The daemon is the hands — voice into keystrokes into any app, with the
consent gate wired into the injector. This package is the mind: a
memory-bearing conversational agent (xAI realtime voice agent, or a local
LLM) that answers, remembers, and — when allowed — DRAFTS actions the
human approves by pressing Enter. Same doctrine, one daemon: frontier
brain, local hands, the human owns the commit.
"""

from voice_keyboard.assistant.brain import Brain, ConverseResult, create_brain
from voice_keyboard.assistant.memory import AssistantMemory
from voice_keyboard.assistant.models import Citation, ContextChunk

__all__ = [
    "Brain",
    "ConverseResult",
    "create_brain",
    "AssistantMemory",
    "Citation",
    "ContextChunk",
]
