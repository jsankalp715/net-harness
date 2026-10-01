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


# ----------------------------------------------------------------------------- silent loss
@dataclass(frozen=True)
class SilentLossCase:
    """100% netem loss on ``cut`` (carrier stays up): only protocol timers or BFD notice.

    ``min_detect_s``/``max_detect_s`` bound the failover time and prove *which* mechanism
    detected the failure (e.g. a timer-based case must not be suspiciously fast).
    """

    id: str
    topology: str
    protocol: str
    bfd: bool
    observer: str
    prefix: str
    cut: tuple[str, str]
    primary_iface: str
    backup_iface: str
    min_detect_s: float
    max_detect_s: float


# The no-BFD OSPF reference (dead interval 4 s) is test_netem.py::
# test_silent_loss_detected_by_dead_interval; it keeps its own baseline key.
SILENT_LOSS_CASES: list[SilentLossCase] = [
    SilentLossCase(
        id="bgp-hold-timer-no-bfd",
        topology="bgp_ring",
        protocol="bgp",
        bfd=False,
        observer="r1",
        prefix="192.168.3.0/24",
        cut=("r1", "r3"),
        primary_iface="eth2",
        backup_iface="eth1",
        # hold 9 s, keepalive 3 s: expiry lands 6-9 s after the last keepalive got through
        min_detect_s=5.0,
        max_detect_s=15.0,
    ),
    SilentLossCase(
        id="ospf-bfd",
        topology="ospf_triangle_bfd",
        protocol="ospf",
        bfd=True,
        observer="r1",
        prefix="10.0.0.2/32",
        cut=("r1", "r2"),
        primary_iface="eth1",
        backup_iface="eth2",
        min_detect_s=0.0,
        max_detect_s=2.0,  # 3 x 200 ms detection + SPF + polling resolution
    ),
    SilentLossCase(
        id="bgp-bfd",
        topology="bgp_ring_bfd",
        protocol="bgp",
        bfd=True,
        observer="r1",
        prefix="192.168.3.0/24",
        cut=("r1", "r3"),
        primary_iface="eth2",
        backup_iface="eth1",
        min_detect_s=0.0,
        max_detect_s=2.0,
    ),
]


def silent_loss_params() -> list[Any]:
    out = []
    for c in SILENT_LOSS_CASES:
        marks = [getattr(pytest.mark, c.protocol)] + ([pytest.mark.bfd] if c.bfd else [])
        out.append(pytest.param(c.topology, c, id=c.id, marks=marks))
    return out


def bfd_neighbors(vars_: dict[str, dict[str, Any]], node: str) -> set[str]:
    """Link-peer addresses ``node`` should have a BFD session with."""
    peers = set()
    for intf in vars_[node]["interfaces"].values():
        peer = intf["peer"]
        for peer_intf in vars_[peer]["interfaces"].values():
            if peer_intf["peer"] == node:
                peers.add(peer_intf["address"].split("/")[0])
    return peers


# ----------------------------------------------------------------------------- partitions
@dataclass(frozen=True)
class PartitionCase:
    """Cut *every* link of ``isolated``: nobody may keep a (stale) route to it."""

    id: str
    topology: str
    protocol: str
    isolated: str
    max_withdraw_s: float
    max_heal_s: float


PARTITION_CASES: list[PartitionCase] = [
    PartitionCase("ospf-isolate-r3", "ospf_triangle", "ospf", "r3", 5.0, 15.0),
    # r4 is reachable from r1 via iBGP (r2) *and* via r3: after the cut both paths must
    # be withdrawn, exercising BGP path hunting through the ring.
    PartitionCase("bgp-isolate-r4", "bgp_ring", "bgp", "r4", 15.0, 20.0),
]


def partition_params() -> list[Any]:
    return [
        pytest.param(c.topology, c, id=c.id, marks=[getattr(pytest.mark, c.protocol)])
        for c in PARTITION_CASES
    ]


def originated_prefixes(vars_: dict[str, dict[str, Any]], node: str) -> set[str]:
    """Prefixes that exist in the network only because ``node`` announces them."""
    prefixes = {vars_[node]["loopback"]}
    prefixes.update(vars_[node].get("bgp", {}).get("networks", []))
    return prefixes
