"""OSPF triangle: adjacency formation, route learning, ECMP."""

from __future__ import annotations

import pytest

from netharness import Lab, ScenarioRecord, wait_for_convergence
from netharness.checks import ospf_neighbors_full, route_via
from tests.integration.scenarios import topology_vars

TOPOLOGY = "ospf_triangle"
VARS = topology_vars(TOPOLOGY)
NODES = sorted(VARS)

pytestmark = [pytest.mark.ospf]


def _expected_neighbors(node: str) -> dict[str, str]:
    """router-id -> local interface, derived from the vars file."""
    return {
        VARS[intf["peer"]]["router_id"]: ifname for ifname, intf in VARS[node]["interfaces"].items()
    }


def _direct_iface(node: str, dest: str) -> str:
    return next(i for i, d in VARS[node]["interfaces"].items() if d["peer"] == dest)


def test_initial_convergence(lab: Lab, scenario: ScenarioRecord) -> None:
    assert lab.initial_convergence_s is not None
    scenario.record_convergence("initial", lab.initial_convergence_s)
    assert lab.initial_convergence_s < 30


@pytest.mark.parametrize("node", NODES)
def test_ospf_adjacency_full(lab: Lab, scenario: ScenarioRecord, node: str) -> None:
    expected = _expected_neighbors(node)
    wait_for_convergence(ospf_neighbors_full(lab, node, expected), lab=lab, timeout=20)

    neighbors = {n.router_id: n for n in lab.get_ospf_neighbors(node)}
    scenario.event("neighbors", neighbors={rid: n.state for rid, n in neighbors.items()})
    assert set(neighbors) == set(expected), "unexpected/missing OSPF neighbors"
    for rid, iface in expected.items():
        assert neighbors[rid].is_full, f"{node}: {rid} is {neighbors[rid].state}"
        assert neighbors[rid].interface == iface


@pytest.mark.parametrize(
    ("node", "dest"),
    [(n, d) for n in NODES for d in NODES if n != d],
    ids=lambda v: str(v),
)
def test_ospf_route_learning(lab: Lab, scenario: ScenarioRecord, node: str, dest: str) -> None:
    """Every loopback is learned via OSPF over the direct (cheapest) link."""
    prefix = VARS[dest]["loopback"]
    iface = _direct_iface(node, dest)
    wait_for_convergence(route_via(lab, node, prefix, iface, protocol="ospf"), lab=lab, timeout=20)

    route = lab.get_route(node, prefix)
    assert route is not None
    assert route.protocol == "ospf"
    assert route.metric == VARS[node]["ospf"]["default_cost"]
    assert route.interfaces == {iface}


def test_ospf_ecmp_to_remote_transit(lab: Lab, scenario: ScenarioRecord) -> None:
    """r1 reaches the r2-r3 transit /30 over two equal-cost paths (via r2 and via r3)."""
    prefix = "10.1.23.0/30"
    wait_for_convergence(route_via(lab, "r1", prefix, {"eth1", "eth2"}, "ospf"), lab=lab)
    route = lab.get_route("r1", prefix)
    assert route is not None
    assert route.interfaces == {"eth1", "eth2"}
    assert route.metric == 2 * VARS["r1"]["ospf"]["default_cost"]
