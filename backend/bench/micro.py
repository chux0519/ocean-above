"""In-process app.db microbenchmarks (no HTTP): the raw single-writer ceiling, and the cost of the
timing hook itself."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from app.store import Store
from app.store import db as store_db

from .common import percentiles


def writers(work: Path, threads: int, seconds: float) -> dict:
    """`threads` threads each append messages (one BEGIN IMMEDIATE transaction per message) to
    their own conversation as fast as they can; one shared app.db file."""
    work.mkdir(parents=True, exist_ok=True)
    for name in ("micro.db", "micro.db-wal", "micro.db-shm"):
        (work / name).unlink(missing_ok=True)
    store = Store(work / "micro.db")
    convs = []
    for i in range(threads):
        user = store.upsert_user("dev", f"micro-{i}", "m")["id"]
        convs.append((user, store.create_conversation(user)["id"]))
    spans: list = []
    errors = [0]
    previous = store_db.set_timing_hook(spans.append)
    deadline = time.perf_counter() + seconds
    start = threading.Barrier(threads)

    def run(user: str, conv: str) -> None:
        start.wait()
        while time.perf_counter() < deadline:
            try:
                store.add_message(user, conv, "user", "x" * 200)
            except Exception:  # noqa: BLE001 - counted
                errors[0] += 1

    try:
        ts = [threading.Thread(target=run, args=c) for c in convs]
        began = time.perf_counter()
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        elapsed = time.perf_counter() - began
    finally:
        store_db.set_timing_hook(previous)
    writes = [s for s in spans if s.kind == "write"]
    commits = sum(1 for s in writes if s.outcome == "commit")
    return {
        "threads": threads, "seconds": round(elapsed, 3), "commits_per_s": round(commits / elapsed, 1),
        "busy": sum(1 for s in writes if s.outcome == "busy"), "errors": errors[0],
        "lock_wait_ms": percentiles([s.wait for s in writes]),
        "hold_ms": percentiles([s.hold for s in writes]),
        "total_ms": percentiles([s.total for s in writes]),
    }


def hook_overhead(work: Path, ops: int = 2000, rounds: int = 5) -> dict:
    """Microseconds per `get_user` (one read transaction) with the hook off and on (a no-op hook):
    the best of `rounds` alternating rounds, so machine noise does not count as overhead."""
    work.mkdir(parents=True, exist_ok=True)
    store = Store(work / "overhead.db")
    user = store.upsert_user("dev", "o", "o")["id"]
    best = {"off": float("inf"), "on": float("inf")}
    for _ in range(rounds):
        for label, hook in (("off", None), ("on", lambda t: None)):
            previous = store_db.set_timing_hook(hook)
            try:
                t = time.perf_counter()
                for _ in range(ops):
                    store.get_user(user)
                best[label] = min(best[label], (time.perf_counter() - t) / ops * 1e6)
            finally:
                store_db.set_timing_hook(previous)
    return {"off_us_per_read": round(best["off"], 2), "on_us_per_read": round(best["on"], 2),
            "ops": ops, "rounds": rounds}
