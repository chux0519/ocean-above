"""One lock per conversation, shared by chat turns and the booking endpoint (design §4.6, B7).

In-process only: the API runs as a single uvicorn worker. The database does not rely on this lock
for B5/B7 (those hold through `BEGIN IMMEDIATE` and `tickets.quote_id UNIQUE`); the lock keeps a
booking click and a running chat turn of the same conversation from interleaving.

The lock is NOT reentrant. `Airline.book` takes it itself; everything else in `Airline` expects
the caller (the chat turn) to hold it.
"""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator


class ConversationBusy(Exception):
    """The conversation lock was not obtained within the timeout."""


class ConversationLocks:
    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._entries: dict[str, list] = {}  # conversation id -> [lock, holders + waiters]

    def _checkout(self, conversation_id: str) -> threading.Lock:
        with self._mutex:
            entry = self._entries.setdefault(conversation_id, [threading.Lock(), 0])
            entry[1] += 1
            return entry[0]

    def _checkin(self, conversation_id: str) -> None:
        with self._mutex:
            entry = self._entries[conversation_id]
            entry[1] -= 1
            if entry[1] == 0:
                del self._entries[conversation_id]

    @contextmanager
    def hold(self, conversation_id: str, timeout: float | None = None) -> Iterator[None]:
        """Blocking form, for sync code (threadpool endpoints). Never call it on the event loop:
        use `hold_async` there."""
        lock = self._checkout(conversation_id)
        try:
            if not lock.acquire(timeout=-1 if timeout is None else timeout):
                raise ConversationBusy(conversation_id)
            try:
                yield
            finally:
                lock.release()
        finally:
            self._checkin(conversation_id)

    @asynccontextmanager
    async def hold_async(self, conversation_id: str, timeout: float | None = None,
                         poll: float = 0.01) -> AsyncIterator[None]:
        """Async form of the same lock: waits without blocking the event loop, safe to cancel
        while waiting, and may be held across awaits (a whole streamed turn)."""
        lock = self._checkout(conversation_id)
        try:
            deadline = None if timeout is None else time.monotonic() + timeout
            while not lock.acquire(blocking=False):
                if deadline is not None and time.monotonic() >= deadline:
                    raise ConversationBusy(conversation_id)
                await asyncio.sleep(poll)
            try:
                yield
            finally:
                lock.release()
        finally:
            self._checkin(conversation_id)

    def locked(self, conversation_id: str) -> bool:
        with self._mutex:
            entry = self._entries.get(conversation_id)
            return entry is not None and entry[0].locked()
