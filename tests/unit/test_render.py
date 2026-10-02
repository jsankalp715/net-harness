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


def test_vars_extends_merges_onto_parent(tmp_path: Path) -> None:
    (tmp_path / "base.vars.yml").write_text(
        "defaults: {ospf: {area: 0, cost: 10}}\nnodes: {r1: {loopback: 10.0.0.1/32}}\n",
        encoding="utf-8",
    )
    (tmp_path / "child.vars.yml").write_text(
        "extends: base.vars.yml\ndefaults: {ospf: {cost: 5}, bfd: {profile: fast}}\n",
        encoding="utf-8",
    )
    nodes = load_vars(tmp_path / "child.vars.yml")
    assert nodes["r1"]["ospf"] == {"area": 0, "cost": 5}
    assert nodes["r1"]["bfd"] == {"profile": "fast"}
    assert nodes["r1"]["loopback"] == "10.0.0.1/32"


def test_vars_extends_cycle_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.vars.yml").write_text("extends: b.vars.yml\n", encoding="utf-8")
    (tmp_path / "b.vars.yml").write_text("extends: a.vars.yml\n", encoding="utf-8")
    with pytest.raises(ValueError, match="circular"):
        load_vars(tmp_path / "a.vars.yml")


def test_bfd_variants_render_bfd_everywhere() -> None:
    ospf = render_node_config("r1", load_vars(TOPOLOGY_DIR / "ospf_triangle_bfd.vars.yml")["r1"])
    assert "bfd\n profile fast\n  detect-multiplier 3\n  receive-interval 200" in ospf
    assert ospf.count(" ip ospf bfd profile fast") == 2  # both links, not loopback
    bgp = render_node_config("r1", load_vars(TOPOLOGY_DIR / "bgp_ring_bfd.vars.yml")["r1"])
    assert " neighbor 10.2.12.2 bfd profile fast" in bgp
    assert " neighbor 10.2.13.2 bfd profile fast" in bgp
    # the non-BFD base topologies must stay BFD-free
    base = render_node_config("r1", load_vars(TOPOLOGY_DIR / "ospf_triangle.vars.yml")["r1"])
    assert "bfd" not in base


def test_maximum_paths_only_when_configured() -> None:
    leaf = render_node_config("leaf1", load_vars(TOPOLOGY_DIR / "spine_leaf.vars.yml")["leaf1"])
    assert "  maximum-paths 4\n" in leaf
    ring = render_node_config("r1", load_vars(TOPOLOGY_DIR / "bgp_ring.vars.yml")["r1"])
    assert "maximum-paths" not in ring  # existing topologies render unchanged
