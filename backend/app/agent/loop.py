"""The agent loop (design §4): one chat turn as a stream of contract SSE events.

    user message stored -> [LLM -> tools -> cards]* -> guarded text -> assistant message stored

The LLM chooses tools and writes text; routes, prices and tickets come from the airline through
the four tools. Text is released sentence by sentence after the ReplyGuard traced every number
and city in it; the first problem replaces the whole reply with a code template. The turn's
quote cards become bookable only when `Airline.post_assistant_message` stores them (`done`).

Nothing here can book: the loop talks to the airline through `Toolbox` (four tools, no booking)
and `post_assistant_message`. Booking is the HTTP endpoint's call, caused by the user's click.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from ..airline import ConversationBusy, NotFound
from .context import build_context
from .guard import CityIndex, ReplyGuard, SentenceBuffer
from .config import EFFORTS
from .llm import LLM, Completion, LLMError, TextDelta
from .prompt import system_prompt
from .templates import CLICK_TO_BOOK, fallback_reply
from .tools import DEFAULT_STATUS, STATUS_TEXT, TOOL_SCHEMAS, Outcome, Toolbox

log = logging.getLogger(__name__)

MAX_STEPS = 6
TURN_TIMEOUT = 90.0  # a whole turn (all LLM calls, tools, retries); then the template reply
LOCK_TIMEOUT = 10.0  # waiting for another turn of the same conversation; then conversation_busy
MAX_USER_CHARS = 2000
TITLE_CHARS = 60
LLM_UNAVAILABLE = "The assistant is temporarily unavailable. Your message was saved; please try again."
BUSY = "This conversation is still working on your previous message. Please try again in a moment."
INTERNAL = "Something went wrong. Your message was saved; please try again."


@dataclass(frozen=True)
class Event:
    """One SSE event of docs/api/contract.md: `name` and its JSON `data`."""

    name: str
    data: dict


class _GuardFailed(Exception):
    def __init__(self, problems: list[dict]):
        super().__init__("reply guard")
        self.problems = problems


@dataclass
class _Turn:
    user_id: str
    conversation_id: str
    user_message: dict
    guard: ReplyGuard
    emit: Callable[[str, dict], None]
    reasoning_effort: str | None = None  # per-request thinking level; None = the client's default
    text: str = ""  # released so far
    cards: list[dict] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)


def message_view(message: dict) -> dict:
    """Contract `Message` of a stored row without quote cards (user messages)."""
    return {"id": message["id"], "role": message["role"], "text": message["text"],
            "cards": list(message["cards"]), "created_at": message["created_at"]}


class Agent:
    def __init__(self, airline: Any, llm: LLM, *, max_steps: int = MAX_STEPS,
                 ping_interval: float = 15.0, retries: int = 2, backoff: float = 0.5,
                 llm_timeout: float = 60.0, turn_timeout: float = TURN_TIMEOUT,
                 lock_timeout: float | None = LOCK_TIMEOUT,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.llm = llm
        self.store = airline.store
        self.max_steps = max_steps
        self.ping_interval = ping_interval
        self.retries = retries
        self.backoff = backoff
        self.llm_timeout = llm_timeout
        self.turn_timeout = turn_timeout
        self.lock_timeout = lock_timeout
        self._sleep = sleep
        self._tools = Toolbox(airline)
        # The only other things the loop takes from the airline. No reference to the Airline
        # object itself is kept.
        self._post_assistant_message = airline.post_assistant_message
        self._locks = airline.locks
        self._networks = airline.networks
        self._index: tuple[str, CityIndex] | None = None
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ public -------------
    def stream_turn(self, user_id: str, conversation_id: str, text: str, *,
                    reasoning_effort: str | None = None) -> AsyncIterator[Event]:
        """Start a turn and return its events: `user_message`, then `status` / `card` / `delta` /
        `replace` / `ping`, ending with exactly one `done` or `error`.

        `reasoning_effort` overrides the LLM's thinking level for this turn (none / low / high /
        max; None = configured default).

        Raises before anything is stored: NotFound (not the user's conversation), ValueError
        (empty or too long text, unknown reasoning_effort). Must be called on the event loop. The turn runs in its own
        task: it finishes and is stored even when the client stops reading."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must not be empty")
        text = text.strip()
        if len(text) > MAX_USER_CHARS:
            raise ValueError(f"text is longer than {MAX_USER_CHARS} characters")
        if reasoning_effort is not None and reasoning_effort not in EFFORTS:
            raise ValueError(f"reasoning_effort must be one of {', '.join(EFFORTS)}")
        if self.store.get_conversation(user_id, conversation_id) is None:
            raise NotFound(conversation_id)
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        task = asyncio.get_running_loop().create_task(
            self._run(user_id, conversation_id, text, reasoning_effort, queue))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self._events(queue)

    async def wait_idle(self) -> None:
        """Wait for every running turn (shutdown, tests)."""
        while pending := [t for t in self._tasks if not t.done()]:
            await asyncio.wait(pending)

    async def _events(self, queue: asyncio.Queue) -> AsyncIterator[Event]:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), self.ping_interval)
            except asyncio.TimeoutError:
                yield Event("ping", {})
                continue
            if event is None:
                return
            yield event

    # ------------------------------------------------------------------ one turn -----------
    async def _run(self, user_id: str, conversation_id: str, text: str, reasoning_effort: str | None,
                   queue: asyncio.Queue) -> None:
        def emit(name: str, data: dict) -> None:
            queue.put_nowait(Event(name, data))

        try:
            async with self._locks.hold_async(conversation_id, timeout=self.lock_timeout):
                await self._turn(user_id, conversation_id, text, reasoning_effort, emit)
        except ConversationBusy:
            emit("error", {"code": "conversation_busy", "message": BUSY})
        except Exception:
            log.exception("chat turn failed (conversation %s)", conversation_id)
            emit("error", {"code": "internal_error", "message": INTERNAL})
        finally:
            queue.put_nowait(None)

    async def _turn(self, user_id: str, conversation_id: str, text: str, reasoning_effort: str | None,
                    emit: Callable[[str, dict], None]) -> None:
        store = self.store
        conversation = await asyncio.to_thread(store.get_conversation, user_id, conversation_id)
        user_message = await asyncio.to_thread(store.add_message, user_id, conversation_id, "user", text)
        emit("user_message", message_view(user_message))
        if conversation is not None and not conversation["title"]:
            await asyncio.to_thread(store.set_conversation_title, user_id, conversation_id,
                                    " ".join(text.split())[:TITLE_CHARS])

        context = await asyncio.to_thread(build_context, store, user_id, conversation_id)
        index = await asyncio.to_thread(self._city_index)
        today = store.clock().date()
        guard = ReplyGuard(index, records=context.records, user_text=text,
                           earlier_user_texts=context.user_texts[:-1], today=today)
        turn = _Turn(user_id, conversation_id, user_message, guard, emit, reasoning_effort)
        messages = [{"role": "system", "content": system_prompt(today)}, *context.messages]

        try:
            async with asyncio.timeout(self.turn_timeout):
                fallback = await self._steps(turn, messages)
        except TimeoutError:
            fallback = [{"kind": "turn_timeout", "value": str(self.turn_timeout)}]
        except LLMError as exc:
            fallback = [{"kind": "llm_error", "value": str(exc)[:200]}]
        except Exception as exc:  # a bug or a store error in the middle of the turn
            if not turn.outcomes and not turn.text:
                raise
            log.exception("chat turn failed after producing output (conversation %s)", conversation_id)
            fallback = [{"kind": "internal_error", "value": type(exc).__name__}]
        if fallback is not None and fallback[0]["kind"] in ("llm_error", "turn_timeout") \
                and not turn.outcomes and not turn.text:
            emit("error", {"code": "llm_unavailable", "message": LLM_UNAVAILABLE})
            return
        await self._finish(turn, fallback)

    async def _steps(self, turn: _Turn, messages: list[dict]) -> list[dict] | None:
        """The LLM / tool steps of a turn. Returns None when the LLM's text can stay, otherwise
        the problems that make the template reply replace it."""
        for _ in range(self.max_steps):
            try:
                completion = await self._llm_step(turn, messages)
            except _GuardFailed as failed:
                return failed.problems
            if not completion.tool_calls:
                if completion.finish_reason != "stop":  # "length", "content_filter", ...
                    return [{"kind": "truncated", "value": str(completion.finish_reason)}]
                if not turn.text.strip():
                    return [{"kind": "empty_reply", "value": ""}]
                return None
            messages.append(completion.assistant_message())
            for call in completion.tool_calls:
                turn.emit("status", {"text": STATUS_TEXT.get(call["name"], DEFAULT_STATUS)})
                outcome = await asyncio.to_thread(self._tools.run, turn.user_id, turn.conversation_id,
                                                  call["name"], call["arguments"])
                await asyncio.to_thread(
                    self.store.add_tool_call, turn.user_id, turn.user_message["id"], outcome.name,
                    outcome.args, outcome.payload, error=outcome.error, latency_ms=outcome.latency_ms)
                turn.outcomes.append(outcome)
                turn.guard.add_record(outcome.payload)
                for card in outcome.cards:
                    turn.cards.append(card)
                    turn.emit("card", {"card": card})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": outcome.for_llm()})
        return [{"kind": "max_steps", "value": str(self.max_steps)}]

    async def _finish(self, turn: _Turn, fallback: list[dict] | None = None) -> None:
        """Persist the assistant message (the `done` moment: quote cards become bookable)."""
        text = turn.text.rstrip()
        if fallback is not None:
            text = fallback_reply(turn.outcomes)
            turn.emit("replace", {"text": text})
        elif CLICK_TO_BOOK not in text and any(
                o.name == "request_booking" and o.code == "ok" for o in turn.outcomes):
            # fixed by code, whatever the LLM wrote: the click is what books
            piece = ("\n\n" if text else "") + CLICK_TO_BOOK
            turn.emit("delta", {"text": piece})
            text += piece
        message = await asyncio.to_thread(
            self._post_assistant_message, turn.user_id, turn.conversation_id, text, turn.cards, fallback)
        turn.emit("done", {"message": message})

    # ------------------------------------------------------------------ LLM step -----------
    def _release(self, turn: _Turn, piece: str, first_of_step: bool) -> None:
        """Check the reply so far plus `piece`; send `piece` when nothing is wrong. Checking the
        whole text catches a number or a city name that spans two pieces (`St. | Louis`)."""
        if not turn.text:
            piece = piece.lstrip()
        elif first_of_step and not turn.text[-1].isspace() and not piece[:1].isspace():
            piece = "\n\n" + piece  # text of a later step starts a new paragraph
        problems = turn.guard.check(turn.text + piece)
        if problems:
            raise _GuardFailed(problems)
        turn.text += piece
        turn.emit("delta", {"text": piece})

    async def _llm_step(self, turn: _Turn, messages: list[dict]) -> Completion:
        """One LLM call with retries. Text is released as it streams. A failed call is retried
        only while none of its text has been released."""
        request = {"turn": turn.user_message["id"], "messages": list(messages),
                   "tools": [t["function"]["name"] for t in TOOL_SCHEMAS],
                   "reasoning_effort": turn.reasoning_effort or getattr(self.llm, "reasoning_effort", None)}
        for attempt in range(self.retries + 1):
            started = time.monotonic()
            buffer = SentenceBuffer()
            completion: Completion | None = None
            released = False
            content = blank = ""

            def log_call(response: dict, tokens: int | None = None) -> Awaitable[Any]:
                return asyncio.to_thread(
                    self.store.add_llm_call, turn.user_id, turn.conversation_id, request, response,
                    latency_ms=int((time.monotonic() - started) * 1000), tokens=tokens,
                    model=getattr(self.llm, "model", None))

            try:
                # aclosing: a guard failure or a timeout must close the HTTP stream right away
                async with asyncio.timeout(self.llm_timeout), \
                        aclosing(self.llm.stream(
                            messages, TOOL_SCHEMAS, reasoning_effort=turn.reasoning_effort)) as stream:
                    async for event in stream:
                        if isinstance(event, TextDelta):
                            content += event.text
                            for sentence in buffer.push(event.text):
                                if sentence.strip():
                                    self._release(turn, blank + sentence, not released)
                                    blank, released = "", True
                                else:
                                    blank += sentence  # white space is never sent on its own
                        else:
                            completion = event
                if completion is None:
                    raise LLMError("LLM stream ended without a completion", retryable=True)
                rest = buffer.flush()
                if rest.strip():
                    self._release(turn, blank + rest, not released)
            except _GuardFailed as failed:
                await log_call({"content": content, "guard_problems": failed.problems, "aborted": True})
                raise
            except (LLMError, TimeoutError) as exc:
                error = exc if isinstance(exc, LLMError) else LLMError("LLM call timed out", retryable=True)
                await log_call({"content": content, "error": str(error)[:300], "attempt": attempt + 1})
                if released or not error.retryable or attempt == self.retries:
                    raise error from None
                await self._sleep(self.backoff * 2 ** attempt)
                continue
            await log_call(completion.as_record(), completion.total_tokens)
            return completion
        raise LLMError("unreachable", retryable=False)  # pragma: no cover

    def _city_index(self) -> CityIndex:
        """City names for the guard, from the airline's current network (rebuilt when
        locations.db changes)."""
        net = self._networks.current()
        if self._index is None or self._index[0] != net.fingerprint.id:
            self._index = (net.fingerprint.id, CityIndex(net.cities))
        return self._index[1]
