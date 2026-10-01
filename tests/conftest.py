"""Shared fixtures and the per-scenario JSON reporting hook.

Fixtures
--------
lab        module-scoped deployed :class:`Lab`. Topology comes from the module-level
           ``TOPOLOGY`` constant, or from indirect parametrization
           (``@pytest.mark.parametrize("lab", ["ospf_triangle"], indirect=True)``).
faults     function-scoped :class:`FaultInjector`; restores links/netem and waits for
           the lab to re-converge on teardown so scenarios never leak state.
scenario   function-scoped :class:`ScenarioRecord`; snapshots routing tables
           "before"/"after" automatically and is written to ``results/<test>.json``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from netharness import constants
from netharness.checks import full_loopback_reachability, steady_state
from netharness.clab import Containerlab
from netharness.convergence import wait_for_convergence
from netharness.faults import FaultInjector
from netharness.lab import Lab
from netharness.results import ScenarioRecord, utc_now, write_record

log = logging.getLogger("netharness.tests")

RECORD_KEY = pytest.StashKey[ScenarioRecord]()
REPORTS_KEY = pytest.StashKey[dict[str, pytest.TestReport]]()
SESSION_RECORDS_KEY = pytest.StashKey[list[ScenarioRecord]]()
CLEARED_KEY = pytest.StashKey[bool]()

TESTS_DIR = Path(__file__).parent


# --------------------------------------------------------------------------- options
def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("netharness")
    group.addoption(
        "--keep-lab",
        action="store_true",
        default=os.environ.get("NETHARNESS_KEEP_LAB") == "1",
        help="do not destroy labs after their module (debugging)",
    )
    group.addoption(
        "--results-dir",
        default=str(constants.RESULTS_DIR),
        help="where per-scenario JSON logs are written (default: results/)",
    )
    group.addoption(
        "--keep-results",
        action="store_true",
        help="don't clear the previous run's scenario logs at session start",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.stash[SESSION_RECORDS_KEY] = []


def _clear_previous_run(config: pytest.Config, results_dir: Path) -> None:
    """Drop the previous run's scenario logs so results/ reflects exactly this run.

    Called lazily before the first scenario log of a session is written, so runs that
    produce no scenario logs (e.g. ``make test-unit``) never touch results/.
    """
    if config.stash.get(CLEARED_KEY, False):
        return
    config.stash[CLEARED_KEY] = True
    if config.getoption("--keep-results"):
        return
    for path in results_dir.glob("*.json"):
        if path.name == "summary.json" or _is_scenario_log(path):
            path.unlink()


def _is_scenario_log(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and "test_id" in data and "schema_version" in data


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark tests by directory so ``-m unit`` / ``-m integration`` just work."""
    for item in items:
        path = Path(str(item.fspath))
        if (TESTS_DIR / "integration") in path.parents:
            item.add_marker(pytest.mark.integration)
        elif (TESTS_DIR / "unit") in path.parents:
            item.add_marker(pytest.mark.unit)


# --------------------------------------------------------------------------- fixtures
def _require_containerlab() -> None:
    if Containerlab.available():
        return
    msg = f"containerlab binary {constants.CLAB_BIN!r} not found on PATH"
    if os.environ.get("NETHARNESS_REQUIRE_LAB") == "1":
        pytest.fail(msg)  # CI: missing tooling is a failure, not a skip
    pytest.skip(msg)


@pytest.fixture(scope="module")
def lab(request: pytest.FixtureRequest) -> Iterator[Lab]:
    topology = getattr(request, "param", None) or getattr(request.module, "TOPOLOGY", None)
    if topology is None:
        raise pytest.UsageError(f"{request.module.__name__}: set TOPOLOGY or parametrize 'lab'")
    _require_containerlab()

    the_lab = Lab(topology)
    try:
        the_lab.deploy(configure=False)
        t0 = time.monotonic()
        the_lab.configure_all()
        the_lab.initial_convergence_s = wait_for_convergence(
            full_loopback_reachability(the_lab),
            timeout=constants.DEFAULT_CONVERGENCE_TIMEOUT,
            lab=the_lab,
            start=t0,
            consecutive=3,
        )
        wait_for_convergence(steady_state(the_lab), lab=the_lab, consecutive=3)
        log.info("lab %s converged after %.2fs", the_lab.name, the_lab.initial_convergence_s)
        yield the_lab
    finally:
        if request.config.getoption("--keep-lab"):
            log.warning("--keep-lab: leaving %s running", the_lab.name)
        else:
            the_lab.destroy()


@pytest.fixture
def faults(lab: Lab, scenario: ScenarioRecord) -> Iterator[FaultInjector]:
    # Depends on `scenario` so this teardown (restore + reconverge) runs *before*
    # the scenario's "after" snapshot.
    injector = FaultInjector(lab)
    yield injector
    injector.restore_all()
    wait_for_convergence(steady_state(lab), lab=lab, consecutive=3, description="post-test restore")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


@pytest.fixture
def scenario(request: pytest.FixtureRequest) -> Iterator[ScenarioRecord]:
    node = request.node
    callspec = getattr(node, "callspec", None)
    record = ScenarioRecord(
        test_id=node.nodeid,
        name=node.name,
        markers=sorted({m.name for m in node.iter_markers()} - {"parametrize"}),
        params={k: _jsonable(v) for k, v in (callspec.params if callspec else {}).items()},
    )
    node.stash[RECORD_KEY] = record

    the_lab: Lab | None = request.getfixturevalue("lab") if "lab" in request.fixturenames else None
    if the_lab is not None:
        record.topology = the_lab.name
        record.snapshot(the_lab, "before")
    yield record
    if the_lab is not None:
        record.snapshot(the_lab, "after")


# --------------------------------------------------------------------------- reporting
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    outcome = yield
    report: pytest.TestReport = outcome.get_result()  # type: ignore[attr-defined]
    reports = item.stash.setdefault(REPORTS_KEY, {})
    reports[report.when] = report
    if report.when != "teardown":
        return

    record = item.stash.get(RECORD_KEY, None)
    if record is None:
        if item.get_closest_marker("integration") is None:
            return  # unit tests don't produce scenario logs
        record = ScenarioRecord(
            test_id=item.nodeid,
            name=item.name,
            markers=sorted({m.name for m in item.iter_markers()} - {"parametrize"}),
        )

    status, error = _final_outcome(reports)
    record.finish(status, error)
    results_dir = Path(item.config.getoption("--results-dir"))
    _clear_previous_run(item.config, results_dir)
    path = write_record(record, results_dir)
    item.config.stash[SESSION_RECORDS_KEY].append(record)
    log.debug("scenario log: %s", path)


def _final_outcome(reports: dict[str, pytest.TestReport]) -> tuple[str, str | None]:
    setup, call, teardown = (reports.get(w) for w in ("setup", "call", "teardown"))
    for phase, rep in (("setup", setup), ("call", call), ("teardown", teardown)):
        if rep is None:
            continue
        if rep.skipped:
            return "skipped", _longrepr(rep)
        if rep.failed:
            kind = "failed" if phase == "call" else "error"
            return kind, _longrepr(rep)
    return "passed", None


def _longrepr(rep: pytest.TestReport) -> str:
    if isinstance(rep.longrepr, tuple):  # skip: (file, line, reason)
        return str(rep.longrepr[2])
    return str(rep.longrepr)[-8000:]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    records = session.config.stash.get(SESSION_RECORDS_KEY, [])
    if not records:
        return
    results_dir = Path(session.config.getoption("--results-dir"))
    counts: dict[str, int] = {}
    for r in records:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    summary = {
        "finished_at": utc_now(),
        "exit_status": int(exitstatus),
        "counts": counts,
        "scenarios": [
            {
                "test_id": r.test_id,
                "outcome": r.outcome,
                "topology": r.topology,
                "duration_s": r.duration_s,
                "convergence_s": r.convergence_s,
            }
            for r in records
        ],
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    records = [r for r in config.stash.get(SESSION_RECORDS_KEY, []) if r.convergence_s]
    if not records:
        return
    terminalreporter.section("convergence times")
    for r in records:
        for label, seconds in r.convergence_s.items():
            terminalreporter.write_line(f"{seconds:8.3f}s  {label:<22} {r.test_id}")
