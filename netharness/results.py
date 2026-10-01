"""Structured per-scenario records, written as JSON by the pytest hook."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = 1


class SupportsRoutingTables(Protocol):
    name: str

    def routing_tables(self) -> dict[str, dict[str, dict[str, Any]]]: ...


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass
class ScenarioRecord:
    """Everything we know about one test scenario run."""

    test_id: str
    name: str
    markers: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    topology: str | None = None
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    duration_s: float | None = None
    outcome: str = "unknown"  # passed | failed | error | skipped
    convergence_s: dict[str, float] = field(default_factory=dict)
    routes: dict[str, dict[str, Any]] = field(default_factory=dict)  # label -> node -> table
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    _t0: float = field(default_factory=time.monotonic, repr=False)

    def event(self, message: str, **data: Any) -> None:
        self.events.append(
            {"at": utc_now(), "t_s": round(time.monotonic() - self._t0, 4), "msg": message, **data}
        )

    def snapshot(self, lab: SupportsRoutingTables, label: str) -> None:
        """Capture every node's selected routes under ``label`` (e.g. "before", "after")."""
        self.topology = self.topology or lab.name
        self.routes[label] = lab.routing_tables()
        self.event(f"snapshot:{label}")

    def record_convergence(self, label: str, seconds: float) -> None:
        self.convergence_s[label] = round(seconds, 4)
        self.event(f"converged:{label}", seconds=round(seconds, 4))

    def finish(self, outcome: str, error: str | None = None) -> None:
        self.outcome = outcome
        self.error = error
        self.finished_at = utc_now()
        self.duration_s = round(time.monotonic() - self._t0, 4)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_t0")
        data["schema_version"] = SCHEMA_VERSION
        return data


def safe_filename(test_id: str) -> str:
    """``tests/x.py::test_a[ospf-r1]`` -> ``x__test_a[ospf-r1].json``-ish, filesystem-safe."""
    stem = test_id.split("/")[-1].replace(".py::", "__").replace("::", "__")
    return re.sub(r"[^A-Za-z0-9_.\-\[\]]+", "_", stem) + ".json"


def write_record(record: ScenarioRecord, results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / safe_filename(record.test_id)
    path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=False) + "\n")
    return path


def load_records(results_dir: Path) -> list[dict[str, Any]]:
    """Load every scenario JSON in ``results_dir`` (ignores summary/junit files)."""
    records: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("*.json")):
        if path.name == "summary.json":
            continue
        data = json.loads(path.read_text())
        if isinstance(data, dict) and "test_id" in data:
            records.append(data)
    return records
