"""BGP ring: eBGP + iBGP sessions, prefix advertisement and receipt, best-path selection."""

from __future__ import annotations

import pytest

from netharness import Lab, ScenarioRecord, wait_for_convergence
from netharness.checks import bgp_established, route_via
from tests.integration.scenarios import BGP_EXPECTED_BEST, BgpExpectation, topology_vars

TOPOLOGY = "bgp_ring"
VARS = topology_vars(TOPOLOGY)
NODES = sorted(VARS)

pytestmark = [pytest.mark.bgp]


def test_initial_convergence(lab: Lab, scenario: ScenarioRecord) -> None:
    assert lab.initial_convergence_s is not None
    scenario.record_convergence("initial", lab.initial_convergence_s)
    assert lab.initial_convergence_s < 45


@pytest.mark.parametrize("node", NODES)
def test_bgp_sessions_established(lab: Lab, scenario: ScenarioRecord, node: str) -> None:
    bgp = VARS[node]["bgp"]
    expected = {n["address"]: n["remote_as"] for n in bgp["neighbors"]}
    wait_for_convergence(bgp_established(lab, node, expected), lab=lab, timeout=30)

    peers = {p.address: p for p in lab.get_bgp_peers(node)}
    assert set(peers) == set(expected)
    for address, remote_as in expected.items():
        peer = peers[address]
        assert peer.established
        assert peer.remote_as == remote_as
        # session type must match the ASN relationship declared in the vars file
        assert peer.is_ebgp == (remote_as != bgp["asn"])
    scenario.event(
        "sessions",
        peers={
            a: {"as": p.remote_as, "ebgp": p.is_ebgp, "pfx": p.prefixes_received}
            for a, p in peers.items()
        },
    )


@pytest.mark.parametrize("node", NODES)
def test_prefix_advertisement(lab: Lab, scenario: ScenarioRecord, node: str) -> None:
    """Each node originates its networks and advertises them to every peer."""
    bgp = VARS[node]["bgp"]
    rib = lab.get_bgp_rib(node)
    for prefix in bgp["networks"]:
        local = [p for p in rib.get(prefix, []) if p.best]
        assert local, f"{node} does not originate {prefix}"
        assert local[0].as_path == "", f"{node}: {prefix} should be locally originated"

    for nbr in bgp["neighbors"]:

        def advertised(addr: str = nbr["address"]) -> bool:
            data = lab.vtysh_json(node, f"show bgp ipv4 unicast neighbors {addr} advertised-routes")
            return set(bgp["networks"]) <= set(data.get("advertisedRoutes", {}))

        wait_for_convergence(
            advertised, lab=lab, timeout=20, description=f"{node} advertises to {nbr['address']}"
        )


@pytest.mark.parametrize("exp", BGP_EXPECTED_BEST, ids=lambda e: f"{e.node}-{e.prefix}")
def test_prefix_receipt_best_path(lab: Lab, scenario: ScenarioRecord, exp: BgpExpectation) -> None:
    """Remote prefixes are received, best path has the expected AS path and egress."""
    wait_for_convergence(route_via(lab, exp.node, exp.prefix, exp.egress, "bgp"), lab=lab)
    best = [p for p in lab.get_bgp_rib(exp.node)[exp.prefix] if p.best]
    assert len(best) == 1
    assert best[0].as_path == exp.as_path


@pytest.mark.parametrize("node", NODES)
def test_as_path_loop_prevention(lab: Lab, scenario: ScenarioRecord, node: str) -> None:
    """A node never accepts its own customer prefix back from the ring."""
    own = VARS[node]["bgp"]["networks"][1]
    paths = lab.get_bgp_rib(node)[own]
    asn = str(VARS[node]["bgp"]["asn"])
    assert all(asn not in p.as_path.split() for p in paths)
    assert [p.as_path for p in paths if p.best] == [""]
