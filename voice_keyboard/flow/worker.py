"""The injection worker: converge the screen toward the desired text.

One asyncio task owns the injector during a live session. There is no op
queue — only the latest desired string. Each burst re-reads it, diffs it
against what has actually been typed, and either backspaces or types up to
`burst_chars`. Stale intermediate targets coalesce away for free, and a
tail revised mid-burst is caught at the next burst boundary.

The engine guarantees repairs stay short (they never cross committed
text), so the worker needs no policy — just the diff-and-converge loop.

On any injector error the worker abandons: nothing further is typed or
deleted, and whatever is on screen is treated as final. The daemon applies
the same rule on STT errors by calling `abandon()` — never backspace on an
error path.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


def common_prefix_len(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    index = 0
    while index < limit and a[index] == b[index]:
        index += 1
    return index


class InjectionWorker:
    def __init__(self, injector, *, burst_chars: int = 32):
        self._injector = injector
        self._burst = max(1, burst_chars)
        self.screen = ""
        self._desired = ""
        self._dirty = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._abandoned = False
        self._task: asyncio.Task | None = None

    @property
    def abandoned(self) -> bool:
        return self._abandoned

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="flow-injection")

    def set_target(self, text: str) -> None:
        if self._abandoned or text == self._desired:
            return
        self._desired = text
        self._idle.clear()
        self._dirty.set()

    def abandon(self) -> None:
        """Freeze the screen as-is: no further typing or deleting."""
        self._abandoned = True
        self._dirty.set()
        self._idle.set()

    async def drain(self, timeout: float) -> str:
        """Wait until the screen has converged (or the worker abandoned or
        the timeout passed); returns what is actually typed."""
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("flow: injection did not converge within %.1fs", timeout)
        return self.screen

    async def close(self) -> None:
        self._abandoned = True
        self._dirty.set()
        self._idle.set()
        if self._task is not None:
            task, self._task = self._task, None
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        try:
            while True:
                await self._dirty.wait()
                self._dirty.clear()
                if self._abandoned:
                    break
                while not self._abandoned:
                    target = self._desired  # always converge to the latest
                    if self.screen == target:
                        break
                    prefix = common_prefix_len(self.screen, target)
                    if len(self.screen) > prefix:
                        count = min(len(self.screen) - prefix, self._burst)
                        await asyncio.to_thread(self._injector.delete_chars, count)
                        self.screen = self.screen[:len(self.screen) - count]
                    else:
                        chunk = target[len(self.screen):len(self.screen) + self._burst]
                        await asyncio.to_thread(self._injector.type_text, chunk)
                        self.screen += chunk
                if not self._dirty.is_set():
                    self._idle.set()
                if self._abandoned:
                    break
        except Exception:
            logger.exception("flow: injector failed; freezing screen as-is")
            self._abandoned = True
        finally:
            self._idle.set()
