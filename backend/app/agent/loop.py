"""The agent loop (design §4): one chat turn as a stream of contract SSE events.

    user message stored -> [LLM -> tools -> cards]* -> guarded text -> assistant message stored

The LLM chooses tools and writes text; routes, prices and tickets come from the airline through
the four tools. Text is released sentence by sentence after the ReplyGuard traced every number
and city in it; the first problem replaces the whole reply with a code template. The turn's
quote cards become bookable only when `Airline.post_assistant_message` stores them (`done`).

Runtime (ADR-001): Pydantic AI runs the model stream and dispatches the tool calls
(`Agent.iter` + `node.stream`); this module decides everything else - budgets, retries and their
charges, the guard, cards, app.db records, time limits, cancellation and the worker-thread drain.

Nothing here can book: the loop talks to the airline through `Toolbox` (four tools, no booking)
and `post_assistant_message`. Booking is the HTTP endpoint's call, caused by the user's click.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator

from pydantic_ai import Agent as _Runtime
from pydantic_ai import InstrumentationSettings, RunContext, Tool, UsageLimits
from pydantic_ai.capabilities import AbstractCapability, Instrumentation
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.messages import (FunctionToolResultEvent, PartDeltaEvent, PartStartEvent,
                                  TextPart, TextPartDelta, ThinkingPart, ThinkingPartDelta, ToolCallPart,
                                  ToolCallPartDelta)

from .. import tracing
from ..airline import ConversationBusy, NotFound
from .budget import AgentBudget, BudgetTracker, refusal
from .concurrency import ServerBusy, Threads, TurnConcurrency, TurnSlots
from .config import EFFORTS
from .context import build_context
from .guard import CityIndex, ReplyGuard, SentenceBuffer
from .model import ChatModel, Completion, LLMError, ThinkSplitter, before_response, llm_error, to_model_messages
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
FINISHED_RUNS_KEPT = 1024  # finished runs the cancel endpoint still knows (repeated Stop, late Stop)
CANCELLED_CALL = {"code": "cancelled", "message": "The user stopped the reply before this tool call finished."}
TERMINAL = {"done": "completed", "cancelled": "cancelled", "error": "failed"}  # event -> final run state
FLOOD_FACTOR = 4  # a completion with more than max_tool_calls_per_step * this many calls runs none
SDK_TOOL_RETRIES = 1000  # the SDK's own per-tool retry cap; our budget ends a turn long before
_RAW = "__raw__"  # arguments the SDK could not parse, handed to Toolbox.run as the raw string
_ACTIVE = object()  # `Agent(tracer_provider=...)` default: the provider of `app.tracing` (None: off)


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


class _RetryStep(Exception):
    """The LLM call of a step failed before any of its text was released: call again."""


class _UserBusy(Exception):
    """The user already has `max_turns_per_user` turns running."""


class _NotStored(Exception):
    """Storing the user's message failed: nothing of the turn exists."""


@dataclass
class Run:
    """One run of a chat turn in the in-process run registry (#48, invariants R1-R8).

    `state`: queued (waiting for the conversation lock / a slot; nothing stored yet) -> running
    (from the moment the user message is being stored) -> finishing (the steps ended; the reply
    is being stored) -> completed | failed; or, through `Agent.cancel_run`, queued / running ->
    cancelling -> cancelled. The final state follows the terminal event the run sent.

    The registry lives in this process: it is correct only with one worker (uvicorn runs one,
    design §4.9; the conversation locks have the same limit). With several workers or hosts a
    Stop could reach a process that does not run the turn; that needs a shared coordinator
    (e.g. a cancel flag in app.db or Redis that every step boundary polls, or sticky routing by
    conversation)."""

    id: str
    user_id: str
    conversation_id: str
    state: str = "queued"
    started: bool = False  # the run's task has begun (a Stop before that only sets the state)
    task: asyncio.Task | None = None
    turn: "_Turn | None" = None
    stop_requested: float | None = None  # monotonic time of the accepted Stop
    tokens_at_stop: int | None = None
    terminal: str | None = None  # the terminal event sent (done / cancelled / error)
    sent_first: bool = False  # the first event (it carries the run id) was sent
    error_code: str | None = None  # the code of the terminal `error` event (tracing)


class TurnStream:
    """The events of one turn (an async iterator) and its `run_id` (the SSE header `X-Run-Id`)."""

    def __init__(self, run_id: str, events: AsyncIterator["Event"]):
        self.run_id = run_id
        self._events = events

    def __aiter__(self) -> "TurnStream":
        return self

    async def __anext__(self) -> "Event":
        return await self._events.__anext__()

    async def aclose(self) -> None:
        await self._events.aclose()


@dataclass
class _Turn:
    """One turn's state. It is also the run's `deps`: the tools take the user and the
    conversation from here, never from the model's arguments."""

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
    # the tool calls of the current step (raw arguments), their results by tool_call_id, and how
    # many of them already have their tool_calls row
    step_calls: list[dict] = field(default_factory=list)
    step_results: dict[str, Outcome] = field(default_factory=dict)
    step_logged: int = 0
    exhausted: bool = False  # a call was refused because the turn's calls were used up
    messages: list[dict] = field(default_factory=list)  # the context, OpenAI shape (see `_WireContext`)
    attempt: int = 0  # failed attempts of the current step's LLM call (reset when a call succeeds)
    steps_task: asyncio.Future | None = None
    cancelled: bool = False  # the user stopped the run (`Agent.cancel_run`)
    in_flight: str | None = None  # the tool call whose worker thread is running
    span: Any = tracing.NO_SPAN  # the turn's `agent.turn` span (#35; a no-op when tracing is off)
    fallback_kind: str | None = None  # the template replaced the reply because of this (tracing)


def message_view(message: dict) -> dict:
    """Contract `Message` of a stored row without quote cards (user messages)."""
    return {"id": message["id"], "role": message["role"], "text": message["text"],
            "cards": list(message["cards"]), "created_at": message["created_at"],
            "status": message.get("status", "completed")}


class _RawArguments(AbstractCapability[Any]):
    """Arguments that are not a JSON object reach the tool as the raw string (`Toolbox.run`
    answers `invalid_arguments`, as before) instead of the SDK's own retry prompt."""

    async def before_tool_validate(self, ctx, *, call, tool_def, args):  # type: ignore[override]
        if isinstance(args, dict):
            return args
        try:
            value = json.loads(args) if isinstance(args, str) and args.strip() else {}
        except (ValueError, RecursionError):
            value = None
        return value if isinstance(value, dict) else {_RAW: args if isinstance(args, str) else ""}


class _WireContext(AbstractCapability[Any]):
    """Every request carries the loop's own context (`_Turn.messages`: app.db history plus this
    turn's steps, in the OpenAI shape) rather than the SDK's copy of it. So the wire is what it
    was before ADR-001: tool calls in their wire-safe form (`wire_tool_call`, review #11), our
    JSON for every tool result (also `unknown_tool` and budget refusals), and the reasoning of
    earlier steps passed back verbatim."""

    async def before_model_request(self, ctx, request_context):  # type: ignore[override]
        return replace(request_context, messages=to_model_messages(ctx.deps.messages))


class Agent:
    def __init__(self, airline: Any, llm: ChatModel, *, budget: AgentBudget | None = None,
                 max_steps: int | None = None,
                 ping_interval: float = 15.0, retries: int = 2, backoff: float = 0.5,
                 llm_timeout: float = 60.0, turn_timeout: float | None = None,
                 lock_timeout: float | None = LOCK_TIMEOUT,
                 concurrency: TurnConcurrency | None = None, drain_timeout: float = DRAIN_TIMEOUT,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 tracer_provider: Any = _ACTIVE):
        """`budget`: the per-turn limits (design §4.8; default `AgentBudget()`). `max_steps` and
        `turn_timeout` override its fields (tests, older callers). `concurrency`: the server-wide
        limit on running turns (design §4.9; default `TurnConcurrency()`). `drain_timeout`: how
        long a cut-short turn waits for its worker threads before it ends anyway.
        `tracer_provider`: an OpenTelemetry TracerProvider for the runtime's spans (#35). Default:
        the one `app.tracing` holds when the agent is built (none unless OTEL_TRACING_ENABLED);
        None: no SDK instrumentation at all. The loop's own spans (`agent.turn`, `reply_guard`,
        `run.stop`) always go to `app.tracing`'s provider."""
        if tracer_provider is _ACTIVE:
            tracer_provider = tracing.provider()
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
        self._running: dict[str, _Turn] = {}  # conversation id -> its running turn
        self._runs: dict[str, Run] = {}  # run id -> run, until it ended (event loop only)
        self._finished: OrderedDict[str, Run] = OrderedDict()  # the last FINISHED_RUNS_KEPT runs
        capabilities: list[Any] = [_RawArguments(), _WireContext()]
        if tracer_provider is not None:
            capabilities.append(Instrumentation(InstrumentationSettings(
                tracer_provider=tracer_provider, include_content=False)))
        self._runtime: _Runtime[_Turn, str] = _Runtime(
            llm.chat, deps_type=_Turn, tools=self._sdk_tools(), capabilities=capabilities,
            # tool calls that come with text are run (no early end on text); the loop returns
            # after a step without tool calls, so the SDK's End node is never reached
            retries=SDK_TOOL_RETRIES, end_strategy="graceful", name="ocean-above")

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
                    owner_checked: bool = False) -> TurnStream:
        """Start a turn and return its events: `user_message`, then `status` / `card` / `delta` /
        `replace` / `ping`, ending with exactly one `done`, `cancelled` (Stop, `cancel_run`) or
        `error`. The first event other than `ping` carries the run's `run_id` (also
        `TurnStream.run_id`).

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
        run = Run(f"r_{secrets.token_hex(8)}", user_id, conversation_id)
        self._runs[run.id] = run
        run.task = task = asyncio.get_running_loop().create_task(
            self._run(run, text, reasoning_effort, queue, on_not_stored or on_server_busy))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return TurnStream(run.id, self._events(queue))

    def cancel_run(self, user_id: str, conversation_id: str, run_id: str) -> str | None:
        """The user's Stop (#48): cancel the run `run_id` of the user's conversation. Returns the
        run's state after the request (`cancelling` when this Stop was accepted; `cancelled` for a
        repeated Stop; `finishing` / `completed` / `failed` when it came too late and changes
        nothing), or None when no such run of this user and conversation is known (R1).

        Queued (nothing stored yet): the run's task is cancelled; nothing is stored and the day's
        message slot is given back (R5). Running: no further LLM request or tool call starts and
        the model's HTTP stream is closed (R2); a worker thread that already started runs to its
        end and is drained before the stopped reply is stored and the lock and slot are released
        (R3). Only this method stops a run: a client that stops reading does not. Event loop only."""
        run = self._runs.get(run_id) or self._finished.get(run_id)
        if run is None or run.user_id != user_id or run.conversation_id != conversation_id:
            return None
        if run.state not in ("queued", "running"):
            return run.state  # cancelling, finishing or ended: idempotent, nothing changes (R8)
        turn = run.turn
        if turn is not None and turn.steps_task is not None and turn.steps_task.done():
            # the steps ended in this instant (their result is set, `_turn` has not resumed yet):
            # the reply counts as complete and is stored as usual (R4; test_p2_9_…finishing…)
            return "finishing"
        queued = run.state == "queued"
        run.state = "cancelling"
        run.stop_requested = time.monotonic()
        log.info("agent.run.stop_requested run=%s conversation=%s queued=%s", run.id, conversation_id, queued)
        if queued:
            if run.started and run.task is not None:
                run.task.cancel()  # waiting for the lock or a slot; `_run` settles it
        elif turn is not None:
            run.tokens_at_stop = turn.budget.tokens
            turn.cancelled = True
            if turn.steps_task is not None:
                turn.steps_task.cancel()
        # else: the user message is being stored or the context built; `_turn` sees the state
        return "cancelling"

    def cancel(self, user_id: str, conversation_id: str) -> bool:
        """Stop the user's running turn in `conversation_id` (any run id; tests and tools). True
        when a Stop was accepted. See `cancel_run`."""
        for run in list(self._runs.values()):
            if run.user_id == user_id and run.conversation_id == conversation_id \
                    and run.state in ("queued", "running"):
                return self.cancel_run(user_id, conversation_id, run.id) == "cancelling"
        return False

    def run_state(self, run_id: str) -> str | None:
        """The registry's state of a run (tests, diagnostics)."""
        run = self._runs.get(run_id) or self._finished.get(run_id)
        return None if run is None else run.state

    def _retire(self, run: Run) -> None:
        """Move an ended run to the bounded list of finished runs, keeping only what `cancel_run`
        reads (id, owner, conversation, state): the turn's context, cards and event queue are
        dropped (review P2-2)."""
        run.turn = None
        run.task = None
        if self._runs.get(run.id) is run:
            del self._runs[run.id]
        self._finished[run.id] = run
        while len(self._finished) > FINISHED_RUNS_KEPT:
            self._finished.popitem(last=False)

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
    async def _run(self, run: Run, text: str, reasoning_effort: str | None,
                   queue: asyncio.Queue, on_not_stored: Callable[[], Any] | None = None) -> None:
        conversation_id = run.conversation_id
        run.started = True

        def emit(name: str, data: dict) -> None:
            if run.terminal is not None:  # R4: nothing after the terminal event
                log.error("chat turn: %s after %s dropped (run %s)", name, run.terminal, run.id)
                return
            if not run.sent_first:
                data = {**data, "run_id": run.id}  # the first event carries the run id
                run.sent_first = True
            if name in TERMINAL:
                run.terminal = name
                run.state = TERMINAL[name]
                if name == "error":
                    run.error_code = data.get("code")
            queue.put_nowait(Event(name, data))

        async def not_stored() -> None:
            if on_not_stored is not None:
                try:
                    await asyncio.to_thread(on_not_stored)
                except Exception:
                    log.exception("not-stored callback failed (conversation %s)", conversation_id)

        # One trace per turn (#35): `agent.turn` is the root; the SDK's run / chat / tool spans
        # and the airline's spans nest under it. Correlation: the run id (SSE `X-Run-Id`) and
        # the user message id (app.db llm_calls / tool_calls rows) are attributes.
        with tracing.span("agent.turn", {"ocean.run_id": run.id, "ocean.reasoning_effort": reasoning_effort}) as span:
            span.set_attributes_from(lambda: dict.fromkeys(("session.id", "ocean.conversation_hash"),
                                                           tracing.digest(conversation_id)))
            await self._run_traced(run, text, reasoning_effort, queue, emit, not_stored, span)

    async def _run_traced(self, run: Run, text: str, reasoning_effort: str | None, queue: asyncio.Queue,
                          emit: Callable[[str, dict], None], not_stored: Callable[[], Awaitable[None]],
                          span: Any) -> None:
        user_id, conversation_id = run.user_id, run.conversation_id
        # The conversation lock first, then the user's own limit, then a server-wide slot: a
        # second message of a busy conversation waits without taking a slot. Every refusal comes
        # before anything is stored.
        try:
            if run.state == "cancelling":  # stopped before the task began
                raise asyncio.CancelledError()
            async with self._locks.hold_async(conversation_id, timeout=self.lock_timeout):
                with self._user_turn(user_id):
                    async with self.slots.hold():
                        await self._turn(run, text, reasoning_effort, emit)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if run.state != "cancelling" or run.turn is not None or (task is not None and task.uncancel() > 0):
                raise  # shutdown, not a Stop
            # Stop while queued (R5): nothing was stored; the day's message slot is given back
            await not_stored()
            emit("cancelled", {"run_id": run.id, "message": None})
            self._log_stopped(run, None)
        except ConversationBusy:
            emit("error", {"code": "conversation_busy", "message": BUSY})
        except _UserBusy:
            log.warning("chat turn refused: user already has %d turns running (conversation %s)",
                        self.slots.config.max_turns_per_user, conversation_id)
            emit("error", {"code": "conversation_busy", "message": USER_BUSY})
        except ServerBusy:
            run.state = "finishing"  # refused: a Stop from here on is too late (review P2-1)
            log.warning("chat turn refused: %d turns running, none ended within %gs (conversation %s)",
                        self.slots.active, self.slots.config.queue_seconds, conversation_id)
            await not_stored()
            emit("error", {"code": "server_busy", "message": SERVER_BUSY})
        except _NotStored:
            run.state = "finishing"  # (review P2-1)
            log.exception("chat turn: the user's message could not be stored (conversation %s)", conversation_id)
            await not_stored()
            emit("error", {"code": "internal_error", "message": NOT_STORED})
        except Exception:
            log.exception("chat turn failed (conversation %s)", conversation_id)
            emit("error", {"code": "internal_error", "message": INTERNAL})
        finally:
            if run.terminal is None:
                # R4 backstop (review P2-1): every stream ends with a terminal event, also when the
                # task was cancelled by a shutdown or a path above ended without one
                emit("error", {"code": "internal_error", "message": INTERNAL})
            turn = run.turn
            # the run is retired and its stream ended before anything is traced, so no tracing
            # error can keep the stream open or the run registered (review P2-2); lock and slots
            # were already released by the `async with` blocks above
            self._retire(run)
            queue.put_nowait(None)
            span.set_attributes_from(lambda: _trace_end(run, turn))

    def _log_stopped(self, run: Run, turn: _Turn | None, drain_ms: int | None = None) -> None:
        """agent.run.cancelled: stop-to-end latency, tokens charged after the Stop, drain time
        (log fields now; spans in #35)."""
        latency = None if run.stop_requested is None else int((time.monotonic() - run.stop_requested) * 1000)
        after = None if turn is None or run.tokens_at_stop is None else turn.budget.tokens - run.tokens_at_stop
        # `run.stop`: from the accepted Stop to the stopped reply stored (#35)
        start = None if run.stop_requested is None else tracing.monotonic_to_epoch_ns(run.stop_requested)
        with tracing.span("run.stop", start_time_ns=start) as span:
            span.set_attributes({"ocean.run_id": run.id, "ocean.stop.stored": turn is not None,
                                 "ocean.stop.stop_to_end_ms": latency, "ocean.stop.drain_ms": drain_ms,
                                 "ocean.stop.tokens_after_stop": after})
        log.info("agent.run.cancelled run=%s conversation=%s stored=%s stop_to_end_ms=%s tokens_after_stop=%s "
                 "drain_ms=%s", run.id, run.conversation_id, turn is not None, latency, after, drain_ms)

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

    async def _turn(self, run: Run, text: str, reasoning_effort: str | None,
                    emit: Callable[[str, dict], None]) -> None:
        user_id, conversation_id = run.user_id, run.conversation_id
        store = self.store
        conversation = await asyncio.to_thread(store.get_conversation, user_id, conversation_id)
        # From here a Stop no longer cancels this task: the user message may be stored (R5)
        run.state = "running"
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
        turn.messages = messages = [{"role": "system", "content": system_prompt(today)}, *context.messages]
        turn.span = tracing.current()  # `agent.turn` (set in `_run`)
        turn.span.set_attribute("ocean.message_id", user_message["id"])
        run.turn = turn
        if run.state == "cancelling":  # stopped while the message was stored: no step starts
            turn.cancelled = True
            run.tokens_at_stop = 0

        self._running[conversation_id] = turn
        stopped = False
        drain_ms = None
        try:
            try:
                try:
                    if not turn.cancelled:
                        async with asyncio.timeout(self.turn_timeout):
                            turn.steps_task = asyncio.ensure_future(self._steps(turn, messages))
                            fallback = await turn.steps_task
                        run.state = "finishing"
                    else:
                        stopped = True
                finally:
                    drain_started = time.monotonic()
                    await self._drain(turn)
                    drain_ms = int((time.monotonic() - drain_started) * 1000)
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if not turn.cancelled or (task is not None and task.cancelling()):
                    raise  # shutdown, not a Stop
                stopped = True
            except TimeoutError:
                fallback = [{"kind": "turn_timeout", "value": str(self.turn_timeout)}]
            except LLMError as exc:
                fallback = [{"kind": "llm_error", "value": str(exc)[:200]}]
            except Exception as exc:  # a bug or a store error in the middle of the turn
                if not turn.outcomes and not turn.text:
                    raise
                log.exception("chat turn failed after producing output (conversation %s)", conversation_id)
                fallback = [{"kind": "internal_error", "value": type(exc).__name__}]
        finally:
            if self._running.get(conversation_id) is turn:
                del self._running[conversation_id]
        # an accepted Stop always ends `cancelled` (R4), also when the steps ended anyway in the
        # same instant (or a library swallowed the cancellation)
        stopped = stopped or turn.cancelled
        if stopped:
            await self._finish_stopped(run, turn)
            self._log_stopped(run, turn, drain_ms)
            return
        run.state = "finishing"
        if fallback is not None and fallback[0]["kind"] in ("max_steps", "token_budget", "tool_budget"):
            log.warning("chat turn stopped by its budget (conversation %s): %s %s", conversation_id,
                        fallback[0]["kind"], turn.budget.snapshot())
        if fallback is not None and fallback[0]["kind"] in ("llm_error", "turn_timeout") \
                and not turn.outcomes and not turn.text:
            emit("error", {"code": "llm_unavailable", "message": LLM_UNAVAILABLE})
            return
        await self._finish(turn, fallback)

    # ------------------------------------------------------------------ the steps ----------
    async def _steps(self, turn: _Turn, messages: list[dict]) -> list[dict] | None:
        """The LLM / tool steps of a turn. Returns None when the LLM's text can stay, otherwise
        the problems that make the template reply replace it.

        One Pydantic AI run drives the steps: it streams each LLM call and dispatches its tool
        calls (one at a time, in call order, see `_sdk_tools`). `messages` is our copy of the
        context in the OpenAI shape (estimates, llm_calls records); a failed LLM call that is
        retried restarts the run from it.

        Budgets (design §4.8): a step starts only while steps and tokens are left; each tool call
        runs only while calls are left for the step and the turn. A call that is not admitted is
        never executed: it gets a `tool_budget_exceeded` result (stored like any tool call, so
        every tool call of the completion has its result and the stored history stays well-formed).
        Once the turn's calls are used up and a call was refused, the turn ends with the template."""
        while True:
            try:
                return await self._run_steps(turn, messages)
            except _RetryStep:  # the step's retry count is on the turn (`turn.attempt`)
                pass

    async def _run_steps(self, turn: _Turn, messages: list[dict]) -> list[dict] | None:
        settings = self.llm.settings(turn.reasoning_effort)
        # no SDK request limit: the steps budget (max 20 by env bounds) is the only one
        # `conversation_id`: span metadata only (the SDK's `gen_ai.conversation.id`, Phoenix's
        # session): the conversation hash, never the id; the wire is rebuilt by `_WireContext`
        async with self._runtime.iter(None, message_history=to_model_messages(messages), deps=turn,
                                      model_settings=settings, usage_limits=UsageLimits(request_limit=None),
                                      conversation_id=tracing.digest(turn.conversation_id)
                                      if tracing.provider() is not None else None) as run:
            async for node in run:
                if _Runtime.is_model_request_node(node):
                    if turn.cancelled:  # R2: no LLM request after a Stop
                        raise asyncio.CancelledError()
                    if turn.attempt == 0 and (stop := turn.budget.stop_before_step()) is not None:
                        return [stop]  # (a retry belongs to the step already counted)
                    try:
                        completion = await self._llm_step(turn, messages, run, node)
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
                    turn.step_calls, turn.step_results, turn.step_logged = completion.tool_calls, {}, 0
                elif _Runtime.is_call_tools_node(node):
                    async with node.stream(run.ctx) as events:
                        async for event in events:
                            if isinstance(event, FunctionToolResultEvent) \
                                    and event.part.tool_call_id not in turn.step_results:
                                # a result our tools did not make: a name that is not one of our
                                # tools, refused by the SDK before any tool ran. It is answered and
                                # stored as before (`unknown_tool`, from Toolbox.run)
                                await self._tool_call(turn, event.part.tool_call_id, event.part.tool_name or "")
                    await self._log_tool_calls(turn, final=True)
                    for call in turn.step_calls:
                        outcome = turn.step_results[call["id"]]
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": outcome.for_llm()})
                    if turn.exhausted:
                        return [{"kind": "tool_budget", "value": str(self.budget.max_tool_calls_per_turn)}]
        # a step without tool calls returns above, before the run could reach its End node
        raise AssertionError("the run ended without a step that returned")

    # ------------------------------------------------------------------ tools --------------
    def _sdk_tools(self) -> list[Tool]:
        """The four tools as SDK tools over our own schemas and `Toolbox` (validation, structured
        errors). Sequential: one call at a time, in the order the model made them, so the budget
        admits them in order and the cards keep that order. `strict` off: the request carries the
        schemas exactly as before."""
        def body(name: str) -> Callable[..., Awaitable[str]]:
            async def run(ctx: RunContext[_Turn], **_args: Any) -> str:
                # the arguments the model sent are taken raw from the completion in `_tool_call`
                span = tracing.current()  # the SDK's `execute_tool` span: our result code on it
                outcome = await self._tool_call(ctx.deps, ctx.tool_call_id or "", name)
                span.set_attributes_from(lambda: {
                    "ocean.tool.code": outcome.code, "ocean.tool.cards": len(outcome.cards),
                    "ocean.tool.refused": (outcome.error or "").startswith("budget")})
                return outcome.for_llm()
            return run

        tools = []
        for schema in TOOL_SCHEMAS:
            name = schema["function"]["name"]
            tool = Tool.from_schema(body(name), name=name, description=schema["function"]["description"],
                                    json_schema=schema["function"]["parameters"], takes_ctx=True, sequential=True)
            tool.strict = False
            tools.append(tool)
        return tools

    async def _tool_call(self, turn: _Turn, call_id: str, name: str) -> Outcome:
        """Admit, run and record one tool call of the current step. The arguments are the model's
        raw string (from the completion), validated by `Toolbox.run`. The blocking work runs in a
        tracked worker thread: if the turn is cancelled meanwhile the thread still ends, and the
        turn waits for it (`_drain`) before it releases its lock."""
        call = next((c for c in turn.step_calls if c["id"] == call_id),
                    {"id": call_id, "name": name, "arguments": ""})
        if turn.cancelled:  # R2: never start a tool after a Stop (also if the SDK caught the cancel)
            raise asyncio.CancelledError()
        refused = turn.budget.admit_tool_call()
        if refused is None:
            turn.emit("status", {"text": STATUS_TEXT.get(call["name"], DEFAULT_STATUS)})
            turn.in_flight = call_id
            outcome = await turn.threads.run(self._tools.run, turn.user_id, turn.conversation_id,
                                             call["name"], call["arguments"])
            if turn.cancelled:  # the Stop came while the thread ran: its result is not used (R7)
                raise asyncio.CancelledError()  # (in_flight still names it: stored as interrupted)
            turn.in_flight = None
        else:
            turn.exhausted = turn.exhausted or refused == "per_turn"
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
        turn.step_results[call_id] = outcome
        await self._log_tool_calls(turn)
        return outcome

    async def _log_tool_calls(self, turn: _Turn, *, final: bool = False) -> None:
        """Store the tool_calls rows of the step in call order (context.py lines them up with
        the completion), each as soon as it and every call before it have their outcome."""
        while turn.step_logged < len(turn.step_calls):
            outcome = turn.step_results.get(turn.step_calls[turn.step_logged]["id"])
            if outcome is None:
                if not final:
                    return
                raise RuntimeError("a tool call of the step got no result")
            turn.step_logged += 1
            await turn.threads.run(
                self.store.add_tool_call, turn.user_id, turn.user_message["id"], outcome.name,
                outcome.args, outcome.payload, error=outcome.error, latency_ms=outcome.latency_ms)

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
            turn.fallback_kind = fallback[0]["kind"]
            turn.span.set_attribute("ocean.fallback_kind", turn.fallback_kind)
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

    async def _finish_stopped(self, run: Run, turn: _Turn) -> None:
        """Store a reply the user stopped (R5-R7), after its worker threads were drained: only the
        text already released through the guard (no template, no click hint) and the cards of
        tool calls that finished; none of its quote cards is presented (no nonce, R6). Tool calls
        of the step that have no result - the one interrupted, the ones never started - are
        stored as `cancelled`, so the next context lines up with the logged completion (R7)."""
        for call in turn.step_calls:
            if call["id"] not in turn.step_results:
                started = call["id"] == turn.in_flight
                if started:
                    log.info("chat turn stopped during tool %s; its result is discarded (run %s)",
                             call["name"], run.id)
                turn.step_results[call["id"]] = Outcome(
                    call["name"], str(call["arguments"])[:MAX_RAW_ARGUMENTS], "cancelled", dict(CANCELLED_CALL),
                    error="cancelled: interrupted" if started else "cancelled: not run")
        await self._log_tool_calls(turn, final=True)
        message = await asyncio.to_thread(
            self._post_assistant_message, turn.user_id, turn.conversation_id, turn.text.rstrip(), turn.cards,
            [{"kind": "cancelled", "value": ""}], present=False, status="cancelled")
        turn.emit("cancelled", {"run_id": run.id, "message": message})

    # ------------------------------------------------------------------ LLM step -----------
    def _release(self, turn: _Turn, piece: str, first_of_step: bool) -> None:
        """Check the reply so far plus `piece`; send `piece` when nothing is wrong. Checking the
        whole text catches a number or a city name that spans two pieces (`St. | Louis`)."""
        if not turn.text:
            piece = piece.lstrip()
        elif first_of_step and not turn.text[-1].isspace() and not piece[:1].isspace():
            piece = "\n\n" + piece  # text of a later step starts a new paragraph
        with tracing.span("reply_guard", {"ocean.guard.chars": len(turn.text) + len(piece)}) as span:
            problems = turn.guard.check(turn.text + piece)
            span.set_attributes_from(lambda: {"ocean.guard.result": "fail" if problems else "pass",
                                              "ocean.guard.problems": sorted({p["kind"] for p in problems}) or None})
        if problems:
            raise _GuardFailed(problems)
        turn.text += piece
        turn.emit("delta", {"text": piece})

    async def _llm_step(self, turn: _Turn, messages: list[dict], run: Any, node: Any) -> Completion:
        """One LLM call (one attempt). Text is released as it streams. A failed call is logged
        and charged; it is retried (`_RetryStep`, after the backoff) only while none of its text
        has been released, retries are left for this step (`turn.attempt`, reset when a call
        succeeds) and the turn still has tokens."""
        attempt = turn.attempt
        request = {"turn": turn.user_message["id"], "messages": list(messages),
                   "tools": [t["function"]["name"] for t in TOOL_SCHEMAS],
                   "reasoning_effort": self.llm.effort(turn.reasoning_effort)}
        started = time.monotonic()
        buffer = SentenceBuffer()
        think = ThinkSplitter()  # an inline <think> block (servers without reasoning_content)
        thought = ""
        released = False
        content = blank = ""
        received: int | None = None  # output characters, once any output arrived

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

        def got(n: int) -> None:
            # output seen so far, for a call that fails part-way; a complete call is charged from
            # its final response instead (`Completion.output_chars`, the same count)
            nonlocal received
            received = (received or 0) + n

        def show(text: str) -> None:
            """Visible text: released sentence by sentence through the guard."""
            nonlocal content, blank, released
            content += text
            for sentence in buffer.push(text):
                if sentence.strip():
                    self._release(turn, blank + sentence, not released)
                    blank, released = "", True
                else:
                    blank += sentence  # white space is never sent on its own

        try:
            # the per-call time limit; leaving the block (a guard failure, the limit, a
            # cancellation) closes the model's HTTP stream (on a real connection: at once)
            async with asyncio.timeout(self.llm_timeout):
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        text = ""
                        if isinstance(event, PartStartEvent):
                            part = event.part
                            if isinstance(part, TextPart):
                                text = part.content
                            elif isinstance(part, ThinkingPart):
                                got(len(part.content))
                            elif isinstance(part, ToolCallPart):
                                got(len(part.tool_name) + len(part.args_as_json_str() if part.args else ""))
                        elif isinstance(event, PartDeltaEvent):
                            delta = event.delta
                            if isinstance(delta, TextPartDelta):
                                text = delta.content_delta
                            elif isinstance(delta, ThinkingPartDelta):
                                got(len(delta.content_delta or ""))
                            elif isinstance(delta, ToolCallPartDelta):
                                args = delta.args_delta
                                got(len(delta.tool_name_delta or "")
                                    + len(args if isinstance(args, str) else json.dumps(args or {})))
                        if not text:
                            continue
                        got(len(text))
                        text, inline = think.feed(text)
                        thought += inline
                        show(text)
                    response = stream.response
                _unique_call_ids(response)
                text, inline = think.finish()
                thought += inline
                show(text)
                completion = Completion.from_response(response, content)
                completion.reasoning += thought
                if completion.finish_reason is None:  # the stream ended early (no finish reason)
                    raise LLMError("LLM stream ended without a finish_reason", retryable=True,
                                   received=completion.output_chars, usage=completion.usage)
                rest = buffer.flush()
                if rest.strip():
                    self._release(turn, blank + rest, not released)
        except _GuardFailed as failed:
            # the stream was cut by us; how much (hidden) output it produced is unknown
            await asyncio.shield(log_call({"content": content, "guard_problems": failed.problems,
                                           "aborted": True}))
            raise
        except asyncio.CancelledError:
            # the turn's time limit, a cancellation or shutdown interrupted this call: it is still
            # recorded and charged (06 review P2-4); `_drain` waits for the record to be written
            log_call({"content": content, "aborted": "cancelled" if turn.cancelled else "turn_timeout"})
            raise
        except Exception as exc:  # every other failure of the call is a failed, logged, charged call (review P2-2)
            if isinstance(exc, TimeoutError):  # this call's own time limit: output unknown
                error, usage, received = LLMError("LLM call timed out", retryable=True), None, None
            else:
                if not isinstance(exc, (LLMError, ModelAPIError, UnexpectedModelBehavior)):
                    # not a known provider failure (an SDK parser error on an odd chunk, or a bug
                    # here): retried like a broken stream, with the original kept in the log
                    log.warning("LLM call failed with %s (conversation %s)", type(exc).__name__,
                                turn.conversation_id, exc_info=exc)
                if received is None and not before_response(exc):
                    received = 0  # a response body was read, with no output in it (e.g. not SSE)
                error = llm_error(exc, received=received, scrub=self.llm.config.api_key)
                usage = error.usage
                received = error.received if error.received is not None else (len(content) or None)
            await asyncio.shield(log_call(
                {"content": content, "error": str(error)[:300], "attempt": attempt + 1}, usage,
                output_chars=received, no_response=not isinstance(exc, TimeoutError) and received is None))
            turn.span.add_event("llm.attempt_failed", {
                "ocean.llm.attempt": attempt + 1, "ocean.llm.error": type(exc).__name__,
                "ocean.llm.retryable": error.retryable, "ocean.llm.released": released})
            if released or not error.retryable or attempt == self.retries:
                raise error from None
            if (stop := turn.budget.out_of_tokens()) is not None:
                raise _Stopped([stop]) from None  # no retry past the token cap (P2-2)
            await self._sleep(self.backoff * 2 ** attempt)
            turn.attempt = attempt + 1
            raise _RetryStep() from None
        turn.attempt = 0
        record = completion.as_record()
        if _flooded(completion, self.budget):
            record["tool_calls_not_run"] = True  # build_context skips this completion
        elif completion.tool_calls:
            # the step's calls are known before the completion is logged: a Stop during the write
            # still stores a (cancelled) row for each of them (R7)
            turn.step_calls, turn.step_results, turn.step_logged = completion.tool_calls, {}, 0
        await asyncio.shield(log_call(record, completion.usage, output_chars=completion.output_chars))
        return completion

    def _city_index(self) -> CityIndex:
        """City names for the guard, from the airline's current network (rebuilt when
        locations.db changes)."""
        net = self._networks.current()
        if self._index is None or self._index[0] != net.fingerprint.id:
            self._index = (net.fingerprint.id, CityIndex(net.cities))
        return self._index[1]


def _unique_call_ids(response: Any) -> None:
    """Some OpenAI-compatible servers repeat a tool_call_id within one completion (`call_0` twice);
    the SDK refuses such a response. Repeats get a suffix (`call_0~2`), in place, so the step runs
    as it did before ADR-001 (review P2-1). The ids are ours to choose: they only pair a call with
    its result."""
    seen: set[str] = set()
    for part in response.parts:
        if isinstance(part, ToolCallPart) and part.tool_call_id:
            call_id, n = part.tool_call_id, 1
            while call_id in seen:
                n += 1
                call_id = f"{part.tool_call_id}~{n}"
            part.tool_call_id = call_id
            seen.add(call_id)


def _flooded(completion: Completion, budget: AgentBudget) -> bool:
    """More tool calls in one completion than any honest step needs (P2-3)."""
    return len(completion.tool_calls) > budget.max_tool_calls_per_step * FLOOD_FACTOR


def _trace_end(run: Run, turn: _Turn | None) -> dict:
    """The attributes of the turn's end on its `agent.turn` span: the outcome (completed /
    fallback - the template replaced the reply - / cancelled / failed), plus the budget used
    (counts only). Computed behind the tracing guard (`set_attributes_from`)."""
    if run.terminal == "done":
        outcome = "fallback" if turn is not None and turn.fallback_kind else "completed"
    else:
        outcome = "cancelled" if run.terminal == "cancelled" else "failed"
    attributes = {"ocean.outcome": outcome, "ocean.error_code": run.error_code}
    if turn is not None:
        attributes.update({f"ocean.budget.{k}": v for k, v in turn.budget.snapshot().items()})
    return attributes
