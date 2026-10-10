"""The brain: realtime voice agent first, local LLM as the ground.

Model-agnostic, like every other channel. When [assistant] brain is
`realtime` (or `auto` with an agent + key configured) the xAI Voice Agent
Builder agent answers — spoken, memory-rich. Otherwise the daemon's local
LLM ([llm], e.g. a local Ollama) answers, fully offline. On a realtime
failure with brain=auto, it falls back to local rather than going silent.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Optional

from voice_keyboard import recall
from voice_keyboard.assistant.citations import make_citations
from voice_keyboard.assistant.context import ContextProvider
from voice_keyboard.assistant.locality import KaiState, kai_state
from voice_keyboard.assistant.memory import AssistantMemory
from voice_keyboard.assistant.models import ConverseResult
from voice_keyboard.assistant.prompting import (
    build_prompt,
    split_action,
    strip_visual_sources,
)
from voice_keyboard.assistant.realtime import create_realtime_client
from voice_keyboard.llm import create_llm_client

logger = logging.getLogger(__name__)

# What a brain reads: copied when it is built, so a turn keeps the settings
# Kai's verdict was made on even if the daemon's config is reloaded mid-turn.
SNAPSHOT_SECTIONS = ("assistant", "llm", "recall", "providers", "stt", "tts", "xai")


def _snapshot(config: dict) -> dict:
    return {name: copy.deepcopy(config.get(name, {})) for name in SNAPSHOT_SECTIONS}


class Brain:
    def __init__(
        self,
        *,
        config: dict,
        memory: AssistantMemory,
        context_provider: ContextProvider,
        state: Optional[KaiState] = None,
    ):
        self._config = _snapshot(config)
        # The verdict this brain was built under (see assistant/locality.py).
        self.state = state if state is not None else kai_state(self._config)
        self._memory = memory
        self._context = context_provider
        assistant_cfg = config.get("assistant", {})
        self._preference = str(assistant_cfg.get("brain", "auto")).strip().lower()
        self._can_act = bool(assistant_cfg.get("can_act", False))
        self._web_enabled = bool(assistant_cfg.get("web_enabled", True))
        self._privacy_mode = str(assistant_cfg.get("privacy_mode", "local")).strip().lower()
        self._max_memory = int(assistant_cfg.get("max_memory_results", 5))
        self._name = str(assistant_cfg.get("name", "Kai")).strip() or "Kai"
        self._memory_enabled = assistant_cfg.get("memory_enabled", True) is not False
        self._realtime = create_realtime_client(self._config)
        self._llm = create_llm_client(self._config)
        # Semantic history search only where the verdict allows it: a local
        # [recall], or Kai turned on with online services. Otherwise keywords.
        self._embedder = (
            recall.create_embedder(self._config) if self.state.uses_embeddings else None
        )

    @property
    def llm(self):
        """The [llm] client this brain answers with (None without [llm]);
        the daemon's terminal route uses it too, never a fresh one."""
        return self._llm

    def built_from(self, config: dict) -> bool:
        """True when `config` holds the settings this brain was built from."""
        return all(self._config.get(name) == config.get(name, {}) for name in SNAPSHOT_SECTIONS)

    def _gather_context(self, user_text: str):
        chunks = list(self._context.selection_chunk())
        if self._memory_enabled:
            # memory_enabled = false: no stored memories, no dictation history.
            chunks.extend(
                self._memory.relevant_chunks(
                    user_text, self._max_memory, embedder=self._embedder
                )
            )
        file_chunks, warnings = self._context.collect(user_text)
        chunks.extend(file_chunks)
        return chunks, warnings

    @property
    def has_voice_agent(self) -> bool:
        """True when a realtime voice agent can answer a spoken turn."""
        return self._realtime is not None and self._preference in {"realtime", "auto"}

    async def respond(self, user_text: str) -> ConverseResult:
        """Answer TYPED text. The realtime voice agent can't take text
        (it's voice-to-voice), so text always uses the local brain — the
        one brain that can answer typed questions."""
        chunks, warnings = self._gather_context(user_text)
        prompt = build_prompt(
            user_text,
            context=chunks,
            privacy_mode=self._privacy_mode,
            web_enabled=self._web_enabled,
            can_act=self._can_act,
            name=self._name,
        )
        if self._llm is None:
            raise RuntimeError(
                "typed conversation needs a local brain: configure [llm] "
                "(the realtime voice agent only answers spoken audio)"
            )
        reply = await asyncio.to_thread(self._llm.complete, prompt)
        return self._finish(reply, make_citations(chunks), audio=b"", which="local", warnings=warnings)

    async def respond_audio(
        self, pcm: bytes, *, sample_rate: int = 16000, transcript_hint: str = ""
    ) -> ConverseResult:
        """Answer a SPOKEN turn. Preferred path is the realtime voice
        agent (audio in → spoken audio + transcript out); on failure, or
        when no agent is configured, fall back to the local brain over the
        STT transcript so a conversation still happens."""
        if self.has_voice_agent:
            try:
                result = await self._realtime.ask_audio(pcm, sample_rate=sample_rate)
                # Nothing is logged here: the daemon records the turn once,
                # through remember_interaction(), which honours
                # [assistant] memory_enabled.
                # The voice agent's own audio is the answer; its transcript
                # is what it said. No action-drafting on this path (the
                # builder agent isn't taught the ACTION grammar).
                return ConverseResult(
                    text=result.transcript,
                    audio=result.audio,
                    brain="realtime",
                )
            except Exception as exc:
                if self._preference == "realtime" or self._llm is None:
                    raise
                logger.warning("Voice agent failed (%s); local brain on the transcript", exc)
        # Local fallback: answer the STT transcript with the local brain.
        if not transcript_hint.strip():
            raise RuntimeError("nothing heard, and no voice agent to answer audio")
        return await self.respond(transcript_hint)

    def _finish(self, reply, citations, *, audio, which, warnings) -> ConverseResult:
        reply = strip_visual_sources(reply)
        spoken, action = split_action(reply)
        if action and not self._can_act:
            action = ""
            spoken = spoken or reply
        return ConverseResult(
            text=spoken or reply,
            citations=citations,
            audio=audio,
            action=action,
            brain=which,
            warnings=warnings,
        )

    def remember_interaction(self, user_text: str, answer_text: str) -> None:
        """The one place a Kai turn is written to disk. With
        [assistant] memory_enabled = false nothing is stored."""
        if not self._memory_enabled:
            return
        try:
            self._memory.log_interaction(user_text, answer_text)
        except Exception:
            logger.debug("Could not log interaction", exc_info=True)

    def maybe_remember(self, user_text: str) -> bool:
        from voice_keyboard.assistant.memory import extract_memory_candidate

        if not self._memory_enabled:
            return False
        candidate = extract_memory_candidate(user_text)
        if not candidate:
            return False
        try:
            self._memory.remember(candidate, kind="preference", source="voice")
            return True
        except Exception:
            return False


def create_brain(config: dict) -> Optional["Brain"]:
    """Build the brain when Kai is on; None otherwise. Under the default
    ([assistant] enabled = "auto") Kai is on only when everything it uses
    runs on this computer (assistant/locality.py); true turns it on anyway,
    false keeps it off. While it is off nothing is built, so its memory is
    neither read nor written."""
    from pathlib import Path

    state = kai_state(config)
    if not state.on:
        return None
    snapshot = _snapshot(config)
    assistant_cfg = snapshot.get("assistant", {})
    home_root = Path(str(assistant_cfg.get("home_root", "")).strip() or Path.home())
    memory = AssistantMemory()
    context_provider = ContextProvider(
        home_root=home_root,
        privacy_mode=str(assistant_cfg.get("privacy_mode", "local")).strip().lower(),
    )
    return Brain(config=snapshot, memory=memory, context_provider=context_provider, state=state)
