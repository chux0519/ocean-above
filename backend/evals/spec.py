"""Benchmark experiment specs (`evals/experiments/*.json`, #47): which configurations play the same
cases, how often and in which order.

A configuration names its `provider` and `model` separately (the same model at two providers is two
configurations) and its credentials by environment variable name only (`api_key_env`,
`base_url_env`); values are read when the benchmark starts and never written anywhere. The
`dialect` says which thinking fields the endpoint takes (app/agent/model.py); a thinking level the
dialect cannot express makes the configuration `unsupported`: it is reported, not run."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from app.agent.config import DEFAULT_BASE_URL, DIALECT_NAMES, EFFORTS, REPO_ROOT, LLMConfig, read_env_file
from app.agent.model import MAX_TOKENS, UnsupportedSetting, thinking_fields
from app.agent.prompt import SYSTEM_PROMPT

EXPERIMENTS = Path(__file__).with_name("experiments")
# provider -> default base URL (None: the spec must name `base_url_env`) and default dialect
PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {"base_url": DEFAULT_BASE_URL, "dialect": "deepseek"},
    "bailian": {"base_url": None, "dialect": "bailian"},
}


class SpecError(ValueError):
    """The spec is malformed or does not match the code it is run with."""


@dataclass(frozen=True)
class ConfigSpec:
    id: str
    provider: str
    model: str
    api_key_env: str
    base_url_env: str | None = None
    dialect: str | None = None  # None: the provider's
    reasoning_effort: str = "high"
    thinking_budget: int | None = None
    temperature: float = 0.0
    max_tokens: int = MAX_TOKENS
    concurrency: int = 4

    @property
    def effective_dialect(self) -> str:
        return self.dialect or PROVIDERS[self.provider]["dialect"]

    def unsupported(self) -> str | None:
        """Why this configuration cannot be asked for, or None."""
        try:
            thinking_fields(self.effective_dialect, self.reasoning_effort, self.thinking_budget)
        except UnsupportedSetting as exc:
            return str(exc)
        return None

    def public(self) -> dict[str, Any]:
        """The configuration as reports show it: names of variables, never their values."""
        return {f.name: getattr(self, f.name) for f in fields(self)} | {"dialect": self.effective_dialect}


@dataclass(frozen=True)
class Spec:
    name: str
    version: int
    configs: tuple[ConfigSpec, ...]
    n: int = 3
    interleave: bool = True
    cases: tuple[str, ...] = ()  # case names; () = every case
    dataset_version: str = "auto"
    prompt_version: str = "auto"
    pricing_version: str = ""
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise SpecError(message)


def parse(data: Mapping[str, Any]) -> Spec:
    _check(isinstance(data, Mapping), "a spec is a JSON object")
    known = {f.name for f in fields(Spec)} - {"configs", "extra"}
    unknown = set(data) - known - {"configs"}
    _check(not unknown, f"unknown spec fields: {sorted(unknown)}")
    configs = []
    for raw in data.get("configs") or []:
        _check(isinstance(raw, Mapping), "a configuration is a JSON object")
        allowed = {f.name for f in fields(ConfigSpec)}
        _check(not set(raw) - allowed, f"unknown configuration fields: {sorted(set(raw) - allowed)}")
        try:
            c = ConfigSpec(**raw)
        except TypeError as exc:
            raise SpecError(f"configuration {raw.get('id')!r}: {exc}") from None
        _check(bool(c.id) and c.id.replace("-", "").replace("_", "").replace(".", "").isalnum(),
               f"configuration id {c.id!r}: letters, digits, - _ . only")
        _check(c.provider in PROVIDERS, f"{c.id}: unknown provider {c.provider!r} (known: {sorted(PROVIDERS)})")
        _check(c.dialect is None or c.dialect in DIALECT_NAMES, f"{c.id}: unknown dialect {c.dialect!r}")
        _check(c.reasoning_effort in (*EFFORTS, "omit"), f"{c.id}: unknown reasoning_effort {c.reasoning_effort!r}")
        _check(_env_name(c.api_key_env), f"{c.id}: api_key_env must be an environment variable name")
        _check(c.base_url_env is None or _env_name(c.base_url_env),
               f"{c.id}: base_url_env must be an environment variable name")
        _check(c.base_url_env is not None or PROVIDERS[c.provider]["base_url"] is not None,
               f"{c.id}: provider {c.provider} has no default base URL; name base_url_env")
        _check(isinstance(c.concurrency, int) and 1 <= c.concurrency <= 16, f"{c.id}: concurrency 1..16")
        _check(isinstance(c.max_tokens, int) and 256 <= c.max_tokens <= 32_768, f"{c.id}: max_tokens 256..32768")
        _check(isinstance(c.temperature, (int, float)) and 0 <= c.temperature <= 2, f"{c.id}: temperature 0..2")
        configs.append(c)
    _check(bool(configs), "a spec needs at least one configuration")
    ids = [c.id for c in configs]
    _check(len(set(ids)) == len(ids), "configuration ids must be unique")
    n = data.get("n", 3)
    _check(isinstance(n, int) and 1 <= n <= 50, "n: 1..50")
    return Spec(name=str(data.get("name") or "unnamed"), version=int(data.get("version", 1)), configs=tuple(configs),
                n=n, interleave=bool(data.get("interleave", True)), cases=tuple(data.get("cases") or ()),
                dataset_version=str(data.get("dataset_version", "auto")),
                prompt_version=str(data.get("prompt_version", "auto")),
                pricing_version=str(data.get("pricing_version", "")), description=str(data.get("description", "")))


def _env_name(name: Any) -> bool:
    return isinstance(name, str) and bool(name) and name.replace("_", "").isalnum() and name.upper() == name


def load(path: str | Path) -> Spec:
    path = Path(path)
    if not path.exists() and (EXPERIMENTS / path).exists():
        path = EXPERIMENTS / path
    try:
        return parse(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path}: {exc}") from None


# ------------------------------------------------------------------ versions ------------------
def _canonical(value: Any) -> Any:
    """A stable, hash-friendly form of a case (scripts of the fake model are left out: they are CI
    plumbing, not the dataset)."""
    if is_dataclass(value) and not isinstance(value, type):
        return {type(value).__name__: {f.name: _canonical(getattr(value, f.name)) for f in fields(value)
                                       if f.name != "fake"}}
    if isinstance(value, (frozenset, set)):
        return sorted(_canonical(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items())}
    return value


def digest(value: Any) -> str:
    text = json.dumps(_canonical(value), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def dataset_version(cases: Any) -> str:
    return digest(list(cases))


def prompt_version() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]


def check_versions(spec: Spec, cases: Any, pricing_version: str) -> dict[str, str]:
    """The versions this run uses; a spec that pins another one is refused."""
    actual = {"dataset_version": dataset_version(cases), "prompt_version": prompt_version(),
              "pricing_version": pricing_version}
    for key, value in actual.items():
        pinned = getattr(spec, key)
        _check(pinned in ("auto", "", value), f"spec pins {key} {pinned}, the code has {value}")
    return actual


# ------------------------------------------------------------------ credentials ---------------
def environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The process environment over the file named by OCEAN_ENV_FILE (default: the repository's .env)."""
    env = dict(os.environ if env is None else env)
    return {**read_env_file(env.get("OCEAN_ENV_FILE") or REPO_ROOT / ".env"), **{k: v for k, v in env.items() if v}}


def llm_config(c: ConfigSpec, env: Mapping[str, str]) -> LLMConfig | None:
    """The configuration's LLMConfig with its credentials from `env`; None when a variable is unset."""
    key = (env.get(c.api_key_env) or "").strip()
    base = (env.get(c.base_url_env) or "").strip() if c.base_url_env else PROVIDERS[c.provider]["base_url"]
    if not key or not base:
        return None
    return LLMConfig(api_key=key, base_url=base.rstrip("/"), model=c.model, reasoning_effort=c.reasoning_effort,
                     dialect=c.effective_dialect, thinking_budget=c.thinking_budget)
