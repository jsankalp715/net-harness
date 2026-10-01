from __future__ import annotations

import json
import sys
from pathlib import Path

from netharness.results import ScenarioRecord, load_records, safe_filename, write_record

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import compare_baseline as cb

TOL = {"relative": 0.5, "absolute_s": 0.5}


def _write(results: Path, test_id: str, outcome: str, **conv: float) -> None:
    rec = ScenarioRecord(test_id=test_id, name=test_id.split("::")[-1])
    for label, s in conv.items():
        rec.record_convergence(label, s)
    rec.finish(outcome)
    write_record(rec, results)


def test_compare_statuses() -> None:
    base = {"a": 1.0, "b": 1.0, "c": 4.0, "gone": 1.0}
    cur = {"a": 1.2, "b": 3.0, "c": 1.0, "fresh": 0.3}
    status = {r.key: r.status for r in cb.compare(cur, base, TOL)}
    assert status == {
        "a": "ok",
        "b": "REGRESSION",
        "c": "improved",
        "gone": "missing",
        "fresh": "new",
    }


def test_small_absolute_change_is_not_a_regression() -> None:
    # +200% but only +0.2s -> noise, not a regression
    (row,) = cb.compare({"x": 0.3}, {"x": 0.1}, TOL)
    assert row.status == "ok"


def test_end_to_end_flags_regression(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write(results, "tests/integration/test_failover.py::test_f[ospf]", "passed", failover=3.0)
    _write(results, "tests/integration/test_failover.py::test_f[bgp]", "failed", failover=9.0)
    baseline = tmp_path / "baseline.json"
    key = "test_failover.py::test_f[ospf]::failover"
    baseline.write_text(json.dumps({"tolerance": TOL, "metrics": {key: 1.0}}))

    report = tmp_path / "report.md"
    rc = cb.main(["--results", str(results), "--baseline", str(baseline), "--report", str(report)])
    assert rc == 1
    assert "REGRESSION" in report.read_text()
    # failed scenarios never contribute measurements
    assert "test_f[bgp]" not in report.read_text()
    assert cb.main(["--results", str(results), "--baseline", str(baseline), "--warn-only"]) == 0


def test_update_writes_baseline(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write(results, "tests/integration/test_ospf.py::test_initial", "passed", initial=1.5)
    baseline = tmp_path / "b" / "baseline.json"
    assert cb.main(["--results", str(results), "--baseline", str(baseline), "--update"]) == 0
    data = json.loads(baseline.read_text())
    assert data["metrics"] == {"test_ospf.py::test_initial::initial": 1.5}


def test_record_roundtrip(tmp_path: Path) -> None:
    rec = ScenarioRecord(test_id="tests/integration/test_x.py::test_y[a-b]", name="test_y[a-b]")
    rec.event("link_down", link="r1:eth1 <-> r2:eth1")
    rec.finish("passed")
    path = write_record(rec, tmp_path)
    assert path.name == safe_filename(rec.test_id) == "test_x__test_y[a-b].json"
    (loaded,) = load_records(tmp_path)
    assert loaded["outcome"] == "passed" and loaded["schema_version"] == 1
    assert loaded["events"][0]["link"] == "r1:eth1 <-> r2:eth1"
    assert loaded["duration_s"] is not None
