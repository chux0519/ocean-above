"""Per-turn budgets of the agent loop (design §4.8): LLM steps, tool calls, tokens, wall time.

`AgentBudget` is the configuration (defaults below, env overrides validated by `from_env`).
`BudgetTracker` is one turn's tally. The loop asks it before every LLM step (`stop_before_step`)
and before every tool call (`admit_tool_call`); it never executes anything itself.

Token accounting is a **soft** cap and not a dollar cap:
- the provider's `usage.total_tokens` of a call is only known after the call, so the check runs
  between LLM requests: a turn that crosses the cap finishes the call in flight and starts no
  further step;
- a call (failed or completed) that reports no usage is charged a conservative estimate:
  `ceil(request characters / CHARS_PER_TOKEN_ESTIMATE) + max_output_tokens_per_call`
  (every character of the request counted as half a token, plus the whole output allowance);
- retries are separate calls and are charged too.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, fields
from typing import Any, Mapping

CHARS_PER_TOKEN_ESTIMATE = 2  # conservative: English text and JSON average about 3-4 characters per token

# name -> (environment variable, lowest, highest). Bounds keep a typo from removing a budget
# (0 or a huge number) or from making every turn fail.
_BOUNDS: dict[str, tuple[str, float, float]] = {
    "max_steps": ("AGENT_MAX_STEPS", 1, 20),
    "max_tool_calls_per_step": ("AGENT_MAX_TOOL_CALLS_PER_STEP", 1, 16),
    "max_tool_calls_per_turn": ("AGENT_MAX_TOOL_CALLS_PER_TURN", 1, 64),
    "max_output_tokens_per_call": ("AGENT_MAX_OUTPUT_TOKENS_PER_CALL", 256, 32_768),
    "max_total_tokens_per_turn": ("AGENT_MAX_TOTAL_TOKENS_PER_TURN", 1_000, 1_000_000),
    "max_turn_seconds": ("AGENT_MAX_TURN_SECONDS", 5, 600),
}


@dataclass(frozen=True)
class AgentBudget:
    max_steps: int = 6  # LLM steps (calls that complete, not counting retries) per turn
    max_tool_calls_per_step: int = 4  # executed tool calls of one completion
    max_tool_calls_per_turn: int = 12  # executed tool calls of the whole turn
    max_output_tokens_per_call: int = 4096  # the request's max_tokens (includes thinking tokens)
    # soft: provider-reported total tokens of all calls. 60k, not the first 20k: every step re-sends
    # the history, and in a 19-turn eval conversation a 3-step turn already had ~18k after step 2
    # (P1 #34, docs/iterations/06-hardening/P1/STATE.md); 60k leaves 4 steps at full history.
    max_total_tokens_per_turn: int = 60_000
    max_turn_seconds: float = 90.0  # the whole turn: LLM calls, tools, retries

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            whole = f.name != "max_turn_seconds"
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or (whole and not isinstance(value, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{f.name} must be a positive {'integer' if whole else 'number'}, got {value!r}")

    def checked(self) -> "AgentBudget":
        """The same budget, also within the bounds the environment may set (startup check)."""
        for name, (var, low, high) in _BOUNDS.items():
            value = getattr(self, name)
            if not low <= value <= high:
                raise ValueError(f"{var} must be between {low:g} and {high:g}, got {value:g}")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AgentBudget":
        """Defaults, overridden by the `AGENT_*` variables of `_BOUNDS`. A value that is not a
        number or is out of bounds raises ValueError (the app does not start with a bad budget)."""
        env = os.environ if env is None else env
        values: dict[str, Any] = {}
        for name, (var, low, high) in _BOUNDS.items():
            raw = (env.get(var) or "").strip().replace("_", "")
            if not raw:
                continue
            try:
                value = float(raw) if name == "max_turn_seconds" else int(raw)
            except ValueError:
                raise ValueError(f"{var} must be {'a number' if name == 'max_turn_seconds' else 'an integer'}, "
                                 f"got {raw!r}") from None
            if not low <= value <= high:  # also catches nan
                raise ValueError(f"{var} must be between {low:g} and {high:g}, got {raw!r}")
            values[name] = value
        return cls(**values).checked()


def estimate_tokens(messages: Any, max_output_tokens: int) -> int:
    """Conservative charge for a call without reported usage (see the module docstring)."""
    chars = len(json.dumps(messages, ensure_ascii=False, separators=(",", ":"), default=str))
    return math.ceil(chars / CHARS_PER_TOKEN_ESTIMATE) + max_output_tokens


def reported_tokens(usage: Any) -> int | None:
    """`usage.total_tokens` when the provider reported a usable one."""
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return None
    return total


class BudgetTracker:
    """One turn's use of an `AgentBudget`. Not thread-safe; owned by the turn's task."""

    def __init__(self, budget: AgentBudget):
        self.budget = budget
        self.steps = 0  # LLM steps started
        self.tool_calls = 0  # tool calls executed (refused ones not counted)
        self.refused_calls = 0
        self.tokens = 0  # reported + estimated
        self.estimated_calls = 0  # calls charged by estimate
        self._step_calls = 0

    # ---------------------------------------------------------------- LLM steps ------------
    def stop_before_step(self) -> dict | None:
        """None when another LLM step may start (and counts it), else the fallback problem."""
        if self.steps >= self.budget.max_steps:
            return {"kind": "max_steps", "value": str(self.budget.max_steps)}
        if self.tokens >= self.budget.max_total_tokens_per_turn:
            return {"kind": "token_budget", "value": str(self.budget.max_total_tokens_per_turn)}
        self.steps += 1
        self._step_calls = 0
        return None

    def charge(self, usage: Any, request_messages: Any) -> tuple[int, bool]:
        """Count one LLM call (completed, failed or retried). Returns (tokens charged, estimated)."""
        total = reported_tokens(usage)
        if total is None:
            total = estimate_tokens(request_messages, self.budget.max_output_tokens_per_call)
            self.estimated_calls += 1
            self.tokens += total
            return total, True
        self.tokens += total
        return total, False

    # ---------------------------------------------------------------- tool calls -----------
    def admit_tool_call(self) -> str | None:
        """None when one more tool call of the current step may run (and counts it), else why
        not: "per_step" or "per_turn". A refused call is never executed."""
        if self.tool_calls >= self.budget.max_tool_calls_per_turn:
            reason = "per_turn"
        elif self._step_calls >= self.budget.max_tool_calls_per_step:
            reason = "per_step"
        else:
            self.tool_calls += 1
            self._step_calls += 1
            return None
        self.refused_calls += 1
        return reason

    @property
    def tool_calls_left(self) -> int:
        return self.budget.max_tool_calls_per_turn - self.tool_calls

    def snapshot(self) -> dict:
        """For logs and the llm_calls record (no secrets, small)."""
        return {"steps": self.steps, "tool_calls": self.tool_calls, "refused_calls": self.refused_calls,
                "tokens": self.tokens, "estimated_calls": self.estimated_calls}


def refusal(reason: str, budget: AgentBudget) -> dict:
    """The tool result of a call that was not executed (the LLM protocol needs one per call)."""
    if reason == "per_step":
        message = (f"Not executed: at most {budget.max_tool_calls_per_step} tool calls are run per step. "
                   "Call the remaining tools again in a later step if they are still needed.")
    else:
        message = (f"Not executed: this turn has used all {budget.max_tool_calls_per_turn} tool calls. "
                   "Answer with the results you already have.")
    return {"code": "tool_budget_exceeded", "message": message, "limit": reason}
