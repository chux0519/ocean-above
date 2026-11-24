# 10-otel T1 — STATE (2026-11-24)

Issue #35 (Refs #31 P2), plan `docs/iterations/10-otel/plan.md`, branch `it10-otel`.

## Done
- `backend/app/tracing.py`: switch, OTLP/HTTP provider, redaction at the exporter, a span helper that does nothing when off.
- Business spans in `agent/loop.py`, `airline/service.py`, `core/routing.py`. Pydantic AI's own spans (`include_content=False`) nest under them.
- `core/network.py`: a deadline on `locations.db` reads (the #35 comment).
- Tracing is wired into `api/settings.py` / `api/routes.py` (built at startup only when enabled, flushed at shutdown) and into the evals CLI.
- Compose profile `tracing` (Phoenix 22.0.1) and `.env.example` entries.
- 25 tests in `backend/tests/test_tracing.py`.
- Evals: fake 31/31, and a real-model run with tracing on against a local Phoenix (results below). Two Phoenix screenshots and a privacy scan of everything the collector received.

## Open decisions / for the coordinator
1. **Loop changes beyond adding spans:**
   - `Agent._run` is split into `_run` (opens the `agent.turn` span) and `_run_traced`, which is the old body unchanged.
   - `_runtime.iter(...)` now gets `conversation_id=<hash>`. This is SDK span metadata only. A test proves it never reaches the request body.
   - Two fields were added for tracing: `Run.error_code` and `_Turn.span` / `_Turn.fallback_kind`.
   - Booking, locks, budgets and cancel semantics are unchanged. The full suite passes unchanged and the B1–B7 model tests pass.
   - Your call whether this needs the Fable review the plan mentions.
2. **Files outside T1's list:**
   - `backend/evals/__main__.py`: 3 lines that turn tracing on from the env and flush it.
   - `backend/evals/report.py`: one header suffix, "tracing on (OTLP)".
   - Both are needed for the "real evals with tracing on" acceptance.
3. **The `locations.db` read deadline changes behaviour slightly.** SQL on locations.db that runs past 5 s is now interrupted and raises `NetworkDataError`; before, it ran without limit. Waiting for a lock was already bounded at 5 s (sqlite3's default busy timeout), so that part is unchanged. Not bounded: a file system that hangs inside a read call, and the hashing / stat around the read (review P2-6). Bookings already map `NetworkDataError` to `data_unavailable`, which is retryable.
4. **Phoenix shows Total Cost $0.** It has no price for `deepseek-flash`. Token counts are shown, from the SDK's `gen_ai.usage.*`.
5. **docker compose was not run here** (this machine has no docker). I checked the YAML with PyYAML: 4 services, `phoenix` only in profile `tracing`, volume `phoenix`. The image tag `arizephoenix/phoenix:version-22.0.1` exists on Docker Hub. Please run `docker compose config` and `docker compose --profile tracing config` on the homelab. A plain `docker compose config --services` must not list phoenix.
6. **Production stays off.** No new env on the VPS. The legal text is unchanged.

## Span inventory
One trace per chat turn; root `agent.turn`. A booking click is its own trace (its own HTTP request), rooted at `airline.book`.

| Span | Where | Parent | Attributes (all non-content) |
|---|---|---|---|
| `agent.turn` | `loop.Agent._run` | root | `ocean.run_id` (= SSE `X-Run-Id`), `ocean.message_id` (= app.db `llm_calls.request.turn` / `tool_calls.message_id`), `ocean.conversation_hash` and `session.id` (HMAC-SHA256[:16] of the conversation id, keyed from SESSION_SECRET), `ocean.reasoning_effort`, `ocean.outcome` (completed / fallback / cancelled / failed), `ocean.error_code`, `ocean.fallback_kind`, `ocean.budget.{steps,tool_calls,refused_calls,tokens,estimated_calls}`; events `llm.attempt_failed` {attempt, error type, retryable, released} |
| `invoke_agent ocean-above` | Pydantic AI | `agent.turn` | GenAI semconv, aggregated usage; message *structure* only (roles, part types, tool names). Phoenix shows it as AGENT. A retried step restarts the run, so a retry shows up as a second `invoke_agent`. |
| `chat <model>` | Pydantic AI | `invoke_agent` | model, provider, `gen_ai.usage.*`, finish reasons, time to first chunk, tool definitions (our static schemas). Phoenix shows it as LLM. |
| `execute_tool <name>` | Pydantic AI | `invoke_agent` | tool name, call id + ours: `ocean.tool.code`, `ocean.tool.cards`, `ocean.tool.refused` (budget). Phoenix shows it as TOOL. |
| `airline.quote_route` | `Airline.quote_route` | `execute_tool quote_route` | `ocean.result.code`, `ocean.quote.legs` |
| `core.routing` | `routing.cheapest_route` | `airline.quote_route` or `booking.recheck` | `ocean.routing.algorithm` (`hub-candidates+enumeration`), `ocean.network.fingerprint`, `ocean.routing.candidates`, `ocean.routing.code`, `ocean.routing.legs` |
| `reply_guard` | `Agent._release` (each released sentence) | `chat` / `invoke_agent` | `ocean.guard.chars`, `ocean.guard.result` (pass / fail), `ocean.guard.problems` (kinds only: number, city, date, time, id, booking_claim). Phoenix shows it as GUARDRAIL. |
| `run.stop` | `Agent._log_stopped` | `agent.turn` | starts at the accepted Stop and ends when the stopped reply is stored; `ocean.stop.{stored, stop_to_end_ms, drain_ms, tokens_after_stop}`, `ocean.run_id` |
| `airline.book` | `Airline.book` / `book_async` | root (HTTP click) | `ocean.book.status` (booked or the refusal code) |
| `booking.recheck` | `Airline._recheck` (B6) | `airline.book` | `ocean.recheck.result` (same / changed), `ocean.recheck.reason` (fixed category before the colon, no city names), `ocean.network.fingerprint` |

- There is no separate `agent.llm` / `agent.tool` span. The SDK's `chat` / `execute_tool` spans cover them, so we add attributes and events instead of duplicates.
- Our own spans carry `openinference.span.kind`: CHAIN, and GUARDRAIL for `reply_guard`. Phoenix 22 maps the SDK's GenAI spans by itself, so `openinference-instrumentation-pydantic-ai` is **not** added.

**Ids that are exported** (review P2-8; none is user content): `ocean.run_id`, `ocean.message_id` (maps to one app.db row), `gen_ai.response.id` (the provider's completion id), `gen_ai.tool.call.id`, `gen_ai.agent.call.id`, `server.address` (the LLM host), and the keyed conversation hash. Tool names are exported only if they are ours; a name the model invented becomes `unknown_tool`.

## Privacy: method and results
- **By construction:**
  - SDK spans run with `include_content=False`.
  - Our attributes are codes, counts, durations, opaque ids (run id, message id) and the `locations.db` fingerprint. City names, text, nonces, user, conversation, quote and ticket ids are never set.
- **At the exporter (`tracing.Redacting`):**
  - An exception event keeps `exception.type` only; its message and stack trace are dropped.
  - A status keeps its code only; the OTel SDK would put `Type: message` in the description.
  - Content keys (`tool_arguments`, `tool_response`, `final_result`, `gen_ai.tool.call.arguments/result`, `gen_ai.system_instructions`) are dropped, even if content capture were ever switched on.
- **Tests:**
  - `test_privacy_scan_over_http_finds_no_sensitive_value` runs the whole HTTP flow: a Google-style user with an email, the cookie, a chat turn that quotes, and the confirm click with its nonce. It scans every span name, attribute, event, status and resource value. Needles:
    - the user's text (which contains an email), the email and its local part, the user's name, user id, provider sub, conversation id;
    - the cookie, the nonce and its body, the session and nonce secrets, the LLM key;
    - the reply text, quote id, ticket id, origin and destination city, price and distance (raw and as displayed).
  - Result: 0 hits.
  - `test_exception_messages_and_status_descriptions_are_redacted`: a failure whose message holds an email and a key is exported as the type `RuntimeError` only.
  - `test_content_keys_are_dropped_even_if_content_capture_were_on`.
- **Real run:** `/tmp/oa-otel/scan_phoenix.py` read every span Phoenix stored for the real-model evals project (682 spans) through its REST API. It compared them against the 120 user texts and replies in `results.json` (whole text, and every 20-character window) and against the 39 quote, ticket, user, conversation and nonce ids.
  - Full-text hits: 0. Id hits: 0.
  - 7 window hits, all in `gen_ai.tool.definitions` / `llm.tools.*.json_schema`. These are our own static tool descriptions, which template replies quote too.
  - Output: `evals/privacy-scan-real.json`.

## Failure-mode tests (`backend/tests/test_tracing.py`)
| Mode | Test | Result |
|---|---|---|
| Off by default | `test_off_builds_no_provider_imports_no_sdk_and_starts_no_thread` (a fresh process runs startup, a chat turn, the booking route and shutdown) | provider None; no `opentelemetry.sdk*` or `exporter*` module imported; no export thread; SDK runtime without `Instrumentation` |
| Exporter raises on every export | `test_an_exporter_that_raises_changes_no_chat_or_booking_result` | same events, text, price, legs, ticket and ticket count as with tracing off |
| Collector down (real OTLP exporter to a closed port) | `test_a_collector_that_is_down_changes_no_chat_or_booking_result` | same results; turn + booking < 3 s; shutdown within the export timeout (5 s) + margin |
| Tracing layer itself broken (`get_tracer` raises) | `test_a_broken_tracing_layer_never_reaches_the_turn` | turn completes, card bookable |
| Stop | `test_stop_shows_run_stop_under_a_cancelled_root` | `run.stop` child of a `cancelled` root, with its timings |
| Guard fallback / LLM down | `test_a_guard_fallback_…`, `test_a_failed_llm_…` | `fallback` + `number`; `failed` + `llm_unavailable` + 3 attempt events |
| `locations.db` read past its deadline | `test_a_locations_read_past_its_deadline_is_a_data_error_not_a_hang` | `NetworkDataError("… interrupted")` |
| Non-local endpoint | `test_a_non_local_endpoint_…` | warning logged; still allowed, because a compose service name or private IP is local |

Export is batched (queue 2048, newest dropped when full) in the SDK's thread. Nothing on the request path waits for it. App shutdown waits at most `SHUTDOWN_TIMEOUT_S` (5 s) for the last spans, then drops them (review P2-1; the SDK alone waits one 5 s export timeout per batch, 20 s for 2000 queued spans).

## Dependencies (named in the plan)
- `opentelemetry-sdk>=1.45,<2` and `opentelemetry-exporter-otlp-proto-http>=1.45,<2` (resolved 1.45.2).
- `opentelemetry-api` was already in the lock through pydantic-ai-slim.
- 12 new locked packages: `opentelemetry-{sdk, semantic-conventions, proto, exporter-otlp-proto-http, exporter-otlp-proto-common, exporter-otlp-common, exporter-http-transport}`, `protobuf`, `googleapis-common-protos`, `requests`, `urllib3`, `charset-normalizer`.
- Unpacked in the venv: about 8 MB.
  - protobuf 1.6 MB, charset-normalizer 1.3 MB, semconv 1.1 MB, urllib3 0.9 MB, otel sdk 0.85 MB, googleapis protos about 1 MB, requests 0.5 MB, otel proto 0.35 MB, exporter 0.3 MB.
- Import cost only when tracing is on: about 15–35 ms (`-X importtime`). Off: none, because nothing is imported.
- `openinference-instrumentation-pydantic-ai` was not added: Phoenix 22 renders the GenAI spans as AGENT / LLM / TOOL without it.

## Evals (in `docs/iterations/10-otel/evals/`)
- **fake-traced:** 31/31, 0 safety violations, tracing on to a local Phoenix (project `ocean-above-evals-fake`).
- **real-traced:**
  - Setup: deepseek-flash, effort high, N=1, `--max-cost 0.25`, tracing on to a local Phoenix (project `ocean-above-evals-real`), commit `4b73169`.
  - Results: 30/31 (96.8%), 0 safety violations, 0% ReplyGuard fallback, **$0.0223**, LLM p50 1050 ms, turn p50 2.14 s.
  - The one failure, `quote_details`: the model also called `request_booking` without being asked. That is model variance, not tracing (08 had 30/31 too).
- **Fake run with tracing off:** 31/31.

## Screenshots (Phoenix 22.0.1, local, ego-browser, 1440 wide)
- `screenshots/phoenix-quote-real-1440.png`: a real DeepSeek quoted itinerary: `agent.turn` → `invoke_agent` → `chat deepseek-flash` → `execute_tool quote_route` → `airline.quote_route` → `core.routing` (attributes shown), then `chat` and the `reply_guard` spans.
- `screenshots/phoenix-stopped-turn-1440.png`: a stopped turn (fake LLM): `agent.turn` → `invoke_agent` → `chat` → `reply_guard`, and `run.stop` (stop_to_end 2 ms, drain 0 ms, stored true).
- Phoenix UI only, so no mobile shot: it is a developer tool and the app UI is unchanged.

## How to verify
```bash
cd backend
HYPOTHESIS_PROFILE=ci nice -n 10 uv run pytest -q tests/test_tracing.py   # 25 passed
cd .. && bash tool/test_diff.sh && python3 tool/review_lint.py
# local Phoenix without docker (not a project dependency):
uv venv /tmp/oa-phoenix/.venv && uv pip install --python /tmp/oa-phoenix/.venv/bin/python arize-phoenix
PHOENIX_WORKING_DIR=/tmp/oa-phoenix/data /tmp/oa-phoenix/.venv/bin/phoenix serve   # UI + OTLP on :6006
cd backend && OTEL_TRACING_ENABLED=1 OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:6006 NO_PROXY=127.0.0.1,localhost \
  uv run python -m evals --mode fake
```
Tested source: commit `4b73169` for the code, plus this docs commit. Checks: `tool/test_diff.sh` "No new failures", lint ok, 902 + 25 backend tests.

## Draft: design.md §4.11 观测（OpenTelemetry，#35）
> 可选，默认关。`OTEL_TRACING_ENABLED=1` 时 `app/tracing.py` 建一个 TracerProvider，BatchSpanProcessor + OTLP/HTTP exporter（端点取标准变量 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `OTEL_EXPORTER_OTLP_ENDPOINT`，默认 `http://localhost:4318`；非本地地址启动时告警）。关时不导入 OTel SDK，没有 provider、exporter 和后台线程，span 调用是共享的空操作。
>
> **Trace 结构**：每轮对话一个 trace，根 `agent.turn`（run_id = SSE `X-Run-Id`，message_id = app.db 记录，会话 id 的哈希，结果 completed / fallback / cancelled / failed，预算用量）。其下是 Pydantic AI 自己的 `invoke_agent` / `chat` / `execute_tool`（GenAI 语义约定，`include_content=False`），我们只补 SDK 没有的：`airline.quote_route` → `core.routing`（算法、locations.db 指纹、候选数、腿数）、`reply_guard`（每句放行的检查，pass/fail + 问题类别）、`run.stop`（从接受 Stop 到停止的回复落库，含 drain 时间）。工具结果码作为属性加在 SDK 的 `execute_tool` 上，LLM 失败重试作为 `agent.turn` 的事件，不另起重复 span。出票点击是独立的 HTTP 请求，自成 trace：`airline.book`（只有状态）→ `booking.recheck`（B6 结果类别、指纹）→ `core.routing`。
>
> **隐私**：span 里只有代码、计数、时长、不透明 id 和指纹（导出的 id：run_id、app.db 的 message_id、供应商的 completion id、tool call id、SDK 的 agent call id、LLM 主机名；会话 id 只以带服务端密钥的 HMAC 出现，可关联、不可反推、无密钥不可核对；模型编造的工具名替换为 `unknown_tool`）；没有用户或模型的文字、prompt、工具参数和结果、城市名、邮箱、用户名、nonce、cookie、密钥。exporter 前一层 `Redacting` 再兜底：异常只留类型（去掉消息和堆栈），status 只留 code，内容类属性键一律删除。生产（VPS）保持关闭，隐私页无需改动；开启时只导出到本机或私网的 collector。
>
> **失败模式**：导出在 SDK 的后台线程里批量进行，队列满了就丢 span；collector 宕机、超时（单次 5 s）或 exporter 抛异常都只丢 span，对话和出票的结果与时延不变（测试：exporter 抛异常、端口关闭、collector 挂起、span 的每个方法都抛异常）。tracing 层自身的错误（包括属性值的计算）被守护层吞掉，不会进入业务代码；一轮结束时先退场 run、结束事件流，最后才写 trace 属性。关停时最多等 5 s，之后剩下的 span 丢弃。本地端点（localhost、私网地址、compose 服务名）一律直连，不走环境里的 HTTP(S)_PROXY / ALL_PROXY；非本地端点告警。
>
> **Phoenix**：`docker compose --profile tracing up` 起 `arizephoenix/phoenix:version-22.0.1`（`PHOENIX_TELEMETRY_ENABLED=false`），UI 只绑 loopback `http://localhost:6006`，数据在 `phoenix` 卷里；普通 `docker compose up` 不启动它。Phoenix 22 能直接把 GenAI span 识别为 AGENT / LLM / TOOL；我们自己的 span 带 `openinference.span.kind`（CHAIN / GUARDRAIL）。
>
> **locations.db 读取期限**（#35 评论）：`_read_rows` 的 SQL 由进度回调 5 s 截止（同 app.db），超时抛 `NetworkDataError`；等锁仍由 5 s busy timeout 限制（原本如此）。文件系统卡在一次读调用里、以及读前后的哈希和 stat 不在期限内。出票时对应 `data_unavailable`，可重试，quote 不会因此变 stale。

## Draft: README "Tracing" section
```markdown
## Tracing (optional, off by default)

The API can export OpenTelemetry traces of each chat turn to a local [Phoenix](https://github.com/Arize-ai/phoenix):
the agent run, every model call (tokens, latency) and tool call, the route search, the reply check, a user's Stop and
the booking re-check. Spans carry codes, counts, durations and ids only — never message text, prompts, tool results,
city names, emails, nonces, cookies or keys.

With Docker:

    # .env
    OTEL_TRACING_ENABLED=1
    OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006

    docker compose --profile tracing up -d
    # chat at http://localhost:3000, traces at http://localhost:6006

A plain `docker compose up` does not start Phoenix, and without `OTEL_TRACING_ENABLED=1` nothing is traced or exported.
Running the backend on the host instead: start Phoenix (`docker compose --profile tracing up -d phoenix`, or
`pip install arize-phoenix && phoenix serve`) and set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006`. Evals
(`python -m evals`) honour the same variables. If the collector is down, spans are dropped; chat and booking are
unaffected.
```

## Review findings → fixes → tests (round 2, Fable review `review-otel.md`)
| Finding | Fix | Test (mutation it kills) |
|---|---|---|
| P1-1 HTTP_PROXY / ALL_PROXY sent "local" spans through the proxy | local endpoint: exporter gets a `requests.Session` with `trust_env=False`; non-local keeps env proxies + warning; `.env.example` comment corrected | `test_p1_1_a_local_collector_is_reached_directly_whatever_proxy_the_environment_names[HTTP_PROXY/ALL_PROXY/HTTPS_PROXY/http_proxy]` (fake proxy + fake collector sockets); `test_p1_1_only_a_local_collector_ignores_the_environment_proxy` (M20) |
| P2-1 shutdown took 20 s with a hung collector | `tracing.shutdown(timeout=5)`: provider shutdown in a daemon thread, bounded join, drops the rest with a warning | `test_p2_1_shutdown_returns_within_its_bound_when_the_collector_hangs` (2000 spans, hung socket, < 2 s with timeout 1), `…healthy_collector_finishes_and_flushes` (M21) |
| P2-2 `_trace_end` before `_retire` / end of stream; attribute computations outside the guard | `_run_traced`'s `finally` retires the run and ends the stream first, then `span.set_attributes_from(lambda: _trace_end(run, turn))`; new `set_attributes_from` guards the computation too (off: not even computed); used for every computed attribute (tool, guard, quote, routing, recheck, turn end, conversation hash); `current()` fully guarded | `test_p2_2_a_failing_trace_end_keeps_the_terminal_event_retirement_lock_and_slots` (M17: hung before), `test_p2_2_a_failing_attribute_computation_changes_no_booking_result` (`_reason_kind` raising inside the booking transaction; M19) |
| P2-3 mutants M1, M2, M6, M10, M15, M18 survived | tests below | probe A as `test_p2_3_every_span_method_raising_changes_no_result[all, all-but-is_recording]` (quote, wrong nonce, `book_async`, LLM failure with its error code, guard fallback, Stop; M1, M2, M18, M25); `test_p2_3_an_exception_through_our_own_span_is_exported_with_its_type_only` (NotFound through `airline.quote_route`; M6); `test_p2_3_the_http_click_records_its_status_through_book_async` (M15); `test_p2_3_a_cancellation_through_our_span_ends_it_and_restores_the_context` |
| P2-4 invented tool names exported | `Redacting`: `gen_ai.tool.name` and tool-call parts in the message-structure JSON outside our tool list → `unknown_tool` (unparsable JSON: key dropped) | `test_p2_4_a_tool_name_the_model_invented_is_exported_as_unknown_tool` (M22) |
| P2-5 conversation hash unkeyed | `digest` = HMAC-SHA256 with a key derived from SESSION_SECRET (`set_hash_key` at install; random per process otherwise); only computed when tracing is on | `test_p2_5_the_conversation_hash_is_keyed` (M23) |
| P2-6 deadline described too strongly | docstring, STATE open decision 3 and design draft reworded; test renamed `test_sql_on_locations_db_past_its_deadline_is_a_data_error`. No code change (a real bound needs the whole load in a timed thread: follow-up) | — |
| P2-7 cancelled click had no status | `_book_status`: any other BaseException → `interrupted`, re-raised | `test_p2_7_a_cancelled_click_is_recorded_as_interrupted` (click waiting for a running turn's lock, cancelled; no ticket; M24) |
| P2-8 PR said "no ids" | exported ids listed above, in the design draft and the PR body | — |
| P2-9 Phoenix telemetry | checked in Phoenix 22.0.1 (`phoenix/config.py`: `PHOENIX_TELEMETRY_ENABLED`, default true); compose sets it to `"false"` | — (compose) |

**Mutation check** (`mutate.py`, results in `mutation-results.txt`, only `tests/test_tracing.py` run on a copy): 16 mutants,
15 killed. Survivor **M10** (`span()` catching `Exception` only) is equivalent on CPython: when a `BaseException` such as
`CancelledError` leaves the body, our generator is finalized at once and the OTel `start_as_current_span` generator gets
`GeneratorExit`, which ends the span and restores the context the same way, and exports the same status and events
(probe `/tmp/oa-otel/m10.py`: identical output). The `BaseException` clause stays as the explicit form; the test pins the
observable behaviour (span ended, context restored in the same task).

**After merging origin/main (#57, `_run` busy branches give the slot back):** the merge applied #57's branches inside
`_run_traced` unchanged (`ConversationBusy` / `_UserBusy` → `not_stored()`); no conflict markers. Full suite 955 passed,
`test_diff` no new failures, lint ok, fake evals 31/31.

**Real-model evals after the fixes** (`evals/real-traced-r2/`, commit `5ee6e74`, tracing on to local Phoenix, through the
proxy environment, which now no longer catches the local collector): 28/31 (90.3%, threshold 85%), **0 safety
violations**, 0% fallback, $0.0228. The three failures are trajectory variance seen before (`quote_details`,
`zh_book_in_one_message` and `false_booked_prompt` all failed in earlier reports, e.g. 08 `real-after-review`): an extra
`request_booking`, and a first `quote_route` with Chinese city names. Tracing does not change the wire (tested).
Privacy scan of the 691 stored spans: 0 full-text hits, 0 id hits, 3 window hits in our static tool descriptions
(`evals/privacy-scan-real-r2.json`).

## Next
Coordinator: review the diff and the privacy scan, run the compose config check on the homelab, merge `design.md`
§4.11 and the README section, and decide on the Fable review (open decision 1). Then release with tracing off and
verify health and one quote.
