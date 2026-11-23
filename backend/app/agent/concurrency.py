"""Server-wide limits on running chat turns, and the bookkeeping of a turn's worker threads
(06 P0b, design §4.9).

`TurnSlots`: at most `max_active_turns` turns run at once, across all users and conversations, in
addition to the per-conversation lock. A turn that finds every slot taken waits up to
`queue_seconds` (first come, first served), then gives up with `ServerBusy`. Waiting is an
`await` on a future: the event loop is never blocked.

`Threads`: every blocking call a turn makes inside its time limit runs through `Threads.run`, so
the turn knows which worker threads are still running when the limit cuts it short. A Python
thread cannot be stopped from outside: a timeout around `asyncio.to_thread` only stops *waiting*
for it. The turn therefore waits (bounded by `drain_seconds`) for those threads before it stores
its reply and releases the conversation lock and its slot; the threads themselves are bounded by
app.db's busy and operation timeouts (`store/db.py`).
"""
from __future__ import annotations

import asyncio
import math
import os
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping

# name -> (environment variable, lowest, highest)
_BOUNDS: dict[str, tuple[str, float, float]] = {
    "max_active_turns": ("AGENT_MAX_ACTIVE_TURNS", 1, 256),
    "queue_seconds": ("AGENT_TURN_QUEUE_SECONDS", 0, 60),
}


class ServerBusy(Exception):
    """No turn slot became free within the queue time."""


@dataclass(frozen=True)
class TurnConcurrency:
    max_active_turns: int = 8  # turns running at once, whole server
    queue_seconds: float = 5.0  # how long a turn waits for a slot, then server_busy

    def __post_init__(self) -> None:
        n, wait = self.max_active_turns, self.queue_seconds
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"max_active_turns must be a positive integer, got {n!r}")
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not math.isfinite(wait) or wait < 0:
            raise ValueError(f"queue_seconds must be a number >= 0, got {wait!r}")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TurnConcurrency":
        """Defaults, overridden by `AGENT_MAX_ACTIVE_TURNS` (integer 1–256) and
        `AGENT_TURN_QUEUE_SECONDS` (number 0–60). A bad value raises ValueError (startup fails)."""
        env = os.environ if env is None else env
        values: dict[str, Any] = {}
        for name, (var, low, high) in _BOUNDS.items():
            raw = (env.get(var) or "").strip().replace("_", "")
            if not raw:
                continue
            whole = name == "max_active_turns"
            try:
                value = int(raw) if whole else float(raw)
            except ValueError:
                raise ValueError(f"{var} must be {'an integer' if whole else 'a number'}, got {raw!r}") from None
            if not low <= value <= high:  # also catches nan
                raise ValueError(f"{var} must be between {low:g} and {high:g}, got {raw!r}")
            values[name] = value
        return cls(**values)


class TurnSlots:
    """A FIFO counting semaphore for chat turns. Lives on one event loop at a time; not
    thread-safe (only the event loop touches it). Unlike `asyncio.Semaphore` it is not bound to
    the loop it first waited on (tests run turns of one agent under several `asyncio.run`)."""

    def __init__(self, config: TurnConcurrency):
        self.config = config
        self.active = 0  # slots taken
        self._waiters: deque[asyncio.Future] = deque()

    @property
    def waiting(self) -> int:
        return sum(not f.done() for f in self._waiters)

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        """Take a slot for the body, waiting up to `queue_seconds`; else raise ServerBusy."""
        await self._acquire()
        try:
            yield
        finally:
            self._release()

    async def _acquire(self) -> None:
        if self.active < self.config.max_active_turns and not self._waiters:
            self.active += 1
            return
        if self.config.queue_seconds <= 0:
            raise ServerBusy()
        handover = asyncio.get_running_loop().create_future()
        self._waiters.append(handover)
        try:
            async with asyncio.timeout(self.config.queue_seconds):
                await handover
        except BaseException as exc:
            if handover.done() and not handover.cancelled():
                # the slot was handed over in the same instant as the timeout / cancellation
                if isinstance(exc, TimeoutError):
                    return  # keep it: the turn may run
                self._release()
                raise
            handover.cancel()
            try:
                self._waiters.remove(handover)
            except ValueError:
                pass
            if isinstance(exc, TimeoutError):
                raise ServerBusy() from None
            raise
        # handed over by `_release`: `active` already counts this turn

    def _release(self) -> None:
        while self._waiters:
            handover = self._waiters.popleft()
            if not handover.done():
                handover.set_result(None)  # the slot passes to the oldest waiter
                return
        self.active -= 1


class Threads:
    """The worker threads one turn started and may not have finished."""

    def __init__(self) -> None:
        self._running: set[asyncio.Future] = set()

    @property
    def running(self) -> int:
        return sum(not f.done() for f in self._running)

    async def run(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """`await asyncio.to_thread(fn, ...)`, but if the caller is cancelled (the turn's time
        limit), the thread is still tracked until it ends, so `drain` can wait for it."""
        future = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
        self._running.add(future)
        future.add_done_callback(self._finished)
        return await asyncio.shield(future)

    def _finished(self, future: asyncio.Future) -> None:
        self._running.discard(future)
        if not future.cancelled():
            future.exception()  # retrieved: a thread that failed after its caller left is not "never retrieved"

    async def drain(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for the tracked threads. True when none is left running."""
        pending = [f for f in self._running if not f.done()]
        if not pending:
            return True
        done, still = await asyncio.wait(pending, timeout=timeout)
        return not still
