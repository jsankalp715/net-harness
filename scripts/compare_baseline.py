#!/usr/bin/env python3
"""Compare measured convergence times against a stored baseline and flag regressions.

    python scripts/compare_baseline.py --results results \
        --baseline baseline/convergence_baseline.json --report results/regression_report.md
    python scripts/compare_baseline.py --results results --baseline ... --update

A metric regresses when it is slower than baseline by more than BOTH the relative
and the absolute tolerance (sub-second timings are noisy; the absolute floor stops
0.1s -> 0.25s from being flagged as +150%). Exit status: 0 ok, 1 regression(s),
2 usage/data error.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TOLERANCE = {"relative": 1.0, "absolute_s": 1.0}


@dataclass(frozen=True)
class Comparison:
    key: str
    baseline: float | None
    current: float | None
    status: str  # ok | REGRESSION | improved | new | missing

    @property
    def delta(self) -> float | None:
        if self.baseline is None or self.current is None:
            return None
        return self.current - self.baseline


def metric_key(test_id: str, label: str) -> str:
    """Stable key: ``test_failover.py::test_x[case]::failover`` (directory stripped)."""
    return f"{test_id.split('/')[-1]}::{label}"


def collect_metrics(results_dir: Path) -> dict[str, float]:
    """All convergence measurements of *passed* scenarios in ``results_dir``."""
    metrics: dict[str, float] = {}
    for path in sorted(results_dir.glob("*.json")):
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or "test_id" not in data:
            continue
        if data.get("outcome") != "passed":
            continue
        for label, seconds in (data.get("convergence_s") or {}).items():
            metrics[metric_key(data["test_id"], label)] = float(seconds)
    return metrics


def compare(
    current: dict[str, float], baseline: dict[str, float], tolerance: dict[str, float]
) -> list[Comparison]:
    rel = float(tolerance.get("relative", DEFAULT_TOLERANCE["relative"]))
    abs_s = float(tolerance.get("absolute_s", DEFAULT_TOLERANCE["absolute_s"]))
    out: list[Comparison] = []
    for key in sorted(set(current) | set(baseline)):
        cur, base = current.get(key), baseline.get(key)
        if base is None:
            status = "new"
        elif cur is None:
            status = "missing"
        elif cur > base * (1 + rel) and cur - base > abs_s:
            status = "REGRESSION"
        elif cur < base and base - cur > abs_s:
            status = "improved"
        else:
            status = "ok"
        out.append(Comparison(key, base, cur, status))
    return out


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.3f}"


def render_markdown(rows: list[Comparison], tolerance: dict[str, float]) -> str:
    regressions = [r for r in rows if r.status == "REGRESSION"]
    lines = [
        "# Convergence regression report",
        "",
        f"Tolerance: +{tolerance['relative'] * 100:.0f}% **and** +{tolerance['absolute_s']}s "
        "over baseline.",
        "",
        f"**{len(regressions)} regression(s)** across {len(rows)} metric(s).",
        "",
        "| status | metric | baseline (s) | current (s) | delta (s) |",
        "|---|---|---:|---:|---:|",
    ]
    for r in rows:
        delta = "—" if r.delta is None else f"{r.delta:+.3f}"
        badge = "❌ REGRESSION" if r.status == "REGRESSION" else r.status
        lines.append(f"| {badge} | `{r.key}` | {_fmt(r.baseline)} | {_fmt(r.current)} | {delta} |")
    return "\n".join(lines) + "\n"


def load_baseline(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    if not path.exists():
        return {}, dict(DEFAULT_TOLERANCE)
    data = json.loads(path.read_text(encoding="utf-8"))
    tolerance = {**DEFAULT_TOLERANCE, **data.get("tolerance", {})}
    return {k: float(v) for k, v in data.get("metrics", {}).items()}, tolerance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--baseline", type=Path, default=Path("baseline/convergence_baseline.json"))
    parser.add_argument("--report", type=Path, help="write a Markdown report here")
    parser.add_argument("--update", action="store_true", help="overwrite baseline with results")
    parser.add_argument("--warn-only", action="store_true", help="never exit non-zero")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # report has emoji; Windows console is cp1252

    if not args.results.is_dir():
        print(f"results dir {args.results} not found", file=sys.stderr)
        return 2
    current = collect_metrics(args.results)
    baseline, tolerance = load_baseline(args.baseline)

    if args.update:
        if not current:
            print("no passed measurements to store", file=sys.stderr)
            return 2
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tolerance": tolerance, "metrics": dict(sorted(current.items()))}
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"baseline updated: {len(current)} metric(s) -> {args.baseline}")
        return 0

    rows = compare(current, baseline, tolerance)
    report = render_markdown(rows, tolerance)
    print(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
    regressions = sum(r.status == "REGRESSION" for r in rows)
    return 1 if regressions and not args.warn_only else 0


if __name__ == "__main__":
    sys.exit(main())
