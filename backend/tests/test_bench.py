"""The app.db benchmark harness (#41) keeps working: a tiny end-to-end run, plus its statistics."""
from __future__ import annotations

import json

from bench import __main__ as bench_main
from bench.common import percentiles


def test_percentiles_interpolate_and_use_milliseconds():
    p = percentiles([i / 1000 for i in range(1, 101)])  # 1..100 ms
    assert p["n"] == 100 and p["p50"] == 50.5 and p["p95"] == 95.05 and p["p99"] == 99.01 and p["max"] == 100.0
    assert percentiles([None])["n"] == 0 and percentiles([])["p50"] is None


def test_a_tiny_run_has_no_integrity_failures(tmp_path):
    out = tmp_path / "out"
    code = bench_main.main(["--quick", "--levels", "3", "--llm-levels", "", "--micro-threads", "2",
                            "--work", str(tmp_path / "work"), "--out", str(out)])
    doc = json.loads((out / "quick.json").read_text())
    assert code == 0 and doc["integrity_failures"] == 0
    level = doc["mixed"][0]
    assert level["client"]["http_5xx"] == 0 and level["server"]["busy"] == 0
    assert level["server"]["by_site"]["_book_transaction"]["n"] > 0  # the timing hook saw bookings
    assert doc["correctness"]["double_click"]["quotes_with_more_than_one_success"] == 0
    modes = doc["recheck"]["modes"]
    assert modes["changed_inside_transaction"]["server"]["rebuild_in_recheck"] > 0
    assert modes["unchanged"]["server"]["rebuild_in_recheck"] == 0
    assert "Mixed workload" in (out / "quick.md").read_text()
    assert "machine" in doc and "hostname" not in json.dumps(doc["machine"]).lower()
