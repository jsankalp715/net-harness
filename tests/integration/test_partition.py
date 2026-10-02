"""Partitions: isolate a router by cutting all its links.

Unlike link failover there is no alternate path, so the only correct outcome is that
every other router *withdraws* the isolated router's prefixes, and the isolated router
loses everything it learned. A stale route here silently blackholes traffic.
"""

from __future__ import annotations

import time

import pytest

from netharness import FaultInjector, Lab, ScenarioRecord, wait_for_convergence
from netharness.checks import (
    all_of,
    full_loopback_reachability,
    only_connected_routes,
    prefixes_withdrawn,
)
from tests.integration.scenarios import (
    PartitionCase,
    originated_prefixes,
    partition_params,
    topology_vars,
)

pytestmark = [pytest.mark.failure]

#: after converging, the withdrawn state must hold this long (no stale route reappears)
HOLD_S = 3.0


@pytest.mark.parametrize(("lab", "case"), partition_params(), indirect=["lab"])
def test_partition_withdraws_and_heals(
    lab: Lab, case: PartitionCase, faults: FaultInjector, scenario: ScenarioRecord
) -> None:
    vars_ = topology_vars(case.topology)
    others = [n for n in lab.nodes if n != case.isolated]
    gone = originated_prefixes(vars_, case.isolated)
    scenario.event("partition_plan", isolated=case.isolated, prefixes=sorted(gone))

    reachable = full_loopback_reachability(lab)
    wait_for_convergence(reachable, lab=lab, consecutive=2)  # precondition

    withdrawn = all_of(
        prefixes_withdrawn(lab, others, gone),
        only_connected_routes(lab, case.isolated, [case.protocol]),
    )
    t_cut = faults.isolate_node(case.isolated)
    scenario.event("isolated", links=[str(lk) for lk in lab.links_of(case.isolated)])
    elapsed = wait_for_convergence(
        withdrawn, lab=lab, start=t_cut, timeout=case.max_withdraw_s + 15, consecutive=2
    )
    scenario.record_convergence("withdrawal", elapsed)
    scenario.snapshot(lab, "during_partition")
    assert elapsed <= case.max_withdraw_s, f"withdrawal took {elapsed:.2f}s"

    # the withdrawn state must be stable, not a moment between path-hunting steps
    deadline = time.monotonic() + HOLD_S
    while time.monotonic() < deadline:
        assert withdrawn(), "a withdrawn prefix reappeared while still partitioned"
        time.sleep(0.5)

    t_heal = faults.heal_node(case.isolated)
    healed = wait_for_convergence(reachable, lab=lab, start=t_heal, timeout=60, consecutive=2)
    scenario.record_convergence("heal", healed)
    assert healed <= case.max_heal_s, f"heal took {healed:.2f}s"
