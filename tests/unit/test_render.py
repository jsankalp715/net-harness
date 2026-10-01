from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import UndefinedError

from netharness.constants import TOPOLOGY_DIR
from netharness.render import deep_merge, load_vars, render_node_config


def test_deep_merge_nested_override() -> None:
    base = {"ospf": {"area": "0", "cost": 10}, "x": [1]}
    merged = deep_merge(base, {"ospf": {"cost": 5}, "x": [2]})
    assert merged == {"ospf": {"area": "0", "cost": 5}, "x": [2]}
    assert base["ospf"]["cost"] == 10  # input not mutated


def test_ospf_render_has_interfaces_and_router_block() -> None:
    nodes = load_vars(TOPOLOGY_DIR / "ospf_triangle.vars.yml")
    cfg = render_node_config("r1", nodes["r1"])
    assert "hostname r1" in cfg
    assert " ip address 10.1.12.1/30" in cfg
    assert " ip ospf network point-to-point" in cfg
    assert " ip ospf dead-interval 4" in cfg
    assert "router ospf\n ospf router-id 10.0.0.1" in cfg
    assert " timers throttle lsa all 100" in cfg
    assert "router bgp" not in cfg


def test_bgp_render_ibgp_gets_next_hop_self_only() -> None:
    nodes = load_vars(TOPOLOGY_DIR / "bgp_ring.vars.yml")
    cfg = render_node_config("r1", nodes["r1"])
    assert "router bgp 65001" in cfg
    assert " neighbor 10.2.13.2 remote-as 65002" in cfg
    assert "  neighbor 10.2.12.2 next-hop-self" in cfg  # iBGP peer
    assert "10.2.13.2 next-hop-self" not in cfg  # eBGP peer
    assert "  network 192.168.1.0/24" in cfg
    assert "ip route 192.168.1.0/24 blackhole" in cfg
    assert "router ospf" not in cfg


def test_missing_variable_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(UndefinedError):
        render_node_config("rX", {"interfaces": {}})  # no loopback
