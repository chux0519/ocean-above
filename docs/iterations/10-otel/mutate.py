import subprocess, sys, pathlib, os
M = pathlib.Path("/tmp/oa-otel/mut/backend")
PY = "/Volumes/ZT/repos/llc2026/ocean-above-it10-otel/backend/.venv/bin/python"
T = "app/tracing.py"
MUTS = {
 "M1 _Span.set_attribute unguarded": (T, "        try:\n            self._span.set_attribute(key, value)\n        except Exception:\n            log.debug(\"tracing: set_attribute failed\", exc_info=True)\n", "        self._span.set_attribute(key, value)\n"),
 "M2 _exit unguarded": (T, "    try:\n        manager.__exit__(*exc_info)\n    except Exception:\n        log.debug(\"tracing: span end failed\", exc_info=True)\n", "    manager.__exit__(*exc_info)\n"),
 "M3 include_content=True": ("app/agent/loop.py", "include_content=False", "include_content=True"),
 "M6 status description kept": (T, "status=Status(span.status.status_code)", "status=span.status"),
 "M10 span() catches Exception only": (T, "    except BaseException as exc:\n        _exit(manager,", "    except Exception as exc:\n        _exit(manager,"),
 "M13 exception events kept": (T, "if e.name == \"exception\" else e for e in span.events]", "if e.name == \"never\" else e for e in span.events]"),
 "M15 book_async without _book_status": ("app/airline/service.py", "        with tracing.span(\"airline.book\") as span, _book_status(span):\n            quote = await asyncio.to_thread", "        with tracing.span(\"airline.book\") as span:\n            quote = await asyncio.to_thread"),
 "M17 trace end before retire, unguarded": ("app/agent/loop.py", "            self._retire(run)\n            queue.put_nowait(None)\n            span.set_attributes_from(lambda: _trace_end(run, turn))", "            span.set_attributes(_trace_end(run, turn))\n            self._retire(run)\n            queue.put_nowait(None)"),
 "M18 add_event unguarded": (T, "        try:\n            self._span.add_event(name, {k: v for k, v in (attributes or {}).items() if v is not None})\n        except Exception:\n            log.debug(\"tracing: add_event failed\", exc_info=True)\n", "        self._span.add_event(name, {k: v for k, v in (attributes or {}).items() if v is not None})\n"),
 "M19 set_attributes_from computation unguarded": (T, "        try:\n            attributes = compute()\n        except Exception:\n            log.debug(\"tracing: attribute computation failed\", exc_info=True)\n            return\n", "        attributes = compute()\n"),
 "M20 P1-1 local endpoint trusts env proxies": (T, "            session.trust_env = False\n", "            session.trust_env = True\n"),
 "M21 P2-1 shutdown unbounded": (T, "    worker.join(timeout)\n", "    worker.join()\n"),
 "M22 P2-4 invented tool names kept": (T, "            out[\"name\"] = UNKNOWN_TOOL\n", "            pass\n"),
 "M23 P2-5 unkeyed hash": (T, "    return hmac.new(_hash_key, value.encode(), hashlib.sha256).hexdigest()[:16]", "    return hashlib.sha256(value.encode()).hexdigest()[:16]"),
 "M24 P2-7 no interrupted status": ("app/airline/service.py", "        span.set_attribute(\"ocean.book.status\", \"interrupted\")\n", "        pass\n"),
 "M25 current() unguarded": (T, "        live = trace.get_current_span()\n        return _Span(live) if live.is_recording() else NO_SPAN\n    except Exception:\n        return NO_SPAN", "        live = trace.get_current_span()\n    except Exception:\n        return NO_SPAN\n    return _Span(live) if live.is_recording() else NO_SPAN"),
}
for name, (rel, old, new) in MUTS.items():
    if len(sys.argv) > 1 and not name.startswith(tuple(sys.argv[1:])): continue
    p = M / rel; src = p.read_text()
    if src.count(old) != 1:
        print(name, "-> PATTERN NOT FOUND", src.count(old)); continue
    p.write_text(src.replace(old, new))
    try:
        r = subprocess.run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests/test_tracing.py"], cwd=M, capture_output=True, text=True,
                           env={**os.environ, "HYPOTHESIS_PROFILE": "ci"}, timeout=120)
        failed = [l.split(" ")[1].split("::")[-1] for l in r.stdout.splitlines() if l.startswith("FAILED")]
        print(f"{name}: {'KILLED' if r.returncode else 'SURVIVED'} | {failed[:2]}", flush=True)
    except subprocess.TimeoutExpired:
        print(name, "-> KILLED (tests hung, timeout 120 s)", flush=True)
    finally:
        p.write_text(src)
