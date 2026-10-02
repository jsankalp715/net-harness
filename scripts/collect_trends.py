#!/usr/bin/env python3
"""Plot convergence times across CI runs (trend report).

Downloads the results artifact of every successful CI run with ``gh`` (cached under
``results/trends/runs/<run-id>/``, so reruns only fetch new runs), extracts each
run's convergence metrics exactly like ``compare_baseline.py`` does, and writes:

    results/trends/index.html    small-multiples report (open in a browser)
    results/trends/history.json  the same data, machine-readable

    python scripts/collect_trends.py                 # repo from the git remote
    python scripts/collect_trends.py --limit 50 --repo owner/name

Needs an authenticated GitHub CLI (``gh auth login``). Artifacts expire after the
workflow's retention period (30 days), so older runs drop out of the report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compare_baseline import collect_metrics, load_baseline

from netharness.trends import RunInfo, build_series, render_html

ARTIFACT_RE = re.compile(r"^(results-run\d+|scenario-results-[0-9a-f]+)$")


def gh_json(gh: str, *args: str) -> Any:
    out = subprocess.run([gh, *args], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def list_runs(gh: str, repo: str, limit: int) -> list[RunInfo]:
    data = gh_json(
        gh, "run", "list", "-R", repo, "--workflow", "ci.yml", "--status", "success",
        "-L", str(limit), "--json", "databaseId,number,headSha,headBranch,event,createdAt",
    )  # fmt: skip
    return [
        RunInfo(
            run_id=int(r["databaseId"]),
            number=int(r["number"]),
            sha=str(r["headSha"]),
            branch=str(r["headBranch"]),
            event=str(r["event"]),
            created_at=str(r["createdAt"]),
        )
        for r in data
    ]


def fetch_run(gh: str, repo: str, run: RunInfo, cache: Path) -> Path | None:
    """Return the directory holding this run's scenario JSONs (downloading if needed)."""
    target = cache / str(run.run_id)
    if any(target.glob("*.json")):
        return target
    arts = gh_json(gh, "api", f"repos/{repo}/actions/runs/{run.run_id}/artifacts")
    names = [
        a["name"]
        for a in arts.get("artifacts", [])
        if ARTIFACT_RE.match(a["name"]) and not a.get("expired", False)
    ]
    if not names:
        return None
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [gh, "run", "download", str(run.run_id), "-R", repo, "-n", names[0], "-D", str(target)],
        capture_output=True,
        text=True,
        check=True,
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", help="owner/name (default: from `gh repo view`)")
    parser.add_argument("--limit", type=int, default=30, help="max successful runs to scan")
    parser.add_argument("--out", type=Path, default=Path("results/trends"))
    parser.add_argument("--baseline", type=Path, default=Path("baseline/convergence_baseline.json"))
    parser.add_argument("--gh", default=os.environ.get("GH_BIN", "gh"), help="gh executable")
    args = parser.parse_args(argv)

    try:
        repo = (
            args.repo
            or gh_json(args.gh, "repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
        )
        runs = list_runs(args.gh, repo, args.limit)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"gh failed ({exc}); is the GitHub CLI installed and logged in?", file=sys.stderr)
        return 2

    collected: list[tuple[RunInfo, dict[str, float]]] = []
    for run in runs:
        try:
            folder = fetch_run(args.gh, repo, run, args.out / "runs")
        except subprocess.CalledProcessError as exc:
            print(f"run {run.run_id}: download failed: {exc.stderr.strip()}", file=sys.stderr)
            continue
        if folder is None:
            print(f"run #{run.number} ({run.run_id}): no results artifact (expired?)")
            continue
        metrics = collect_metrics(folder)
        if metrics:
            collected.append((run, metrics))
    if not collected:
        print("no runs with convergence metrics found", file=sys.stderr)
        return 2

    series = build_series(collected)
    baseline, _ = load_baseline(args.baseline)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(render_html(series, baseline, generated), encoding="utf-8")
    history = [
        {"run_id": r.run_id, "number": r.number, "sha": r.sha, "branch": r.branch,
         "event": r.event, "created_at": r.created_at, "metrics": m}
        for r, m in sorted(collected, key=lambda rm: rm[0].created_at)
    ]  # fmt: skip
    (args.out / "history.json").write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    print(f"{len(series)} metrics from {len(collected)} runs -> {args.out / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
