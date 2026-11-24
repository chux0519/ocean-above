"""LLM settings from the environment. The key is read here and nowhere else; it is never logged
and never part of a repr."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

DEFAULT_BASE_URL = "https://api.deepseek.com"
# DeepSeek's flash model id on 2026-11-23 (GET /models: "deepseek-flash" = DeepSeek-V4.1-Flash).
DEFAULT_MODEL = "deepseek-flash"
REPO_ROOT = Path(__file__).resolve().parents[3]
EFFORTS = ("none", "low", "high", "max")  # what a request may ask for
# How an endpoint spells the thinking switch (app/agent/model.py `DIALECTS`): "deepseek" (the
# production default), "bailian" (Alibaba Cloud Bailian's OpenAI-compatible endpoint), "plain"
# (no thinking fields at all).
DIALECT_NAMES = ("deepseek", "bailian", "plain")


class LLMNotConfigured(Exception):
    """No API key in the environment or the env file."""


def read_env_file(path: str | Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env file (comments, blank lines and `export ` are skipped)."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key.strip()] = value
    return out


@dataclass(frozen=True)
class LLMConfig:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    # Thinking level (DeepSeek: `thinking` + `reasoning_effort`, docs "Thinking Mode"):
    #   "low" | "high" | "max" -> {"thinking": {"type": "enabled"}, "reasoning_effort": <level>}
    #   "none"                 -> {"thinking": {"type": "disabled"}}
    #   "omit"                 -> neither field, for OpenAI-compatible servers that reject them
    reasoning_effort: str = "high"
    # the request dialect of the endpoint (#47); production talks to DeepSeek
    dialect: str = "deepseek"
    # Bailian only: a cap on the reasoning tokens, sent instead of `reasoning_effort` (Qwen
    # rejects both together, and its `max_tokens` does not bound the reasoning)
    thinking_budget: int | None = None

    def __post_init__(self) -> None:
        if self.reasoning_effort not in (*EFFORTS, "omit"):
            raise ValueError(f"LLM_REASONING_EFFORT must be one of {', '.join(EFFORTS)} or omit")
        if self.dialect not in DIALECT_NAMES:
            raise ValueError(f"LLM_DIALECT must be one of {', '.join(DIALECT_NAMES)}")
        if self.thinking_budget is not None and (isinstance(self.thinking_budget, bool)
                                                 or not isinstance(self.thinking_budget, int)
                                                 or self.thinking_budget <= 0):
            raise ValueError("thinking_budget must be a positive integer")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMConfig":
        """`LLM_API_KEY` (fallback `DEEPSEEK_API_KEY`), `LLM_BASE_URL`, `LLM_MODEL`,
        `LLM_REASONING_EFFORT` (default high), `LLM_DIALECT` (default deepseek). Process environment first; then the file named by `OCEAN_ENV_FILE`
        (default: `.env` in the repository root), so a worktree can point at the main checkout."""
        env = os.environ if env is None else env
        file_values = read_env_file(env.get("OCEAN_ENV_FILE") or REPO_ROOT / ".env")

        def get(name: str) -> str:
            return (env.get(name) or file_values.get(name) or "").strip()

        key = get("LLM_API_KEY") or get("DEEPSEEK_API_KEY")
        if not key:
            raise LLMNotConfigured("set LLM_API_KEY (or DEEPSEEK_API_KEY), or OCEAN_ENV_FILE")
        return cls(
            api_key=key,
            base_url=(get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            model=get("LLM_MODEL") or DEFAULT_MODEL,
            reasoning_effort=(get("LLM_REASONING_EFFORT") or "high").lower(),
            dialect=(get("LLM_DIALECT") or "deepseek").lower(),
        )
