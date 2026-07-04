import asyncio

import pytest

from voice_keyboard.flow.vad import SilenceGate, chunk_rms, vu_bar
from voice_keyboard.flow.worker import InjectionWorker, common_prefix_len


class FakeInjector:
    """Records injection ops and reconstructs the resulting screen text."""

    def __init__(self, fail_after: int = -1):
        self.ops: list[tuple[str, object]] = []
        self.screen = ""
        self.fail_after = fail_after

    def _maybe_fail(self) -> None:
        if self.fail_after == 0:
            raise RuntimeError("injector exploded")
        if self.fail_after > 0:
            self.fail_after -= 1

    def type_text(self, text: str) -> None:
        self._maybe_fail()
        self.ops.append(("type", text))
        self.screen += text

    def delete_chars(self, count: int) -> None:
        self._maybe_fail()
        self.ops.append(("delete", count))
        self.screen = self.screen[: len(self.screen) - count]


class TestInjectionWorker:
    def test_types_incrementally_and_converges(self) -> None:
        async def run() -> None:
            injector = FakeInjector()
            worker = InjectionWorker(injector, burst_chars=4)
            worker.start()
            worker.set_target("hello")
            await worker.drain(timeout=2.0)
            worker.set_target("hello world")
            screen = await worker.drain(timeout=2.0)
            assert screen == "hello world"
            assert injector.screen == "hello world"
            await worker.close()

        asyncio.run(run())

    def test_repairs_by_backspacing_to_divergence(self) -> None:
        async def run() -> None:
            injector = FakeInjector()
            worker = InjectionWorker(injector, burst_chars=64)
            worker.start()
            worker.set_target("hello whirled")
            await worker.drain(timeout=2.0)
            worker.set_target("hello world")
            screen = await worker.drain(timeout=2.0)
            assert screen == "hello world"
            assert injector.screen == "hello world"
            deletes = [op for op in injector.ops if op[0] == "delete"]
            assert deletes, "expected a repair delete"
            # Only the divergent tail is repaired, never the shared
            # prefix ("hello w" is common to both).
            assert sum(count for _, count in deletes) == len("hirled")

        asyncio.run(run())

    def test_intermediate_targets_coalesce(self) -> None:
        async def run() -> None:
            injector = FakeInjector()
            worker = InjectionWorker(injector, burst_chars=8)
            worker.start()
            for target in ("a", "ab", "abc", "abcd", "final text"):
                worker.set_target(target)
            screen = await worker.drain(timeout=2.0)
            assert screen == "final text"
            assert injector.screen == "final text"

        asyncio.run(run())

    def test_abandon_freezes_screen(self) -> None:
        async def run() -> None:
            injector = FakeInjector()
            worker = InjectionWorker(injector, burst_chars=64)
            worker.start()
            worker.set_target("keep this")
            await worker.drain(timeout=2.0)
            worker.abandon()
            worker.set_target("")  # would delete everything — must be ignored
            screen = await worker.drain(timeout=1.0)
            assert screen == "keep this"
            assert injector.screen == "keep this"
            await worker.close()

        asyncio.run(run())

    def test_injector_error_abandons_without_corruption(self) -> None:
        async def run() -> None:
            injector = FakeInjector(fail_after=1)
            worker = InjectionWorker(injector, burst_chars=4)
            worker.start()
            worker.set_target("hello world this is long")
            screen = await worker.drain(timeout=2.0)
            assert worker.abandoned
            assert screen == injector.screen

        asyncio.run(run())

    def test_common_prefix_len(self) -> None:
        assert common_prefix_len("hello", "help") == 3
        assert common_prefix_len("", "x") == 0
        assert common_prefix_len("same", "same") == 4


class TestVad:
    def test_chunk_rms_scales(self) -> None:
        silence = b"\x00\x00" * 160
        loud = b"\xff\x3f" * 160  # ~0.5 full scale
        assert chunk_rms(silence) == 0.0
        assert chunk_rms(loud) > 0.4

    def test_vu_bar_width(self) -> None:
        assert len(vu_bar([], width=5)) == 5
        assert len(vu_bar([0.1, 0.5], width=5)) == 5

    def test_silence_gate_fires_after_speech_then_silence(self) -> None:
        gate = SilenceGate(auto_stop_ms=600)
        fired = False
        for _ in range(5):  # 500ms of speech
            fired = gate.feed(0.2, 100) or fired
        assert not fired
        for _ in range(5):  # 500ms of silence: not yet
            fired = gate.feed(0.001, 100) or fired
        assert not fired
        assert gate.feed(0.001, 100)  # 600ms reached
        assert not gate.feed(0.001, 100)  # fires only once

    def test_silence_gate_needs_speech_first(self) -> None:
        gate = SilenceGate(auto_stop_ms=300)
        for _ in range(50):
            assert not gate.feed(0.001, 100)

    def test_disabled_gate_never_fires(self) -> None:
        gate = SilenceGate(auto_stop_ms=0)
        for _ in range(20):
            assert not gate.feed(0.2, 100)
            assert not gate.feed(0.0, 100)
