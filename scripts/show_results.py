#!/usr/bin/env python3
"""Print the last test run's evidence in a readable form (stdlib only).

    python3 scripts/show_results.py            # from results/ (default)
    bash scripts/run_in_docker.sh python3 scripts/show_results.py

Shows each scenario's outcome and measured convergence, then, for the first failover
scenario, the observer router's route *before*, *during* and *after* the failure,
straight from that scenario's JSON log.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BOLD, GREEN, RED, DIM, RESET = "\033[1m", "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def main() -> int:
    results = Path(sys.argv[1] if len(sys.argv) > 1 else "results")
    summary_path = results / "summary.json"
    if not summary_path.exists():
        print(f"no {summary_path}: run some tests first")
        return 1
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    counts = " · ".join(f"{n} {k}" for k, n in summary["counts"].items())
    print(
        f"{BOLD}Last run:{RESET} {counts}   ({summary['finished_at'][:19].replace('T', ' ')} UTC)\n"
    )
    print(f"{BOLD}{'result':8} {'scenario':64} convergence{RESET}")
    for sc in summary["scenarios"]:
        ok = sc["outcome"] == "passed"
        mark = f"{GREEN}passed{RESET}" if ok else f"{RED}{sc['outcome']}{RESET}"
        # "test_link_restore_returns_to_primary[case]" -> "link_restore_returns_to_primary[case]"
        name = sc["test_id"].split("::", 1)[1].removeprefix("test_")
        conv = ", ".join(f"{k} {v:.2f}s" for k, v in sc["convergence_s"].items()) or "-"
        print(f"{mark:17} {name:64} {conv}")

    # one scenario in depth: what the routing table looked like around the failure
    logs = sorted(results.glob("test_failover__test_link_failure_reconverges*.json"))
    if not logs:
        return 0
    rec = json.loads(logs[0].read_text(encoding="utf-8"))
    case = rec["params"].get("case", {})
    node, prefix = case.get("observer"), case.get("prefix")
    if not node or not prefix:
        return 0
    print(f"\n{BOLD}Inside one JSON log:{RESET} {DIM}{logs[0].name}{RESET}")
    print(f"{node}'s route to {prefix}, as recorded by the harness:")
    for label in ("before", "during_failure", "after"):
        route = rec["routes"].get(label, {}).get(node, {}).get(prefix)
        if route:
            hops = ", ".join(f"{h['ip']} via {h['interface']}" for h in route["nexthops"])
            print(f"  {label:15} metric {route['metric']:<3} {hops}")
    for label, secs in rec["convergence_s"].items():
        print(f"  {GREEN}{label}: {secs:.3f} s{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
