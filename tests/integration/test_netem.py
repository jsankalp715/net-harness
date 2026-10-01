"""Gray failures with netem: delay that must NOT disturb OSPF, and silent 100% loss
that OSPF can only detect via its dead interval (no carrier-down signal).

Skipped automatically when the kernel lacks sch_netem (e.g. some WSL2 kernels).
"""

from __future__ import annotations

import re
import time

import pytest

from netharness import FaultInjector, Lab, Netem, ScenarioRecord, wait_for_convergence
from netharness.checks import ospf_neighbors_full, route_via
from tests.integration.scenarios import topology_vars

TOPOLOGY = "ospf_triangle"
VARS = topology_vars(TOPOLOGY)
DEAD_INTERVAL = VARS["r1"]["ospf"]["dead_interval"]

pytestmark = [pytest.mark.ospf, pytest.mark.netem, pytest.mark.failure]


@pytest.fixture
def netem_faults(faults: FaultInjector, lab: Lab) -> FaultInjector:
    if not faults.netem_supported(lab.link_between("r1", "r2").a):
        pytest.skip("sch_netem not available in this kernel")
    return faults


def _avg_rtt_ms(ping_output: str) -> float:
    match = re.search(r"= [\d.]+/([\d.]+)/", ping_output)
    assert match, ping_output
    return float(match.group(1))


@pytest.mark.parametrize("delay_ms", [50, 150])
def test_delay_keeps_adjacency_and_path(
    lab: Lab, netem_faults: FaultInjector, scenario: ScenarioRecord, delay_ms: int
) -> None:
    link = lab.link_between("r1", "r2")
    peer_ip = VARS["r2"]["interfaces"][link.b.interface]["address"].split("/")[0]
    with netem_faults.impairment(link.a, Netem(delay_ms=delay_ms)):
        rtt = _avg_rtt_ms(lab.exec("r1", ["ping", "-c", "3", "-q", peer_ip]).stdout)
        scenario.event("rtt", avg_ms=rtt)
        assert rtt >= delay_ms * 0.9

        # hold for longer than the dead interval: adjacency and path must stay put
        time.sleep(DEAD_INTERVAL + 1)
        assert ospf_neighbors_full(lab, "r1", [VARS["r2"]["router_id"]])()
        assert route_via(lab, "r1", VARS["r2"]["loopback"], link.a.interface, "ospf")()


def test_silent_loss_detected_by_dead_interval(
    lab: Lab, netem_faults: FaultInjector, scenario: ScenarioRecord
) -> None:
    link = lab.link_between("r1", "r2")
    prefix = VARS["r2"]["loopback"]
    total_loss = Netem(loss_pct=100)

    netem_faults.apply_netem(link.a, total_loss)
    netem_faults.apply_netem(link.b, total_loss)
    t0 = time.monotonic()
    elapsed = wait_for_convergence(
        route_via(lab, "r1", prefix, "eth2", "ospf"), lab=lab, start=t0, timeout=DEAD_INTERVAL + 15
    )
    scenario.record_convergence("dead_interval_failover", elapsed)
    scenario.snapshot(lab, "during_failure")
    # Carrier stays up, so detection must have waited for (most of) the dead interval.
    assert elapsed >= DEAD_INTERVAL - 1.5, f"too fast ({elapsed:.2f}s) for timer-based detection"
    assert elapsed <= DEAD_INTERVAL + 5

    netem_faults.clear_netem(link.a)
    netem_faults.clear_netem(link.b)
    t1 = time.monotonic()
    scenario.record_convergence(
        "loss_cleared_restore",
        wait_for_convergence(route_via(lab, "r1", prefix, "eth1", "ospf"), lab=lab, start=t1),
    )
