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
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator

from ..airline import ConversationBusy, NotFound
from .budget import AgentBudget, BudgetTracker, refusal
from .concurrency import ServerBusy, Threads, TurnConcurrency, TurnSlots
from .context import build_context
from .guard import CityIndex, ReplyGuard, SentenceBuffer
from .config import EFFORTS
from .llm import LLM, Completion, LLMError, TextDelta
from .prompt import system_prompt
from .templates import CLICK_TO_BOOK, fallback_reply
from .tools import DEFAULT_STATUS, STATUS_TEXT, TOOL_SCHEMAS, MAX_RAW_ARGUMENTS, Outcome, Toolbox

log = logging.getLogger(__name__)

DEFAULT_BUDGET = AgentBudget()
MAX_STEPS = DEFAULT_BUDGET.max_steps
TURN_TIMEOUT = DEFAULT_BUDGET.max_turn_seconds  # a whole turn (all LLM calls, tools, retries); then the template reply
LOCK_TIMEOUT = 10.0  # waiting for another turn of the same conversation; then conversation_busy
DRAIN_TIMEOUT = 30.0  # after the turn's time limit: waiting for its worker threads (design §4.9)
MAX_USER_CHARS = 2000
TITLE_CHARS = 60
LLM_UNAVAILABLE = "The assistant is temporarily unavailable. Your message was saved; please try again."
BUSY = "This conversation is still working on your previous message. Please try again in a moment."
SERVER_BUSY = "The assistant is busy right now. Your message was not sent; please try again in a moment."
INTERNAL = "Something went wrong. Your message was saved; please try again."
NOT_STORED = "Something went wrong and your message was not saved. Please try again in a moment."
USER_BUSY = ("Your other conversations are still working on earlier messages. "
             "Please wait for a reply there, then try again.")
FLOOD_FACTOR = 4  # a completion with more than max_tool_calls_per_step * this many calls runs none


@dataclass(frozen=True)
class Event:
    """One SSE event of docs/api/contract.md: `name` and its JSON `data`."""

    name: str
    data: dict


class _Stopped(Exception):
    """The step ends the turn with the template reply for `problems`."""

    def __init__(self, problems: list[dict]):
        super().__init__(problems[0]["kind"])
        self.problems = problems


class _GuardFailed(_Stopped):
    """The ReplyGuard refused the text."""


class _UserBusy(Exception):
    """The user already has `max_turns_per_user` turns running."""


class _NotStored(Exception):
    """Storing the user's message failed: nothing of the turn exists."""


@dataclass
class _Turn:
    user_id: str
    conversation_id: str
    user_message: dict
    guard: ReplyGuard
    emit: Callable[[str, dict], None]
    budget: BudgetTracker = field(default_factory=lambda: BudgetTracker(DEFAULT_BUDGET))
    reasoning_effort: str | None = None  # per-request thinking level; None = the client's default
    text: str = ""  # released so far
    cards: list[dict] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    threads: Threads = field(default_factory=Threads)  # blocking calls inside the time limit


def message_view(message: dict) -> dict:
    """Contract `Message` of a stored row without quote cards (user messages)."""
    return {"id": message["id"], "role": message["role"], "text": message["text"],
            "cards": list(message["cards"]), "created_at": message["created_at"]}


class Agent:
    def __init__(self, airline: Any, llm: LLM, *, budget: AgentBudget | None = None,
                 max_steps: int | None = None,
                 ping_interval: float = 15.0, retries: int = 2, backoff: float = 0.5,
                 llm_timeout: float = 60.0, turn_timeout: float | None = None,
                 lock_timeout: float | None = LOCK_TIMEOUT,
                 concurrency: TurnConcurrency | None = None, drain_timeout: float = DRAIN_TIMEOUT,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        """`budget`: the per-turn limits (design §4.8; default `AgentBudget()`). `max_steps` and
        `turn_timeout` override its fields (tests, older callers). `concurrency`: the server-wide
        limit on running turns (design §4.9; default `TurnConcurrency()`). `drain_timeout`: how
        long a cut-short turn waits for its worker threads before it ends anyway."""
        budget = budget or DEFAULT_BUDGET
        if max_steps is not None:
            budget = replace(budget, max_steps=max_steps)
        if turn_timeout is not None:
            budget = replace(budget, max_turn_seconds=turn_timeout)
        self.budget = budget
        self.llm = llm
        self.store = airline.store
        self.ping_interval = ping_interval
        self.retries = retries
        self.backoff = backoff
        self.llm_timeout = llm_timeout
        self.lock_timeout = lock_timeout
        self.slots = TurnSlots(concurrency or TurnConcurrency())
        self.drain_timeout = drain_timeout
        self._sleep = sleep
        self._tools = Toolbox(airline)
        # The only other things the loop takes from the airline. No reference to the Airline
        # object itself is kept.
        self._post_assistant_message = airline.post_assistant_message
        self._locks = airline.locks
        self._networks = airline.networks
        self._index: tuple[str, CityIndex] | None = None
        self._tasks: set[asyncio.Task] = set()
        self._user_turns: dict[str, int] = {}  # user id -> running turns (event loop only)

    @property
    def max_steps(self) -> int:
        return self.budget.max_steps

    @property
    def turn_timeout(self) -> float:
        return self.budget.max_turn_seconds

    # ------------------------------------------------------------------ public -------------
    def stream_turn(self, user_id: str, conversation_id: str, text: str, *,
                    reasoning_effort: str | None = None,
                    on_not_stored: Callable[[], Any] | None = None,
                    on_server_busy: Callable[[], Any] | None = None,
                    owner_checked: bool = False) -> AsyncIterator[Event]:
        """Start a turn and return its events: `user_message`, then `status` / `card` / `delta` /
        `replace` / `ping`, ending with exactly one `done` or `error`.

        `reasoning_effort` overrides the LLM's thinking level for this turn (none / low / high /
        max; None = configured default). `on_not_stored`: a blocking callable run (in a worker
        thread, before the event is sent) when the turn ends before the user's message was
        stored: refused with `server_busy`, or app.db failed to store the message
        (`internal_error`) - the API gives the day's message slot back there. It runs even when
        nobody reads the stream. `on_server_busy` is its older name.
        `owner_checked`: the caller already checked (off the event loop) that the conversation is
        the user's, so this method reads nothing from app.db (06 review P2-9); the turn's own
        writes still check ownership.

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
        if not owner_checked and self.store.get_conversation(user_id, conversation_id) is None:
            raise NotFound(conversation_id)
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        task = asyncio.get_running_loop().create_task(
            self._run(user_id, conversation_id, text, reasoning_effort, queue, on_not_stored or on_server_busy))
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
                   queue: asyncio.Queue, on_not_stored: Callable[[], Any] | None = None) -> None:
        def emit(name: str, data: dict) -> None:
            queue.put_nowait(Event(name, data))

        async def not_stored() -> None:
            if on_not_stored is not None:
                try:
                    await asyncio.to_thread(on_not_stored)
                except Exception:
                    log.exception("not-stored callback failed (conversation %s)", conversation_id)

        # The conversation lock first, then the user's own limit, then a server-wide slot: a
        # second message of a busy conversation waits without taking a slot. Every refusal comes
        # before anything is stored.
        try:
            async with self._locks.hold_async(conversation_id, timeout=self.lock_timeout):
                with self._user_turn(user_id):
                    async with self.slots.hold():
                        await self._turn(user_id, conversation_id, text, reasoning_effort, emit)
        except ConversationBusy:
            emit("error", {"code": "conversation_busy", "message": BUSY})
        except _UserBusy:
            log.warning("chat turn refused: user already has %d turns running (conversation %s)",
                        self.slots.config.max_turns_per_user, conversation_id)
            emit("error", {"code": "conversation_busy", "message": USER_BUSY})
        except ServerBusy:
            log.warning("chat turn refused: %d turns running, none ended within %gs (conversation %s)",
                        self.slots.active, self.slots.config.queue_seconds, conversation_id)
            await not_stored()
            emit("error", {"code": "server_busy", "message": SERVER_BUSY})
        except _NotStored:
            log.exception("chat turn: the user's message could not be stored (conversation %s)", conversation_id)
            await not_stored()
            emit("error", {"code": "internal_error", "message": NOT_STORED})
        except Exception:
            log.exception("chat turn failed (conversation %s)", conversation_id)
            emit("error", {"code": "internal_error", "message": INTERNAL})
        finally:
            queue.put_nowait(None)

    @contextmanager
    def _user_turn(self, user_id: str) -> Iterator[None]:
        """At most `max_turns_per_user` running turns per user (06 review P2-8). Event loop only."""
        if self._user_turns.get(user_id, 0) >= self.slots.config.max_turns_per_user:
            raise _UserBusy()
        self._user_turns[user_id] = self._user_turns.get(user_id, 0) + 1
        try:
            yield
        finally:
            self._user_turns[user_id] -= 1
            if not self._user_turns[user_id]:
                del self._user_turns[user_id]

    async def _turn(self, user_id: str, conversation_id: str, text: str, reasoning_effort: str | None,
                    emit: Callable[[str, dict], None]) -> None:
        store = self.store
        conversation = await asyncio.to_thread(store.get_conversation, user_id, conversation_id)
        try:
            user_message = await asyncio.to_thread(store.add_message, user_id, conversation_id, "user", text)
        except Exception as exc:  # app.db timeout, the conversation gone: nothing of the turn exists
            raise _NotStored() from exc
        emit("user_message", message_view(user_message))
        if conversation is not None and not conversation["title"]:
            await asyncio.to_thread(store.set_conversation_title, user_id, conversation_id,
                                    " ".join(text.split())[:TITLE_CHARS])

        context = await asyncio.to_thread(build_context, store, user_id, conversation_id)
        index = await asyncio.to_thread(self._city_index)
        today = store.clock().date()
        guard = ReplyGuard(index, records=context.records, user_text=text,
                           earlier_user_texts=context.user_texts[:-1], today=today)
        turn = _Turn(user_id, conversation_id, user_message, guard, emit, BudgetTracker(self.budget),
                     reasoning_effort)
        messages = [{"role": "system", "content": system_prompt(today)}, *context.messages]

        try:
            try:
                async with asyncio.timeout(self.turn_timeout):
                    fallback = await self._steps(turn, messages)
            finally:
                await self._drain(turn)
        except TimeoutError:
            fallback = [{"kind": "turn_timeout", "value": str(self.turn_timeout)}]
        except LLMError as exc:
            fallback = [{"kind": "llm_error", "value": str(exc)[:200]}]
        except Exception as exc:  # a bug or a store error in the middle of the turn
            if not turn.outcomes and not turn.text:
                raise
            log.exception("chat turn failed after producing output (conversation %s)", conversation_id)
            fallback = [{"kind": "internal_error", "value": type(exc).__name__}]
        if fallback is not None and fallback[0]["kind"] in ("max_steps", "token_budget", "tool_budget"):
            log.warning("chat turn stopped by its budget (conversation %s): %s %s", conversation_id,
                        fallback[0]["kind"], turn.budget.snapshot())
        if fallback is not None and fallback[0]["kind"] in ("llm_error", "turn_timeout") \
                and not turn.outcomes and not turn.text:
            emit("error", {"code": "llm_unavailable", "message": LLM_UNAVAILABLE})
            return
        await self._finish(turn, fallback)

    async def _steps(self, turn: _Turn, messages: list[dict]) -> list[dict] | None:
        """The LLM / tool steps of a turn. Returns None when the LLM's text can stay, otherwise
        the problems that make the template reply replace it.

        Budgets (design §4.8): a step starts only while steps and tokens are left; each tool call
        runs only while calls are left for the step and the turn. A call that is not admitted is
        never executed: it gets a `tool_budget_exceeded` result (stored like any tool call, so
        every tool call of the completion has its result and the stored history stays well-formed).
        Once the turn's calls are used up and a call was refused, the turn ends with the template."""
        while (stop := turn.budget.stop_before_step()) is None:
            try:
                completion = await self._llm_step(turn, messages)
            except _Stopped as stopped:  # guard failure, or no retry left within the token cap
                return stopped.problems
            if not completion.tool_calls:
                if completion.finish_reason != "stop":  # "length", "content_filter", ...
                    return [{"kind": "truncated", "value": str(completion.finish_reason)}]
                if not turn.text.strip():
                    return [{"kind": "empty_reply", "value": ""}]
                return None
            if _flooded(completion, self.budget):
                # far more calls than a step may run (06 review P2-3): none runs and none is
                # stored one by one; the logged completion is marked so the next context skips it
                refused = len(completion.tool_calls)
                turn.budget.refused_calls += refused
                log.warning("chat turn: a completion with %d tool calls; none run (conversation %s)",
                            refused, turn.conversation_id)
                return [{"kind": "tool_budget", "value": str(refused)}]
            messages.append(completion.assistant_message())
            turn_exhausted = False
            for call in completion.tool_calls:
                refused = turn.budget.admit_tool_call()
                if refused is None:
                    turn.emit("status", {"text": STATUS_TEXT.get(call["name"], DEFAULT_STATUS)})
                    outcome = await turn.threads.run(self._tools.run, turn.user_id, turn.conversation_id,
                                                     call["name"], call["arguments"])
                else:
                    turn_exhausted = turn_exhausted or refused == "per_turn"
                    payload = refusal(refused, self.budget)
                    outcome = Outcome(call["name"], str(call["arguments"])[:MAX_RAW_ARGUMENTS], payload["code"], payload,
                                      error=f"budget: {refused}")
                # The outcome and its cards belong to the turn as soon as the tool returned, before
                # the row is written: if the time limit lands on the write, the reply still
                # carries the card that the stored row (written while draining) describes (P2-5).
                turn.outcomes.append(outcome)
                if refused is None:  # a refusal is no record a reply may quote
                    turn.guard.add_record(outcome.payload)
                for card in outcome.cards:
                    turn.cards.append(card)
                    turn.emit("card", {"card": card})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": outcome.for_llm()})
                await turn.threads.run(
                    self.store.add_tool_call, turn.user_id, turn.user_message["id"], outcome.name,
                    outcome.args, outcome.payload, error=outcome.error, latency_ms=outcome.latency_ms)
            if turn_exhausted:
                return [{"kind": "tool_budget", "value": str(self.budget.max_tool_calls_per_turn)}]
        return [stop]

    async def _drain(self, turn: _Turn) -> None:
        """Wait for worker threads the time limit (or an error) left running. A thread cannot be
        stopped; until it ends it may still write (a quote, a tool call row), so the reply is not
        stored and the conversation lock and the slot are not released before it ends - unless it
        outlives `drain_timeout` (store timeouts make that unlikely), which is logged."""
        if not turn.threads.running:
            return
        log.warning("chat turn cut short with %d worker thread(s) running; waiting up to %gs (conversation %s)",
                    turn.threads.running, self.drain_timeout, turn.conversation_id)
        if not await turn.threads.drain(self.drain_timeout):
            log.error("chat turn: %d worker thread(s) still running after %gs; ending the turn anyway "
                      "(conversation %s)", turn.threads.running, self.drain_timeout, turn.conversation_id)

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

            def log_call(response: dict, usage: Any = None, *, output_chars: int | None = None,
                         no_response: bool = False) -> asyncio.Future:
                # every attempt counts against the turn's tokens, also failed ones and retries
                charged, estimated = turn.budget.charge(usage, messages, output_chars=output_chars,
                                                        no_response=no_response)
                response = {**response, "budget": {"charged_tokens": charged, "estimated": estimated,
                                                   "turn_tokens": turn.budget.tokens}}
                # `tokens` = what the turn was charged, so the site's daily budget (§8.2) counts
                # estimated calls the same way (06 review P2-9); `response.usage` keeps the report
                return turn.threads.start(
                    self.store.add_llm_call, turn.user_id, turn.conversation_id, request, response,
                    latency_ms=int((time.monotonic() - started) * 1000), tokens=charged,
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
                    raise LLMError("LLM stream ended without a completion", retryable=True,
                                   received=len(content))
                rest = buffer.flush()
                if rest.strip():
                    self._release(turn, blank + rest, not released)
            except _GuardFailed as failed:
                # the stream was cut by us; how much (hidden) output it produced is unknown
                await asyncio.shield(log_call({"content": content, "guard_problems": failed.problems,
                                               "aborted": True}))
                raise
            except asyncio.CancelledError:
                # the turn's time limit (or shutdown) interrupted this call: it is still recorded
                # and charged (06 review P2-4); `_drain` waits for the record to be written
                log_call({"content": content, "aborted": "turn_timeout"})
                raise
            except (LLMError, TimeoutError) as exc:
                if isinstance(exc, LLMError):
                    error, usage = exc, exc.usage
                    received = exc.received if exc.received is not None else (len(content) or None)
                else:  # this call's own time limit: the stream was open, its output unknown
                    error, usage, received = LLMError("LLM call timed out", retryable=True), None, None
                await asyncio.shield(log_call(
                    {"content": content, "error": str(error)[:300], "attempt": attempt + 1}, usage,
                    output_chars=received, no_response=isinstance(exc, LLMError) and received is None))
                if released or not error.retryable or attempt == self.retries:
                    raise error from None
                if (stop := turn.budget.out_of_tokens()) is not None:
                    raise _Stopped([stop]) from None  # no retry past the token cap (P2-2)
                await self._sleep(self.backoff * 2 ** attempt)
                continue
            record = completion.as_record()
            if _flooded(completion, self.budget):
                record["tool_calls_not_run"] = True  # build_context skips this completion
            await asyncio.shield(log_call(record, completion.usage, output_chars=completion.output_chars))
            return completion
        raise LLMError("unreachable", retryable=False)  # pragma: no cover

    def _city_index(self) -> CityIndex:
        """City names for the guard, from the airline's current network (rebuilt when
        locations.db changes)."""
        net = self._networks.current()
        if self._index is None or self._index[0] != net.fingerprint.id:
            self._index = (net.fingerprint.id, CityIndex(net.cities))
        return self._index[1]


def _flooded(completion: Completion, budget: AgentBudget) -> bool:
    """More tool calls in one completion than any honest step needs (P2-3)."""
    return len(completion.tool_calls) > budget.max_tool_calls_per_step * FLOOD_FACTOR
