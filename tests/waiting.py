"""Waiting on asynchronous state in tests, by the clock.

Never count iterations of `await asyncio.sleep(small)`: asyncio treats any
timer due within the clock's resolution as already due, and Windows'
monotonic clock ticks every 15.6 ms — so "400 x sleep(0.005)" can finish in
a few milliseconds there, long before the state it waits for arrives.
"""

import asyncio
import time


async def wait_until(predicate, timeout: float = 5.0, step: float = 0.005) -> bool:
    """Poll `predicate` until it is true or `timeout` seconds have really
    passed (perf_counter: fine-grained everywhere). Returns its last value."""
    deadline = time.perf_counter() + timeout
    while True:
        if predicate():
            return True
        if time.perf_counter() >= deadline:
            return bool(predicate())
        await asyncio.sleep(step)
