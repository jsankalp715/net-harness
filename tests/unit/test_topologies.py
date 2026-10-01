"""Static consistency checks across topology YAML, vars files and constants."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from netharness.constants import FRR_IMAGE, TOPOLOGY_DIR
from netharness.lab import Lab

TOPOLOGIES = sorted(TOPOLOGY_DIR.glob("*.clab.yml"))


def test_topologies_exist() -> None:
    assert {p.name for p in TOPOLOGIES} >= {"ospf_triangle.clab.yml", "bgp_ring.clab.yml"}


@pytest.mark.parametrize("path", TOPOLOGIES, ids=lambda p: p.stem)
def test_image_is_pinned_and_consistent(path: Path) -> None:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    images = {spec["topology"].get("defaults", {}).get("image")} | {
        n.get("image") for n in spec["topology"]["nodes"].values() if n and n.get("image")
    }
    images.discard(None)
    assert images == {FRR_IMAGE}
    assert ":latest" not in FRR_IMAGE and ":" in FRR_IMAGE


@pytest.mark.parametrize("path", TOPOLOGIES, ids=lambda p: p.stem)
def test_vars_match_links(path: Path) -> None:
    """Every link endpoint has an address in vars, peers agree, subnets match."""
    lab = Lab(path)
    for link in lab.links:
        a = lab.node_vars[link.a.node]["interfaces"][link.a.interface]
        b = lab.node_vars[link.b.node]["interfaces"][link.b.interface]
        assert a.get("peer") == link.b.node and b.get("peer") == link.a.node
        net_a = a["address"].rsplit(".", 1)[0]
        net_b = b["address"].rsplit(".", 1)[0]
        assert net_a == net_b, f"{link}: {a['address']} vs {b['address']}"


def test_lab_parses_links_and_container_names() -> None:
    lab = Lab("ospf_triangle")
    assert lab.nodes == ["r1", "r2", "r3"]
    assert lab.container("r1") == "clab-ospf-tri-r1"
    link = lab.link_between("r2", "r1")
    assert link.endpoint("r1").interface == "eth1"
    with pytest.raises(KeyError):
        lab.link_between("r1", "r9")
