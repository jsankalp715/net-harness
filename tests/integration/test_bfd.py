"""BFD: sessions come up on every link, and silent loss is detected in well under a
second instead of waiting for protocol timers (OSPF dead interval / BGP hold time).

Silent loss = 100% netem loss with carrier still up, so the protocols get no
interface-down signal. Skipped automatically when the kernel lacks sch_netem.
"""

from __future__ import annotations

import time

import pytest

from netharness import FaultInjector, Lab, Netem, ScenarioRecord, wait_for_convergence
from netharness.checks import bfd_peers_up, route_via
from tests.integration.scenarios import (
    SilentLossCase,
    bfd_neighbors,
    silent_loss_params,
    topology_vars,
)

BFD_TOPOLOGIES = ["ospf_triangle_bfd", "bgp_ring_bfd"]


@pytest.mark.bfd
@pytest.mark.parametrize("lab", BFD_TOPOLOGIES, indirect=True)
def test_bfd_sessions_up_with_configured_timers(lab: Lab, scenario: ScenarioRecord) -> None:
    vars_ = topology_vars(lab.topology_file.name.removesuffix(".clab.yml"))
    for node in lab.nodes:
        expected = bfd_neighbors(vars_, node)
        wait_for_convergence(bfd_peers_up(lab, node, expected), lab=lab, timeout=30)

        bfd = vars_[node]["bfd"]
        raw = lab.vtysh_json(node, "show bfd peers json")
        scenario.event("bfd_peers", node=node, raw=raw)  # real schema kept in the log
        peers = {p.peer: p for p in lab.get_bfd_peers(node)}
        assert expected <= set(peers), f"{node}: missing BFD peers {expected - set(peers)}"
        for addr in expected:
            p = peers[addr]
            assert p.up and not p.multihop
            if p.transmit_interval_ms is not None:
                assert p.transmit_interval_ms == bfd["interval_ms"]
            if p.detect_multiplier is not None:
                assert p.detect_multiplier == bfd["multiplier"]


@pytest.mark.netem
@pytest.mark.failure
@pytest.mark.parametrize(("lab", "case"), silent_loss_params(), indirect=["lab"])
def test_silent_loss_detection(
    lab: Lab, case: SilentLossCase, faults: FaultInjector, scenario: ScenarioRecord
) -> None:
    link = lab.link_between(*case.cut)
    if not faults.netem_supported(link.a):
        pytest.skip("sch_netem not available in this kernel")
    primary = route_via(lab, case.observer, case.prefix, case.primary_iface, case.protocol)
    backup = route_via(lab, case.observer, case.prefix, case.backup_iface, case.protocol)
    wait_for_convergence(primary, lab=lab, consecutive=2)

    total_loss = Netem(loss_pct=100)
    faults.apply_netem(link.a, total_loss)
    faults.apply_netem(link.b, total_loss)
    t0 = time.monotonic()
    scenario.event("silent_loss", link=str(link), bfd=case.bfd)
    elapsed = wait_for_convergence(
        backup, lab=lab, start=t0, timeout=case.max_detect_s + 15, consecutive=2
    )
    scenario.record_convergence("silent_loss_failover", elapsed)
    scenario.snapshot(lab, "during_failure")
    assert elapsed >= case.min_detect_s, f"{elapsed:.2f}s: too fast for timer-based detection"
    assert elapsed <= case.max_detect_s, f"{elapsed:.2f}s: detection too slow"

    faults.clear_netem(link.a)
    faults.clear_netem(link.b)
    t1 = time.monotonic()
    scenario.record_convergence(
        "loss_cleared_restore",
        wait_for_convergence(primary, lab=lab, start=t1, timeout=60, consecutive=2),
    )
