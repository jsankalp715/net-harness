"""Dual-stack: OSPFv3 and BGP over IPv6, running alongside the IPv4 control plane.

The OSPF triangle and BGP ring carry IPv6 too (vars: ``address6``, ``loopback6``,
``networks6``, ``neighbors6``). These tests mirror the IPv4 ones for the IPv6 family and
add an end-to-end ping, proving the data plane forwards and not just that the RIB is
right. IPv6 failover lives in ``test_failover.py`` (cases ``ospf6-*`` / ``bgp6-*``).
"""

from __future__ import annotations

import pytest

from netharness import Lab, ScenarioRecord, wait_for_convergence
from netharness.checks import bgp_established, ospf_neighbors_full, route_via
from tests.integration.scenarios import BGP_EXPECTED_BEST, BgpExpectation, topology_vars

OSPF = "ospf_triangle"
BGP = "bgp_ring"
OSPF_VARS = topology_vars(OSPF)
BGP_VARS = topology_vars(BGP)

pytestmark = [pytest.mark.ipv6]


def v6_customer_prefix(v4_prefix: str) -> str:
    """192.168.N.0/24 -> 2001:db8:N::/48 (the addressing convention in bgp_ring.vars.yml)."""
    return f"2001:db8:{v4_prefix.split('.')[2]}::/48"


def loopback6(vars_: dict[str, dict[str, object]], node: str) -> str:
    return str(vars_[node]["loopback6"]).split("/")[0]


# ------------------------------------------------------------------- OSPFv3
@pytest.mark.ospf
@pytest.mark.parametrize("lab", [OSPF], indirect=True)
@pytest.mark.parametrize("node", sorted(OSPF_VARS))
def test_ospf6_adjacency_full(lab: Lab, scenario: ScenarioRecord, node: str) -> None:
    expected = {
        str(OSPF_VARS[i["peer"]]["router_id"]): eth
        for eth, i in OSPF_VARS[node]["interfaces"].items()
    }
    wait_for_convergence(ospf_neighbors_full(lab, node, expected, "ipv6"), lab=lab, timeout=30)
    nbrs = {n.router_id: n for n in lab.get_ospf_neighbors(node, "ipv6")}
    assert set(nbrs) == set(expected)
    for rid, eth in expected.items():
        assert nbrs[rid].is_full and nbrs[rid].interface == eth


@pytest.mark.ospf
@pytest.mark.parametrize("lab", [OSPF], indirect=True)
@pytest.mark.parametrize(
    ("node", "dest"),
    [(n, d) for n in sorted(OSPF_VARS) for d in sorted(OSPF_VARS) if n != d],
    ids=lambda v: str(v),
)
def test_ospf6_route_learning(lab: Lab, scenario: ScenarioRecord, node: str, dest: str) -> None:
    """Every IPv6 loopback is learned via OSPFv3 over the direct (cheapest) link."""
    prefix = str(OSPF_VARS[dest]["loopback6"])
    iface = next(e for e, i in OSPF_VARS[node]["interfaces"].items() if i["peer"] == dest)
    wait_for_convergence(route_via(lab, node, prefix, iface, "ospf6"), lab=lab, timeout=30)
    route = lab.get_route(node, prefix)
    assert route is not None and route.metric == OSPF_VARS[node]["ospf"]["default_cost"]


# ------------------------------------------------------------------- BGP over IPv6
@pytest.mark.bgp
@pytest.mark.parametrize("lab", [BGP], indirect=True)
@pytest.mark.parametrize("node", sorted(BGP_VARS))
def test_bgp6_sessions_established(lab: Lab, scenario: ScenarioRecord, node: str) -> None:
    bgp = BGP_VARS[node]["bgp"]
    expected = {n["address"]: n["remote_as"] for n in bgp["neighbors6"]}
    wait_for_convergence(bgp_established(lab, node, expected, "ipv6"), lab=lab, timeout=30)
    peers = {p.address: p for p in lab.get_bgp_peers(node, "ipv6")}
    assert set(peers) == set(expected)
    for addr, remote_as in expected.items():
        assert peers[addr].established and peers[addr].remote_as == remote_as
        assert peers[addr].is_ebgp == (remote_as != bgp["asn"])


@pytest.mark.bgp
@pytest.mark.parametrize("lab", [BGP], indirect=True)
@pytest.mark.parametrize("exp", BGP_EXPECTED_BEST, ids=lambda e: f"{e.node}-{e.prefix}")
def test_bgp6_best_path_matches_ipv4(
    lab: Lab, scenario: ScenarioRecord, exp: BgpExpectation
) -> None:
    """IPv6 best paths mirror IPv4 exactly: same AS path, same egress interface."""
    prefix = v6_customer_prefix(exp.prefix)
    wait_for_convergence(route_via(lab, exp.node, prefix, exp.egress, "bgp"), lab=lab)
    best = [p for p in lab.get_bgp_rib(exp.node, "ipv6")[prefix] if p.best]
    assert len(best) == 1 and best[0].as_path == exp.as_path


# ------------------------------------------------------------------- data plane
@pytest.mark.parametrize("lab", [OSPF, BGP], indirect=True)
def test_ipv6_loopback_ping_full_mesh(lab: Lab, scenario: ScenarioRecord) -> None:
    """Every router pings every other router's IPv6 loopback, sourced from its own."""
    vars_ = topology_vars(lab.topology_file.name.removesuffix(".clab.yml"))
    failures = []
    for src in lab.nodes:
        for dst in lab.nodes:
            if src == dst:
                continue
            res = lab.exec(
                src,
                ["ping", "-6", "-c", "2", "-W", "2", "-q", "-I",
                 loopback6(vars_, src), loopback6(vars_, dst)],
                check=False,
            )  # fmt: skip
            if not res.ok:
                failures.append(f"{src} -> {dst}: {res.stdout.strip()[-120:]}")
    scenario.event("ping6", pairs=len(lab.nodes) * (len(lab.nodes) - 1), failed=len(failures))
    assert not failures, "\n".join(failures)
