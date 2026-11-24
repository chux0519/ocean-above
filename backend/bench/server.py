"""The app under test: `python -m bench.server --work DIR --port N [--llm-delay S]`.

The real FastAPI app (`app.main.create_app`) with the bench fake LLM, one uvicorn worker, bound to
127.0.0.1. Measurement is added from outside the app code, in this process only:
- `app.store.db.set_timing_hook`: every `Store.read()` / `write()` (lock wait, hold, total,
  outcome), tagged with the calling function (`_book_transaction`, `add_llm_call`, ...);
- wrappers around `Airline._recheck`, `NetworkProvider.current` and `load_network` (graph rebuild);
- `/bench/*` routes (outside `/api`): reset, stats, and the locations.db mode for the recheck
  scenario ("race": the file changes after the booking's pre-load, so the rebuild happens inside
  the write transaction - the TOCTOU window the design accepts).
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import uvicorn
from fastapi import Request

from app.airline import service as airline_service
from app.api import Settings
from app.core import network as network_mod
from app.main import create_app
from app.store import db as store_db

from .common import NONCE_SECRET, SESSION_SECRET
from .fake_llm import BenchLLM

EXTRA_CITY = ("Bench Island", "1,1", 0, "")  # an unserved city: routes and prices do not change
_SKIP_FILES = (store_db.__file__, "contextlib.py", __file__)
_SKIP_NAMES = {"read", "write", "_report", "__exit__", "__enter__", "_on_store", "_site"}


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.store: dict[tuple, list] = defaultdict(list)  # (kind, site) -> [(wait, hold, total, outcome)]
            self.recheck: list[float] = []
            self.current: dict[str, list[float]] = defaultdict(list)  # strict / normal
            self.rebuild: list[float] = []
            self.rebuild_in_recheck = 0
            self.since = time.time()

    def stats(self) -> dict:
        with self.lock:
            return {
                "seconds": time.time() - self.since,
                "store": [{"kind": k, "site": s, "spans": v} for (k, s), v in self.store.items()],
                "recheck": list(self.recheck),
                "current": dict(self.current),
                "rebuild": list(self.rebuild),
                "rebuild_in_recheck": self.rebuild_in_recheck,
            }


REC = Recorder()
_local = threading.local()
MODE = {"race": False}


def _site() -> str:
    """The function that opened the transaction: a `Store` method (`add_llm_call`, ...) or an
    `Airline` method that uses `store.write()` directly (`_book_transaction`, `quote_route`, ...)."""
    frame = sys._getframe(2)
    while frame is not None and (frame.f_code.co_filename.endswith(_SKIP_FILES)
                                 and frame.f_code.co_name in _SKIP_NAMES):
        frame = frame.f_back
    return frame.f_code.co_name if frame is not None else "?"


def _on_store(t: store_db.OpTiming) -> None:
    site = _site()
    with REC.lock:
        REC.store[(t.kind, site)].append((t.wait, t.hold, t.total, t.outcome))


class Variants:
    """Two valid contents of locations.db with identical routes (B has one extra unserved city);
    `swap()` atomically replaces the live file with the other one."""

    def __init__(self, live: Path):
        self.live = live
        self.a = live.with_name("locations.variant-a.db")
        self.b = live.with_name("locations.variant-b.db")
        shutil.copyfile(live, self.a)
        shutil.copyfile(live, self.b)
        con = sqlite3.connect(self.b)
        con.execute("INSERT INTO locations VALUES (?, ?, ?, ?)", EXTRA_CITY)
        con.commit()
        con.close()
        self.next_b = True
        self.lock = threading.Lock()

    def swap(self) -> None:
        with self.lock:
            src = self.b if self.next_b else self.a
            self.next_b = not self.next_b
            tmp = self.live.with_name("locations.swap.db")
            shutil.copyfile(src, tmp)
            os.replace(tmp, self.live)


def instrument(variants: Variants) -> None:
    store_db.set_timing_hook(_on_store)

    real_recheck = airline_service.Airline._recheck

    def _recheck(self, quote):
        if MODE["race"]:
            variants.swap()  # after the pre-load, inside the write transaction (not timed)
        _local.in_recheck = True
        started = time.perf_counter()
        try:
            return real_recheck(self, quote)
        finally:
            elapsed = time.perf_counter() - started
            _local.in_recheck = False
            with REC.lock:
                REC.recheck.append(elapsed)

    airline_service.Airline._recheck = _recheck

    real_current = network_mod.NetworkProvider.current

    def current(self, strict: bool = False):
        started = time.perf_counter()
        try:
            return real_current(self, strict)
        finally:
            elapsed = time.perf_counter() - started
            with REC.lock:
                REC.current["strict" if strict else "normal"].append(elapsed)

    network_mod.NetworkProvider.current = current

    real_load = network_mod.load_network

    def load_network(path):
        started = time.perf_counter()
        try:
            return real_load(path)
        finally:
            elapsed = time.perf_counter() - started
            with REC.lock:
                REC.rebuild.append(elapsed)
                if getattr(_local, "in_recheck", False):
                    REC.rebuild_in_recheck += 1

    network_mod.load_network = load_network  # NetworkProvider.current looks it up as a module global


def build(work: Path, llm_delay: float):
    variants = Variants(work / "locations.db")
    instrument(variants)
    settings = Settings(
        app_db=work / "app.db", session_secret=SESSION_SECRET, nonce_secret=NONCE_SECRET,
        # the seeded users are "dev" users: their sessions need dev login switched on (no one logs in)
        dev_user="bench-dev", dev_password=secrets.token_hex(16),
        # the daily quotas are product limits, not database limits: out of the way for the bench
        daily_messages_per_user=10**9, daily_token_budget=10**15,
    )
    app = create_app(work / "locations.db", settings, llm=BenchLLM(llm_delay))

    @app.post("/bench/reset")
    def reset() -> dict:
        REC.reset()
        return {"ok": True}

    @app.get("/bench/stats")
    def stats() -> dict:
        return REC.stats()

    @app.post("/bench/mode")
    async def mode(request: Request) -> dict:
        body = await request.json()
        MODE["race"] = bool(body.get("race"))
        if body.get("swap"):
            variants.swap()
        return dict(MODE)

    @app.get("/bench/settings")
    def bench_settings(request: Request) -> dict:
        s = request.app.state.settings
        return {"turn_concurrency": vars(s.turn_concurrency), "agent_budget": vars(s.agent_budget),
                "executor_workers": request.app.state.executor_workers,
                "busy_timeout": request.app.state.store.busy_timeout,
                "operation_timeout": request.app.state.store.operation_timeout,
                "book_lock_timeout": airline_service.BOOK_LOCK_TIMEOUT,
                "book_max_pending_per_user": airline_service.BOOK_MAX_PENDING_PER_USER}

    return app


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--llm-delay", type=float, default=0.0)
    a = p.parse_args()
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):  # never a real model
        os.environ.pop(name, None)
    os.environ["OCEAN_ENV_FILE"] = str(a.work / "no-such.env")
    uvicorn.run(build(a.work, a.llm_delay), host="127.0.0.1", port=a.port, workers=1,
                log_level="warning", access_log=False, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
