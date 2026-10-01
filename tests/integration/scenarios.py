"""Declarative scenario tables. Adding a case here adds a parametrized test."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from netharness.constants import TOPOLOGY_DIR
from netharness.render import load_vars


def topology_vars(topology: str) -> dict[str, dict[str, Any]]:
    """Node vars read at *collection* time (no lab needed) to derive expectations."""
    return load_vars(TOPOLOGY_DIR / f"{topology}.vars.yml")


# ----------------------------------------------------------------------------- failover
@dataclass(frozen=True)
class FailoverCase:
    """Cut ``cut`` and expect ``observer``'s route to ``prefix`` to move interfaces."""

    id: str
    topology: str
    protocol: str  # "ospf" | "bgp"  (also used as a pytest marker)
    observer: str
    prefix: str
    cut: tuple[str, str]
    primary_iface: str
    backup_iface: str
    max_failover_s: float
    max_restore_s: float


FAILOVER_CASES: list[FailoverCase] = [
    FailoverCase(
        id="ospf-r1-to-r2-cut-r1r2",
        topology="ospf_triangle",
        protocol="ospf",
        observer="r1",
        prefix="10.0.0.2/32",
        cut=("r1", "r2"),
        primary_iface="eth1",
        backup_iface="eth2",
        max_failover_s=5.0,
        max_restore_s=15.0,
    ),
    FailoverCase(
        id="ospf-r3-to-r1-cut-r1r3",
        topology="ospf_triangle",
        protocol="ospf",
        observer="r3",
        prefix="10.0.0.1/32",
        cut=("r1", "r3"),
        primary_iface="eth1",
        backup_iface="eth2",
        max_failover_s=5.0,
        max_restore_s=15.0,
    ),
    FailoverCase(
        id="bgp-r1-to-as65002-cut-r1r3",
        topology="bgp_ring",
        protocol="bgp",
        observer="r1",
        prefix="192.168.3.0/24",
        cut=("r1", "r3"),
        primary_iface="eth2",
        backup_iface="eth1",  # via iBGP peer r2 -> r4 -> r3
        max_failover_s=10.0,
        max_restore_s=20.0,
    ),
    FailoverCase(
        id="bgp-r4-to-r2-cut-r2r4",
        topology="bgp_ring",
        protocol="bgp",
        observer="r4",
        prefix="192.168.2.0/24",
        cut=("r2", "r4"),
        primary_iface="eth1",
        backup_iface="eth2",  # via r3 -> r1 (AS path 65002 65001)
        max_failover_s=10.0,
        max_restore_s=20.0,
    ),
]


def failover_params() -> list[Any]:
    """``(lab, case)`` params; ``lab`` is indirect so each topology deploys once."""
    return [
        pytest.param(c.topology, c, id=c.id, marks=[getattr(pytest.mark, c.protocol)])
        for c in FAILOVER_CASES
    ]


# ----------------------------------------------------------------------------- BGP RIB
@dataclass(frozen=True)
class BgpExpectation:
    node: str
    prefix: str
    as_path: str  # "" == iBGP-learned (or local) in FRR's JSON
    egress: str


BGP_EXPECTED_BEST: list[BgpExpectation] = [
    BgpExpectation("r1", "192.168.2.0/24", "", "eth1"),  # iBGP from r2
    BgpExpectation("r1", "192.168.3.0/24", "65002", "eth2"),  # direct eBGP
    BgpExpectation("r1", "192.168.4.0/24", "65003", "eth1"),  # via iBGP (shorter AS path)
    BgpExpectation("r2", "192.168.1.0/24", "", "eth1"),
    BgpExpectation("r2", "192.168.3.0/24", "65002", "eth1"),
    BgpExpectation("r2", "192.168.4.0/24", "65003", "eth2"),
    BgpExpectation("r3", "192.168.1.0/24", "65001", "eth1"),
    BgpExpectation("r3", "192.168.2.0/24", "65001", "eth1"),
    BgpExpectation("r3", "192.168.4.0/24", "65003", "eth2"),
    BgpExpectation("r4", "192.168.1.0/24", "65001", "eth1"),
    BgpExpectation("r4", "192.168.2.0/24", "65001", "eth1"),
    BgpExpectation("r4", "192.168.3.0/24", "65002", "eth2"),
]
