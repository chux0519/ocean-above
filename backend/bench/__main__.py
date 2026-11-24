"""Run the app.db benchmark and write JSON + Markdown results. See `bench/__init__.py`."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import integrity, load, micro
from .common import BACKEND, LOCATIONS_DB, RESULTS, SEED, machine_info, percentiles
from .seed import city_pairs, prepare_locations, seed

LEVELS = (1, 5, 10, 25, 50, 100)
RECHECK_MIX = (("history", 0.2), ("chat", 0.4), ("book", 0.4))


# ------------------------------------------------------------------ server process -------------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """The app in its own process (one uvicorn worker), like the deployment."""

    def __init__(self, work: Path, llm_delay: float = 0.0):
        self.work, self.llm_delay, self.port = work, llm_delay, _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.cpu: list[float] = []
        self.rss_kb: list[int] = []

    async def __aenter__(self) -> "Server":
        self.log = open(self.work / "server.log", "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "bench.server", "--work", str(self.work), "--port", str(self.port),
             "--llm-delay", str(self.llm_delay)], cwd=BACKEND, stdout=self.log, stderr=subprocess.STDOUT)
        async with httpx.AsyncClient(base_url=self.base) as http:
            for _ in range(300):
                if self.proc.poll() is not None:
                    raise RuntimeError(f"bench server exited; see {self.work / 'server.log'}")
                try:
                    if (await http.get("/api/health")).status_code == 200:
                        return self
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
        raise RuntimeError("bench server did not start")

    async def __aexit__(self, *exc) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.log.close()

    async def sample(self, stop: asyncio.Event) -> None:
        """CPU % and RSS of the server process, once a second (`ps`, no extra dependency)."""
        while not stop.is_set():
            proc = await asyncio.create_subprocess_exec(
                "ps", "-o", "%cpu=,rss=", "-p", str(self.proc.pid), stdout=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
            parts = out.decode().split()
            if len(parts) == 2:
                self.cpu.append(float(parts[0]))
                self.rss_kb.append(int(parts[1]))
            try:
                await asyncio.wait_for(stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass

    def resources(self) -> dict:
        return {"cpu_percent_mean": round(sum(self.cpu) / len(self.cpu), 1) if self.cpu else None,
                "cpu_percent_max": max(self.cpu, default=None),
                "rss_mb_max": round(max(self.rss_kb) / 1024, 1) if self.rss_kb else None}


def http_client(server: Server, clients: int) -> httpx.AsyncClient:
    n = clients * 4 + 10
    return httpx.AsyncClient(base_url=server.base, timeout=120,
                             limits=httpx.Limits(max_connections=n, max_keepalive_connections=n))


# ------------------------------------------------------------------ summaries ------------------
def summarize_samples(samples: list[load.Sample], seconds: float | None = None) -> dict:
    by_op: dict[str, dict] = {}
    for op in sorted({s.op for s in samples}):
        mine = [s for s in samples if s.op == op]
        by_op[op] = {**percentiles([s.seconds for s in mine]),
                     "codes": dict(Counter(s.code or "ok" for s in mine))}
    out = {"requests": len(samples), "by_endpoint": by_op,
           "http_5xx": sum(1 for s in samples if s.status >= 500),
           "codes": dict(Counter(s.code or "ok" for s in samples))}
    if seconds:
        out["throughput_rps"] = round(len(samples) / seconds, 1)
    return out


def summarize_server(stats: dict) -> dict:
    writes = [(row["site"], span) for row in stats["store"] if row["kind"] == "write" for span in row["spans"]]
    reads = [span for row in stats["store"] if row["kind"] == "read" for span in row["spans"]]
    sites = {}
    for site in sorted({s for s, _ in writes}):
        mine = [sp for s, sp in writes if s == site]
        sites[site] = {"n": len(mine), "wait_ms": percentiles([sp[0] for sp in mine]),
                       "hold_ms": percentiles([sp[1] for sp in mine])}
    outcomes = Counter(sp[3] for _, sp in writes) + Counter(f"read_{sp[3]}" for sp in reads)
    other = [sp for s, sp in writes if s != "_book_transaction"]
    return {
        "write_lock_wait_ms": percentiles([sp[0] for _, sp in writes]),
        "write_hold_ms": percentiles([sp[1] for _, sp in writes]),
        "write_total_ms": percentiles([sp[2] for _, sp in writes]),
        "non_booking_write_wait_ms": percentiles([sp[0] for sp in other]),
        "read_total_ms": percentiles([sp[2] for sp in reads]),
        "outcomes": dict(outcomes),
        "busy": outcomes.get("busy", 0) + outcomes.get("read_busy", 0),
        "timeouts": outcomes.get("timeout", 0) + outcomes.get("read_timeout", 0),
        "writes_per_s": round(len(writes) / stats["seconds"], 1) if stats["seconds"] else None,
        "by_site": sites,
        "recheck_ms": percentiles(stats["recheck"]),
        "rebuild_ms": percentiles(stats["rebuild"]),
        "rebuild_in_recheck": stats["rebuild_in_recheck"],
        "locations_current_ms": {k: percentiles(v) for k, v in stats["current"].items()},
    }


async def _stats(http: httpx.AsyncClient) -> dict:
    return summarize_server((await http.get("/bench/stats")).json())


def _wal_bytes(work: Path) -> int:
    try:
        return (work / "app.db-wal").stat().st_size
    except FileNotFoundError:
        return 0


# ------------------------------------------------------------------ scenarios ------------------
def fresh(root: Path, name: str, args, pairs, users: int) -> tuple[Path, dict]:
    work = root / name
    prepare_locations(work, LOCATIONS_DB)
    view = seed(work, users=users, quotes_per_user=args.quotes_per_user, history_turns=args.history_turns,
                pairs=pairs, rng=random.Random(SEED))
    return work, view


async def mixed_level(root: Path, clients_n: int, args, pairs, *, llm_delay: float = 0.0,
                      name: str | None = None, with_burst: bool = True) -> dict:
    work, view = fresh(root, name or f"mixed-c{clients_n}", args, pairs, max(clients_n, args.users))
    bookings, counters = load.Bookings(), Counter()
    result: dict = {"clients": clients_n, "llm_delay_s": llm_delay}
    async with Server(work, llm_delay) as server:
        async with http_client(server, clients_n) as http:
            result["server_settings"] = (await http.get("/bench/settings")).json()
            clients = load.clients_for(view, clients_n, SEED)
            await load.mixed(http, clients, pairs, args.warmup, bookings, counters)
            await http.post("/bench/reset")
            stop = asyncio.Event()
            sampler = asyncio.create_task(server.sample(stop))
            samples, reps = [], []
            for _ in range(args.reps):
                t = time.perf_counter()
                got = await load.mixed(http, clients, pairs, args.interval, bookings, counters)
                elapsed = time.perf_counter() - t
                samples += got
                reps.append({"seconds": round(elapsed, 3), "throughput_rps": round(len(got) / elapsed, 1),
                             "wal_bytes": _wal_bytes(work)})
            stop.set()
            await sampler
            result["server"] = await _stats(http)
            result["client"] = summarize_samples(samples, sum(r["seconds"] for r in reps))
            result["reps"] = reps
            rps = [r["throughput_rps"] for r in reps]
            result["throughput_rps"] = {"mean": round(sum(rps) / len(rps), 1), "min": min(rps), "max": max(rps)}
            result["resources"] = server.resources()
            result["counters"] = dict(counters)
            if with_burst:
                await http.post("/bench/reset")
                burst = []
                for _ in range(args.bursts):
                    burst += await load.burst(http, clients, bookings)
                result["burst"] = {"client": summarize_samples(burst), "server": await _stats(http)}
    result["integrity"] = integrity.check(work / "app.db", bookings)
    return result


async def correctness(root: Path, args, pairs) -> dict:
    n = min(25, args.users)
    work, view = fresh(root, "same-conversation", args, pairs, args.users)
    bookings = load.Bookings()
    out: dict = {"clients": n}
    async with Server(work) as server:
        async with http_client(server, n * 4) as http:
            clients = load.clients_for(view, n, SEED)
            samples, dc = await load.double_clicks(http, clients, bookings)
            out["double_click"] = {**dc, "client": summarize_samples(samples)}
            turns = await load.concurrent_turns(http, clients, pairs)
            out["concurrent_turns"] = {"per_conversation": 3, "client": summarize_samples(turns)}
            mix = await load.turn_and_click(http, clients, pairs, bookings)
            out["turn_and_click"] = {"client": summarize_samples(mix)}
    out["integrity"] = integrity.check(work / "app.db", bookings)
    return out


async def recheck(root: Path, args, pairs) -> dict:
    """Bookings while locations.db is unchanged / changes before the click / changes inside the
    booking transaction (race), under a booking-heavy mixed load of 10 clients."""
    n = min(10, args.users)
    work, view = fresh(root, "recheck", args, pairs, args.users)
    bookings, counters = load.Bookings(), Counter()
    out: dict = {"clients": n, "mix": dict(RECHECK_MIX), "modes": {}}
    async with Server(work) as server:
        async with http_client(server, n) as http:
            clients = load.clients_for(view, n, SEED)
            await load.mixed(http, clients, pairs, args.warmup, bookings, counters, RECHECK_MIX)
            for mode in ("unchanged", "changed_before_click", "changed_inside_transaction"):
                await http.post("/bench/mode", json={"race": mode == "changed_inside_transaction"})
                swap = None
                if mode == "changed_before_click":
                    async def swap():
                        await http.post("/bench/mode", json={"race": False, "swap": True})
                await http.post("/bench/reset")
                t = time.perf_counter()
                samples = await load.mixed(http, clients, pairs, args.interval, bookings, counters,
                                           RECHECK_MIX, before_book=swap)
                elapsed = time.perf_counter() - t
                out["modes"][mode] = {"client": summarize_samples(samples, elapsed), "server": await _stats(http)}
            await http.post("/bench/mode", json={"race": False})
    out["counters"] = dict(counters)
    out["integrity"] = integrity.check(work / "app.db", bookings)
    return out


# ------------------------------------------------------------------ report ---------------------
def _p(d: dict | None) -> str:
    if not d or not d.get("n"):
        return "–"
    return f"{d['p50']:.1f} / {d['p95']:.1f} / {d['p99']:.1f}"


def _fails(r: dict) -> int:
    return integrity.failures(r["integrity"])


def markdown(doc: dict) -> str:
    m = doc["machine"]
    lines = [
        f"# app.db benchmark results ({doc['started'][:10]})",
        "",
        "Measured on this machine, one run; all latencies in ms as p50 / p95 / p99. Fake LLM (no model",
        "latency) unless stated. Server: the real app, one uvicorn worker, separate process, 127.0.0.1.",
        "",
        f"- Machine: {m['cpu']}, {m['cpu_count']} cores, {m['memory_gb']} GB; {m['os']}; Python {m['python']};",
        f"  SQLite {m['sqlite']}; work dir filesystem: {m['filesystem']}; container: {m['container']}",
        f"- Run: `{doc['command']}`, {doc['wall_seconds']:.0f} s wall; warm-up {doc['args']['warmup']} s, "
        f"{doc['args']['reps']} × {doc['args']['interval']} s measured per level; seed {doc['args']['seed']}; "
        f"{doc['args']['users']} users, {doc['args']['quotes_per_user']} presented quotes and "
        f"{doc['args']['history_turns']} history turns each",
        f"- App limits in effect: {json.dumps(doc.get('server_settings', {}).get('turn_concurrency'))}, "
        f"busy_timeout {doc.get('server_settings', {}).get('busy_timeout')} s",
        "",
        "## 1. Mixed workload (70 % reads / 20 % chat turns / 10 % bookings), closed loop, no think time",
        "",
        "| clients | req/s mean [min–max] | history | chat turn | book | write lock wait | write hold | writes/s | BUSY | 5xx | other errors | integrity failures |",
        "|---:|---|---|---|---|---|---|---:|---:|---:|---|---:|",
    ]
    for r in doc["mixed"]:
        c, s, e = r["client"], r["server"], r["client"]["by_endpoint"]
        errs = {k: v for k, v in c["codes"].items() if k != "ok"}
        lines.append(
            f"| {r['clients']} | {r['throughput_rps']['mean']} [{r['throughput_rps']['min']}–{r['throughput_rps']['max']}] "
            f"| {_p(e.get('history'))} | {_p(e.get('chat'))} | {_p(e.get('book'))} | {_p(s['write_lock_wait_ms'])} "
            f"| {_p(s['write_hold_ms'])} | {s['writes_per_s']} | {s['busy']} | {c['http_5xx']} | {errs or '–'} | {_fails(r)} |")
    lines += ["", "Server CPU / memory during the measured intervals:", "",
              "| clients | CPU % mean | CPU % max | RSS max MB | WAL bytes at end of reps |", "|---:|---:|---:|---:|---|"]
    for r in doc["mixed"]:
        res = r["resources"]
        lines.append(f"| {r['clients']} | {res['cpu_percent_mean']} | {res['cpu_percent_max']} | {res['rss_mb_max']} "
                     f"| {', '.join(str(x['wal_bytes']) for x in r['reps'])} |")
    top = doc["mixed"][-1]
    lines += ["", f"Write transactions by call site at {top['clients']} clients (lock wait and hold):", "",
              "| site | n | wait | hold |", "|---|---:|---|---|"]
    for site, v in sorted(top["server"]["by_site"].items(), key=lambda kv: -kv[1]["n"]):
        lines.append(f"| `{site}` | {v['n']} | {_p(v['wait_ms'])} | {_p(v['hold_ms'])} |")
    lines += ["", "## 2. Booking burst (every client clicks a distinct quote at the same moment)", "",
              f"{doc['args']['bursts']} bursts per level.", "",
              "| clients | clicks | book latency | lock wait | `_book_transaction` hold | `_recheck` | BUSY | codes |",
              "|---:|---:|---|---|---|---|---:|---|"]
    for r in doc["mixed"]:
        b = r.get("burst")
        if not b:
            continue
        hold = b["server"]["by_site"].get("_book_transaction", {}).get("hold_ms")
        lines.append(f"| {r['clients']} | {b['client']['requests']} | {_p(b['client']['by_endpoint'].get('book'))} "
                     f"| {_p(b['server']['write_lock_wait_ms'])} | {_p(hold)} | {_p(b['server']['recheck_ms'])} "
                     f"| {b['server']['busy']} | {b['client']['codes']} |")
    cor = doc["correctness"]
    dc = cor["double_click"]
    lines += ["", "## 3. Same conversation: double clicks and concurrent turns", "",
              f"- Double click: {dc['quotes']} quotes × {dc['clicks_per_quote']} identical clicks at once → "
              f"codes {dc['client']['codes']}; quotes with more than one success: "
              f"{dc['quotes_with_more_than_one_success']}, with none: {dc['quotes_with_no_success']}, "
              f"unexpected codes: {dc['unexpected_codes'] or 'none'}.",
              f"- {cor['concurrent_turns']['per_conversation']} chat turns at once in each of {cor['clients']} "
              f"conversations → codes {cor['concurrent_turns']['client']['codes']}; chat latency "
              f"{_p(cor['concurrent_turns']['client']['by_endpoint'].get('chat'))}.",
              f"- A turn and a click of the same conversation at once → codes {cor['turn_and_click']['client']['codes']}.",
              f"- Integrity after the scenario: {cor['integrity']}.",
              "", "## 4. `_recheck` while locations.db changes (booking-heavy mix, "
              f"{doc['recheck']['clients']} clients)", "",
              "| locations.db | bookings | `_recheck` | graph rebuilds (n, ms) | rebuilds inside the write transaction "
              "| `_book_transaction` hold | other writes' lock wait | book latency |",
              "|---|---:|---|---|---:|---|---|---|"]
    for mode, v in doc["recheck"]["modes"].items():
        s = v["server"]
        hold = s["by_site"].get("_book_transaction", {}).get("hold_ms")
        lines.append(f"| {mode.replace('_', ' ')} | {s['recheck_ms']['n']} | {_p(s['recheck_ms'])} "
                     f"| {s['rebuild_ms']['n']}: {_p(s['rebuild_ms'])} | {s['rebuild_in_recheck']} | {_p(hold)} "
                     f"| {_p(s['non_booking_write_wait_ms'])} | {_p(v['client']['by_endpoint'].get('book'))} |")
    lines += [f"", f"Integrity after the scenario: {doc['recheck']['integrity']}."]
    if doc.get("llm_latency"):
        lines += ["", f"## 5. End to end with simulated model latency ({doc['args']['llm_delay']} s per LLM step, "
                  "2 steps per turn)", "",
                  "| clients | req/s | chat turn | book | history | write lock wait | chat codes |",
                  "|---:|---:|---|---|---|---|---|"]
        for r in doc["llm_latency"]:
            e = r["client"]["by_endpoint"]
            lines.append(f"| {r['clients']} | {r['throughput_rps']['mean']} | {_p(e.get('chat'))} | {_p(e.get('book'))} "
                         f"| {_p(e.get('history'))} | {_p(r['server']['write_lock_wait_ms'])} "
                         f"| {e.get('chat', {}).get('codes')} |")
    lines += ["", "## 6. In-process store microbenchmark (no HTTP; one message insert per transaction)", "",
              "| threads | commits/s | lock wait | hold | BUSY | errors |", "|---:|---:|---|---|---:|---:|"]
    for r in doc["micro"]["writers"]:
        lines.append(f"| {r['threads']} | {r['commits_per_s']} | {_p(r['lock_wait_ms'])} | {_p(r['hold_ms'])} "
                     f"| {r['busy']} | {r['errors']} |")
    o = doc["micro"]["hook_overhead"]
    lines += ["", f"Timing hook cost per read transaction (best of {o['rounds']} alternating rounds of "
              f"{o['ops']}): off {o['off_us_per_read']} µs, on (no-op hook) {o['on_us_per_read']} µs.", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ main -----------------------
async def main_async(args) -> dict:
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    root = Path(tempfile.mkdtemp(prefix="oa-bench-")) if args.work is None else args.work
    root.mkdir(parents=True, exist_ok=True)
    pairs = city_pairs(LOCATIONS_DB, random.Random(SEED))
    doc: dict = {"started": started.isoformat(timespec="seconds"), "command": " ".join(["python -m bench", *sys.argv[1:]]),
                 "args": {**{k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}, "seed": SEED},
                 "machine": machine_info(root), "mixed": []}
    for n in args.levels:
        print(f"[bench] mixed, {n} clients", flush=True)
        r = await mixed_level(root, n, args, pairs)
        doc["mixed"].append(r)
        doc.setdefault("server_settings", r["server_settings"])
    print("[bench] same-conversation scenarios", flush=True)
    doc["correctness"] = await correctness(root, args, pairs)
    print("[bench] recheck scenarios", flush=True)
    doc["recheck"] = await recheck(root, args, pairs)
    doc["llm_latency"] = []
    for n in args.llm_levels:
        print(f"[bench] simulated LLM latency, {n} clients", flush=True)
        doc["llm_latency"].append(await mixed_level(root, n, args, pairs, llm_delay=args.llm_delay,
                                                    name=f"llm-c{n}", with_burst=False))
    print("[bench] in-process microbenchmark", flush=True)
    doc["micro"] = {"writers": [micro.writers(root / "micro", t, args.micro_seconds) for t in args.micro_threads],
                    "hook_overhead": micro.hook_overhead(root / "micro", args.overhead_ops)}
    doc["wall_seconds"] = round(time.perf_counter() - t0, 1)
    doc["integrity_failures"] = sum(_fails(r) for r in doc["mixed"] + doc["llm_latency"]) \
        + _fails(doc["correctness"]) + _fails(doc["recheck"]) \
        + doc["correctness"]["double_click"]["quotes_with_more_than_one_success"]
    doc["work_dir_kept"] = bool(args.work)
    return doc


FULL = {"levels": list(LEVELS), "warmup": 2.0, "reps": 3, "interval": 5.0, "bursts": 3, "users": 100,
        "quotes_per_user": 6, "history_turns": 5, "llm_delay": 0.6, "llm_levels": [10, 50, 100],
        "micro_threads": [1, 4, 16, 64], "micro_seconds": 3.0, "overhead_ops": 2000}
QUICK = {"levels": [1, 5], "warmup": 0.3, "reps": 2, "interval": 0.7, "bursts": 1, "users": 6,
         "quotes_per_user": 3, "history_turns": 1, "llm_delay": 0.05, "llm_levels": [3],
         "micro_threads": [1, 4], "micro_seconds": 0.3, "overhead_ops": 100}


def parse_args(argv=None):
    ints = lambda s: [int(x) for x in s.split(",") if x]  # noqa: E731
    p = argparse.ArgumentParser(prog="python -m bench")
    p.add_argument("--quick", action="store_true", help="tiny smoke run (seconds), results to a temp dir")
    p.add_argument("--levels", type=ints, help="concurrent clients per mixed level (default 1,5,10,25,50,100)")
    p.add_argument("--warmup", type=float)
    p.add_argument("--reps", type=int)
    p.add_argument("--interval", type=float, help="seconds per measured repetition")
    p.add_argument("--bursts", type=int)
    p.add_argument("--users", type=int)
    p.add_argument("--quotes-per-user", type=int)
    p.add_argument("--history-turns", type=int)
    p.add_argument("--llm-delay", type=float, help="simulated seconds per LLM step")
    p.add_argument("--llm-levels", type=ints)
    p.add_argument("--micro-threads", type=ints)
    p.add_argument("--micro-seconds", type=float)
    p.add_argument("--overhead-ops", type=int)
    p.add_argument("--work", type=Path, default=None, help="keep the databases here (default: a temp dir)")
    p.add_argument("--out", type=Path, default=None, help=f"results dir (default {RESULTS}; --quick: a temp dir)")
    a = p.parse_args(argv)
    for key, value in (QUICK if a.quick else FULL).items():
        if getattr(a, key) is None:
            setattr(a, key, value)
    return a


def main(argv=None) -> int:
    args = parse_args(argv)
    doc = asyncio.run(main_async(args))
    out = args.out or (Path(tempfile.mkdtemp(prefix="oa-bench-out-")) if args.quick else RESULTS)
    out.mkdir(parents=True, exist_ok=True)
    stem = "quick" if args.quick else "bench-" + doc["started"][:10]
    (out / f"{stem}.json").write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    (out / f"{stem}.md").write_text(markdown(doc))
    print(f"[bench] wrote {out / stem}.json and .md; integrity failures: {doc['integrity_failures']}", flush=True)
    return 1 if doc["integrity_failures"] else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    raise SystemExit(main())
