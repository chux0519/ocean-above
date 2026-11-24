"""Shared constants and helpers of the benchmark (no app state here)."""
from __future__ import annotations

import math
import os
import platform
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
LOCATIONS_DB = REPO / "locations.db"
RESULTS = REPO / "docs" / "iterations" / "08-runtime" / "sqlite" / "results"

# Fixed, bench-only secrets: the bench app.db and cookies are throwaway (never a real deployment).
SESSION_SECRET = b"bench-session-secret-0123456789"
NONCE_SECRET = b"bench-nonce-secret-0123456789ab"
SEED = 41
FAKE_REPLY = "Here is your route."


def percentiles(values, points=(50, 95, 99)) -> dict:
    """{"n", "p50", "p95", "p99", "max", "mean"} in milliseconds (values are seconds); linear
    interpolation between closest ranks. Empty input: n=0 and None elsewhere."""
    xs = sorted(v for v in values if v is not None)
    out: dict = {"n": len(xs)}
    if not xs:
        out.update({f"p{p}": None for p in points} | {"max": None, "mean": None})
        return out
    for p in points:
        k = (len(xs) - 1) * p / 100
        lo, hi = math.floor(k), math.ceil(k)
        v = xs[lo] + (xs[hi] - xs[lo]) * (k - lo)
        out[f"p{p}"] = round(v * 1000, 3)
    out["max"] = round(xs[-1] * 1000, 3)
    out["mean"] = round(sum(xs) / len(xs) * 1000, 3)
    return out


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _cpu_model() -> str:
    if sys.platform == "darwin":
        return _run("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def _memory_gb() -> float | None:
    try:
        if sys.platform == "darwin":
            return round(int(_run("sysctl", "-n", "hw.memsize")) / 2**30, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (ValueError, OSError):
        return None


def filesystem_type(path: Path) -> str:
    """The type of the filesystem holding `path` (e.g. apfs, ext4); no device or host names."""
    path = path.resolve()
    best, kind = "", "unknown"
    for line in _run("mount").splitlines():
        # macOS: "/dev/disk3s1 on /Volumes/x (apfs, local, ...)"; Linux: "dev on /x type ext4 (...)"
        m = re.match(r".+? on (.+?) (?:\((\w+)|type (\w+))", line)
        if not m:
            continue
        mount, fs = m.group(1), m.group(2) or m.group(3)
        if (str(path) == mount or str(path).startswith(mount.rstrip("/") + "/")) and len(mount) > len(best):
            best, kind = mount, fs
    return kind


def machine_info(work_dir: Path) -> dict:
    """CPU model, OS, Python and SQLite versions; never the host or user name."""
    return {
        "cpu": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "memory_gb": _memory_gb(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "filesystem": filesystem_type(work_dir),
        "container": Path("/.dockerenv").exists(),
    }
