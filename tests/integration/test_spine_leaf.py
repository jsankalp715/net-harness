"""2-spine / 4-leaf eBGP fabric: ECMP, uplink failure, and whole-spine failure.

Every leaf normally reaches every other leaf over *both* uplinks (ECMP). Losing an
uplink or a whole spine must collapse the affected paths onto the surviving spine,
and restoring it must bring ECMP back. Losing a spine touches all 4 leaves at once,
which is where fabric size shows up in convergence time.
"""

from __future__ import annotations

import pytest

from netharness import FaultInjector, Lab, ScenarioRecord, wait_for_convergence
from netharness.checks import (
    Predicate,
    all_of,
    bgp_established,
    prefixes_withdrawn,
    route_via,
)
from tests.integration.scenarios import topology_vars

TOPOLOGY = "spine_leaf"
VARS = topology_vars(TOPOLOGY)
LEAVES = sorted(n for n in VARS if n.startswith("leaf"))
SPINES = sorted(n for n in VARS if n.startswith("spine"))
UPLINKS = {"eth1", "eth2"}  # leafN:eth1 -> spine1, leafN:eth2 -> spine2

pytestmark = [pytest.mark.bgp]


def server_prefix(leaf: str) -> str:
    return str(VARS[leaf]["bgp"]["networks"][1])


def uplink_to(spine: str) -> str:
    return f"eth{SPINES.index(spine) + 1}"


def leaf_paths(lab: Lab, via: set[str], *, src: list[str], dst: list[str]) -> Predicate:
    """Every leaf in ``src`` reaches every other leaf's server prefix in ``dst`` via ``via``."""
    return all_of(
        *(route_via(lab, s, server_prefix(d), via, "bgp") for s in src for d in dst if s != d)
    )


def test_initial_convergence(lab: Lab, scenario: ScenarioRecord) -> None:
    assert lab.initial_convergence_s is not None
    scenario.record_convergence("initial", lab.initial_convergence_s)
    assert lab.initial_convergence_s < 60


@pytest.mark.parametrize("node", SPINES + LEAVES)
def test_fabric_sessions_established(lab: Lab, scenario: ScenarioRecord, node: str) -> None:
    peers = [n["address"] for n in VARS[node]["bgp"]["neighbors"]]
    assert len(peers) == (len(LEAVES) if node in SPINES else len(SPINES))
    wait_for_convergence(bgp_established(lab, node, peers), lab=lab, timeout=30)


@pytest.mark.parametrize(
    ("src", "dst"), [(s, d) for s in LEAVES for d in LEAVES if s != d], ids=lambda v: str(v)
)
def test_leaf_to_leaf_ecmp(lab: Lab, scenario: ScenarioRecord, src: str, dst: str) -> None:
    """Both uplinks carry traffic to every remote leaf (2-way ECMP)."""
    prefix = server_prefix(dst)
    wait_for_convergence(route_via(lab, src, prefix, UPLINKS, "bgp"), lab=lab, timeout=30)
    route = lab.get_route(src, prefix)
    assert route is not None and route.interfaces == UPLINKS
    best = [p for p in lab.get_bgp_rib(src)[prefix] if p.best]
    assert best and best[0].as_path == f"65100 {VARS[dst]['bgp']['asn']}"


@pytest.mark.failure
def test_uplink_failure_collapses_ecmp(
    lab: Lab, faults: FaultInjector, scenario: ScenarioRecord
) -> None:
    """leaf1 loses its spine1 uplink: leaf1 -> others and others -> leaf1 drop to spine2 only."""
    leaf, spine = LEAVES[0], SPINES[0]
    others = [n for n in LEAVES if n != leaf]
    survivor = {uplink_to(SPINES[1])}
    ecmp = all_of(
        leaf_paths(lab, UPLINKS, src=[leaf], dst=others),
        leaf_paths(lab, UPLINKS, src=others, dst=[leaf]),
    )
    single = all_of(
        leaf_paths(lab, survivor, src=[leaf], dst=others),
        leaf_paths(lab, survivor, src=others, dst=[leaf]),
    )
    wait_for_convergence(ecmp, lab=lab, consecutive=2)

    link = lab.link_between(leaf, spine)
    t_cut = faults.link_down(link)
    scenario.record_convergence(
        "failover", wait_for_convergence(single, lab=lab, start=t_cut, consecutive=2)
    )
    scenario.snapshot(lab, "during_failure")

    t_up = faults.link_up(link)
    healed = wait_for_convergence(ecmp, lab=lab, start=t_up, timeout=60, consecutive=2)
    scenario.record_convergence("restore", healed)
    assert healed <= 20


@pytest.mark.failure
def test_spine_failure_reroutes_whole_fabric(
    lab: Lab, faults: FaultInjector, scenario: ScenarioRecord
) -> None:
    """spine1 fails (all 4 links): all 12 leaf-pair paths collapse onto spine2, then recover."""
    dead, alive = SPINES[0], SPINES[1]
    ecmp = leaf_paths(lab, UPLINKS, src=LEAVES, dst=LEAVES)
    single = leaf_paths(lab, {uplink_to(alive)}, src=LEAVES, dst=LEAVES)
    wait_for_convergence(ecmp, lab=lab, consecutive=2)

    t_cut = faults.isolate_node(dead)
    scenario.event("spine_down", spine=dead, links=len(lab.links_of(dead)))
    elapsed = wait_for_convergence(single, lab=lab, start=t_cut, consecutive=2)
    scenario.record_convergence("failover", elapsed)
    scenario.snapshot(lab, "during_failure")
    assert elapsed <= 10, f"fabric took {elapsed:.2f}s to drain a dead spine"

    t_heal = faults.heal_node(dead)
    healed = wait_for_convergence(ecmp, lab=lab, start=t_heal, timeout=60, consecutive=2)
    scenario.record_convergence("restore", healed)
    assert healed <= 20


def test_spines_never_learn_each_other(lab: Lab, scenario: ScenarioRecord) -> None:
    """Loop prevention: spine2's loopback reaches spine1 only via spine->leaf->spine, so
    spine1 must reject it (own AS in path). Declared in vars as expect.unreachable."""
    for me, other in ((SPINES[0], SPINES[1]), (SPINES[1], SPINES[0])):
        assert frozenset((me, other)) in lab.unreachable_pairs
        loopback = str(VARS[other]["loopback"])
        assert prefixes_withdrawn(lab, [me], [loopback])(), f"{me} learned {loopback}"
        # ...while every leaf does reach it
        for leaf in LEAVES:
            assert lab.get_route(leaf, loopback) is not None
