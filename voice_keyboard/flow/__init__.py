"""Flow — molten dictation.

Pure-logic building blocks (grammar, registers, numbers, vad, engine) plus
the one IO-adjacent piece, the injection worker. The engine turns a stream
of speech-to-text updates into a single "desired text" string; the worker
converges what is actually typed on screen toward that string.
"""

from voice_keyboard.flow.engine import FlowConfig, FlowEngine, FinalResult
from voice_keyboard.flow.grammar import Grammar
from voice_keyboard.flow.registers import Register, resolve_register
from voice_keyboard.flow.worker import InjectionWorker

__all__ = [
    "FlowConfig",
    "FlowEngine",
    "FinalResult",
    "Grammar",
    "InjectionWorker",
    "Register",
    "resolve_register",
]
