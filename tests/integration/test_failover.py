"""Link failure -> alternate path, and link restore -> original path, with timing.

Parametrized over FAILOVER_CASES (both topologies). ``lab`` is indirectly
parametrized and module-scoped, so pytest groups cases by topology and deploys
each topology once for this module.
"""

from __future__ import annotations

import pytest

from netharness import FaultInjector, Lab, ScenarioRecord, wait_for_convergence
from netharness.checks import route_via
from tests.integration.scenarios import FailoverCase, failover_params

pytestmark = [pytest.mark.failure]


def _wait_primary(lab: Lab, case: FailoverCase, start: float | None = None) -> float:
    return wait_for_convergence(
        route_via(lab, case.observer, case.prefix, case.primary_iface, case.rib_protocol),
        lab=lab,
        start=start,
        consecutive=2,
    )


def _wait_backup(lab: Lab, case: FailoverCase, start: float) -> float:
    return wait_for_convergence(
        route_via(lab, case.observer, case.prefix, case.backup_iface, case.rib_protocol),
        lab=lab,
        start=start,
        consecutive=2,
    )


@pytest.mark.parametrize(("lab", "case"), failover_params(), indirect=["lab"])
def test_link_failure_reconverges(
    lab: Lab, case: FailoverCase, faults: FaultInjector, scenario: ScenarioRecord
) -> None:
    link = lab.link_between(*case.cut)
    _wait_primary(lab, case)  # precondition: traffic on the primary path

    t_cut = faults.link_down(link)
    scenario.event("link_down", link=str(link))
    elapsed = _wait_backup(lab, case, start=t_cut)
    scenario.record_convergence("failover", elapsed)
    scenario.snapshot(lab, "during_failure")

    route = lab.get_route(case.observer, case.prefix)
    assert route is not None and route.interfaces == {case.backup_iface}
    assert elapsed <= case.max_failover_s, f"failover took {elapsed:.2f}s"


@pytest.mark.parametrize(("lab", "case"), failover_params(), indirect=["lab"])
def test_link_restore_returns_to_primary(
    lab: Lab, case: FailoverCase, faults: FaultInjector, scenario: ScenarioRecord
) -> None:
    link = lab.link_between(*case.cut)
    _wait_primary(lab, case)

    t_cut = faults.link_down(link)
    scenario.record_convergence("failover", _wait_backup(lab, case, start=t_cut))
    scenario.snapshot(lab, "during_failure")

    t_up = faults.link_up(link)
    scenario.event("link_up", link=str(link))
    elapsed = _wait_primary(lab, case, start=t_up)
    scenario.record_convergence("restore", elapsed)

    route = lab.get_route(case.observer, case.prefix)
    assert route is not None and route.interfaces == {case.primary_iface}
    assert elapsed <= case.max_restore_s, f"restore took {elapsed:.2f}s"
