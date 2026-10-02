from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from netharness.parsers import (
    parse_bfd_peers,
    parse_bgp_rib,
    parse_bgp_summary,
    parse_ospf6_neighbors,
    parse_ospf_neighbors,
    parse_routes,
    selected_route,
    table_summary,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_routes_selected_and_ecmp() -> None:
    table = parse_routes(load("ip_route.json"))
    r = selected_route(table, "10.0.0.2/32")
    assert r is not None
    assert (r.protocol, r.metric, r.distance) == ("ospf", 10, 110)
    assert r.interfaces == {"eth1"}
    assert r.nexthops[0].ip == "10.1.12.2"

    ecmp = selected_route(table, "10.1.23.0/30")
    assert ecmp is not None and ecmp.interfaces == {"eth1", "eth2"}


def test_selected_route_prefers_selected_candidate() -> None:
    table = parse_routes(load("ip_route.json"))
    # 10.0.0.1/32 has an unselected OSPF entry and a selected connected one
    assert len(table["10.0.0.1/32"]) == 2
    r = selected_route(table, "10.0.0.1/32")
    assert r is not None and r.protocol == "connected"


def test_unselected_and_missing_routes_return_none() -> None:
    table = parse_routes(load("ip_route.json"))
    assert selected_route(table, "10.9.9.0/24") is None  # present but not selected
    assert selected_route(table, "203.0.113.0/24") is None
    assert table["10.9.9.0/24"][0].interfaces == set()  # inactive nexthop ignored


def test_table_summary_only_selected() -> None:
    summary = table_summary(parse_routes(load("ip_route.json")))
    assert "10.9.9.0/24" not in summary
    assert summary["10.0.0.2/32"]["nexthops"] == [{"ip": "10.1.12.2", "interface": "eth1"}]
    json.dumps(summary)  # serialisable


def test_parse_ospf_neighbors() -> None:
    nbrs = {n.router_id: n for n in parse_ospf_neighbors(load("ospf_neighbors.json"))}
    assert nbrs["10.0.0.2"].is_full
    assert nbrs["10.0.0.2"].interface == "eth1"
    assert nbrs["10.0.0.2"].address == "10.1.12.2"
    # no "converged" key -> state derived from nbrState
    assert nbrs["10.0.0.3"].state == "ExStart"
    assert not nbrs["10.0.0.3"].is_full


def test_parse_ospf_neighbors_legacy_object_shape() -> None:
    legacy = {"neighbors": {"1.1.1.1": {"nbrState": "Full/DR", "ifaceName": "eth1:10.0.0.1"}}}
    (n,) = parse_ospf_neighbors(legacy)
    assert (n.state, n.role, n.interface) == ("Full", "DR", "eth1")


def test_parse_ospf_neighbors_empty() -> None:
    assert parse_ospf_neighbors({}) == []


def test_parse_bgp_summary() -> None:
    peers = {p.address: p for p in parse_bgp_summary(load("bgp_summary.json"))}
    ibgp, ebgp = peers["10.2.12.2"], peers["10.2.13.2"]
    assert ibgp.established and not ibgp.is_ebgp and ibgp.prefixes_received == 4
    assert ebgp.is_ebgp and not ebgp.established and ebgp.state == "Active"


def test_parse_bgp_summary_wrapped_afi() -> None:
    wrapped = {"ipv4Unicast": load("bgp_summary.json")}
    assert len(parse_bgp_summary(wrapped)) == 2


def test_parse_bgp_rib() -> None:
    rib = parse_bgp_rib(load("bgp_rib.json"))
    best = [p for p in rib["192.168.4.0/24"] if p.best]
    assert len(best) == 1
    assert best[0].as_path == "65003" and best[0].next_hop == "10.2.12.2"
    local = rib["10.0.0.1/32"][0]
    assert local.as_path == "" and local.best


def test_parse_bfd_peers() -> None:
    # fixture: real FRR 10.5.5 session captured in CI (r1, ospf_triangle_bfd) plus a
    # derived "down" copy. Note FRR emits no "local" key for single-hop sessions.
    peers = {p.peer: p for p in parse_bfd_peers(load("bfd_peers.json"))}
    up, down = peers["10.1.12.2"], peers["10.1.13.2"]
    assert up.up and up.interface == "eth1" and not up.multihop and up.local is None
    assert (up.receive_interval_ms, up.transmit_interval_ms, up.detect_multiplier) == (200, 200, 3)
    assert not down.up and down.status == "down"


def test_parse_bfd_peers_tolerates_missing_fields_and_wrapping() -> None:
    (p,) = parse_bfd_peers({"peers": [{"peer": "10.0.0.9", "status": "init"}]})
    assert p.status == "init" and p.transmit_interval_ms is None and p.local is None
    assert parse_bfd_peers([]) == [] and parse_bfd_peers({}) == []


def test_parse_ospf6_neighbors() -> None:
    # fixture: real FRR 10.5.5 output (r1, dual-stack triangle); r3 edited to ExStart
    nbrs = {n.router_id: n for n in parse_ospf6_neighbors(load("ospf6_neighbors.json"))}
    assert nbrs["10.0.0.2"].is_full and nbrs["10.0.0.2"].interface == "eth1"
    assert nbrs["10.0.0.3"].state == "ExStart" and not nbrs["10.0.0.3"].is_full
    assert parse_ospf6_neighbors({}) == []
