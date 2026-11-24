"""The versioned price registry (`pricing.json`) and token usage in one provider-neutral shape.

A price is per provider + model: the same model can cost differently at another provider, or be on
a plan whose price is not known. An unknown price gives cost None ("unknown"), never $0 and never
another provider's rate; token usage is reported either way (#47)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

REGISTRY = Path(__file__).with_name("pricing.json")
USAGE_KEYS = ("input", "cached", "output", "reasoning")  # input includes cached; output includes reasoning


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    input_cache_hit: float
    input_cache_miss: float
    output: float

    def cost(self, usage: Mapping[str, int]) -> float:
        cached = usage.get("cached", 0)
        return (cached * self.input_cache_hit + (usage.get("input", 0) - cached) * self.input_cache_miss
                + usage.get("output", 0) * self.output) / 1e6


@dataclass(frozen=True)
class Pricing:
    version: str
    entries: dict[tuple[str, str], dict[str, Any]]

    def price(self, provider: str, model: str) -> Price | None:
        """None when the registry does not know the price (or says it is unknown)."""
        entry = self.entries.get((provider, model))
        if not entry or entry.get("price") == "unknown":
            return None
        return Price(float(entry["input_cache_hit"]), float(entry["input_cache_miss"]), float(entry["output"]))

    def basis(self, provider: str, model: str) -> str:
        entry = self.entries.get((provider, model))
        return entry.get("basis", "") if entry else "not in the registry"


def load(path: Path = REGISTRY) -> Pricing:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = {}
    for e in data["prices"]:
        key = (e["provider"], e["model"])
        if key in entries:
            raise ValueError(f"duplicate price for {key}")
        if e.get("price") != "unknown":
            for field in ("input_cache_hit", "input_cache_miss", "output"):
                value = e[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise ValueError(f"{key}: {field} must be a non-negative number")
        entries[key] = e
    return Pricing(str(data["version"]), entries)


def cost(price: Price | None, usage: Mapping[str, int]) -> float | None:
    return None if price is None else price.cost(usage)


def normalize_usage(raw: Mapping[str, Any] | None) -> dict[str, int]:
    """A provider's usage block (as `llm_calls` stores it) as input / cached / output / reasoning.
    Cached input: DeepSeek's `prompt_cache_hit_tokens`, else the OpenAI-style
    `prompt_tokens_details.cached_tokens` (Bailian)."""
    raw = raw or {}

    def num(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    cached = raw.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens")
    return {"input": num(raw.get("prompt_tokens")), "cached": num(cached), "output": num(raw.get("completion_tokens")),
            "reasoning": num((raw.get("completion_tokens_details") or {}).get("reasoning_tokens"))}
