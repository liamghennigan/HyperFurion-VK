"""The hard rule, fuzzed through the whole daemon: wherever a terminal
can't be ruled out (a known terminal, or focus that couldn't be
identified), no line break is ever typed while Enter is allowed, and no
key chord anywhere is Enter — whatever is said: line breaks, headings,
lists, multi-line snippets, model rewrites that come back multi-line,
per-app polish, caret commands."""

import asyncio
import os
import random
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon

SEEDS = range(int(os.environ.get("VK_SAFETY_SEEDS", "48")))  # widen locally to hunt
PIECES = [
    "hello world", "new line", "new paragraph", "new heading groceries", "new number milk",
    "new checkbox eggs", "new bullet tea", "vk sig", "vk make that formal", "see you monday period",
    "select previous word", "go to end of line", "press tab", "undo that", "paste that",
    "emoji rocket", "scratch that", "quote ship it unquote", "correct monday to friday",
]
ENTER = {"enter", "return", "kpenter"}


class GuardedInjector(RecordingInjector):
    def __init__(self):
        super().__init__()
        self.suppress_enter = False
        self.violations: list[str] = []
        self.risky = False

    def type_text(self, text: str) -> None:
        if self.risky and "\n" in text and not self.suppress_enter:
            self.violations.append(f"typed a line break with Enter allowed: {text!r}")
        super().type_text(text)

    def press_combo(self, names: list) -> None:
        lowered = {str(n).lower() for n in names}
        if lowered & ENTER or {"ctrl", "j"} <= lowered or {"ctrl", "m"} <= lowered:
            self.violations.append(f"pressed {names}")
        super().press_combo(names)


@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


def _session(seed: int) -> list[str]:
    rng = random.Random(seed)
    app = rng.choice(("kitty", "", "gedit"))
    said = " ".join(rng.choice(PIECES) for _ in range(rng.randint(1, 4)))
    injector = GuardedInjector()
    injector.risky = app != "gedit"
    events = [{"type": "transcript.partial", "text": said, "is_final": True}]
    daemon = _make_daemon(FakeStreamingSTT(events), injector, app=app)
    daemon._config["snippets"] = {"sig": "Best,\nLiam"}
    daemon._config["nav"]["enabled"] = rng.random() < 0.7
    if rng.random() < 0.5:
        daemon._config["polish"] = {"map": {app or "gedit": "casual"}}
    llm = mock.Mock()
    llm.rewrite = mock.Mock(return_value="Line one.\nLine two.")
    llm.clean_corrections = mock.Mock(side_effect=lambda text: text)

    async def run() -> None:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch, \
                mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm), \
                mock.patch("voice_keyboard.daemon.llm_ready", return_value=True):
            await daemon._start_recording()
            await asyncio.sleep(0.08)
            await daemon._stop_recording()

    asyncio.run(run())
    return [f"seed {seed} ({app or 'unknown'}: {said!r}): {v}" for v in injector.violations]


@pytest.mark.parametrize("chunk", range(4))
def test_no_enter_where_a_terminal_cannot_be_ruled_out(chunk: int) -> None:
    violations = [v for seed in SEEDS[chunk::4] for v in _session(seed)]
    assert not violations, "\n".join(violations)
