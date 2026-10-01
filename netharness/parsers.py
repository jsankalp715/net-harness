"""Pure parsers: FRR ``vtysh ... json`` output -> typed dataclasses.

Schemas verified against FRR 10.5.5. Kept free of I/O so they can be unit-tested
with captured fixtures (tests/unit/fixtures/).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class NextHop:
    ip: str | None
    interface: str | None
    active: bool
    directly_connected: bool = False


@dataclass(frozen=True)
class Route:
    prefix: str
    protocol: str
    selected: bool
    installed: bool
    distance: int
    metric: int
    nexthops: tuple[NextHop, ...] = field(default_factory=tuple)

    @property
    def interfaces(self) -> set[str]:
        """Egress interfaces of the *active* next hops."""
        return {nh.interface for nh in self.nexthops if nh.active and nh.interface}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: prefix -> every candidate route FRR knows for it (selected or not)
RoutingTable = dict[str, list[Route]]


@dataclass(frozen=True)
class OspfNeighbor:
    router_id: str
    state: str  # e.g. "Full", "Init", "ExStart"
    role: str
    interface: str
    address: str

    @property
    def is_full(self) -> bool:
        return self.state == "Full"


@dataclass(frozen=True)
class BgpPeer:
    address: str
    remote_as: int
    local_as: int
    state: str
    prefixes_received: int
    hostname: str | None = None

    @property
    def established(self) -> bool:
        return self.state == "Established"

    @property
    def is_ebgp(self) -> bool:
        return self.remote_as != self.local_as


@dataclass(frozen=True)
class BgpPath:
    prefix: str
    best: bool
    valid: bool
    as_path: str
    next_hop: str | None
    peer: str | None


def parse_routes(data: dict[str, Any]) -> RoutingTable:
    """Parse ``show ip route json``."""
    table: RoutingTable = {}
    for prefix, entries in data.items():
        routes: list[Route] = []
        for e in entries:
            nexthops = tuple(
                NextHop(
                    ip=nh.get("ip"),
                    interface=nh.get("interfaceName"),
                    active=bool(nh.get("active", False)),
                    directly_connected=bool(nh.get("directlyConnected", False)),
                )
                for nh in e.get("nexthops", [])
            )
            routes.append(
                Route(
                    prefix=e.get("prefix", prefix),
                    protocol=e.get("protocol", "unknown"),
                    selected=bool(e.get("selected", False)),
                    installed=bool(e.get("installed", False)),
                    distance=int(e.get("distance", 0)),
                    metric=int(e.get("metric", 0)),
                    nexthops=nexthops,
                )
            )
        table[prefix] = routes
    return table


def selected_route(table: RoutingTable, prefix: str) -> Route | None:
    """The route zebra selected (and installed) for ``prefix``, if any."""
    for route in table.get(prefix, []):
        if route.selected and route.installed:
            return route
    return None


def table_summary(table: RoutingTable) -> dict[str, dict[str, Any]]:
    """Compact JSON-friendly view of the *selected* routes, for logs and diagnostics."""
    out: dict[str, dict[str, Any]] = {}
    for prefix in sorted(table):
        route = selected_route(table, prefix)
        if route is None:
            continue
        out[prefix] = {
            "protocol": route.protocol,
            "metric": route.metric,
            "distance": route.distance,
            "nexthops": [
                {"ip": nh.ip, "interface": nh.interface} for nh in route.nexthops if nh.active
            ],
        }
    return out


def parse_ospf_neighbors(data: dict[str, Any]) -> list[OspfNeighbor]:
    """Parse ``show ip ospf neighbor json``.

    Shape: ``{"neighbors": {"<router-id>": [ {nbrState, converged, ifaceName, ...} ]}}``.
    Older FRR releases used a single object instead of a list; both are accepted.
    """
    neighbors: list[OspfNeighbor] = []
    for router_id, entries in data.get("neighbors", {}).items():
        for e in entries if isinstance(entries, list) else [entries]:
            nbr_state = str(e.get("nbrState", ""))
            state = str(e.get("converged") or nbr_state.split("/")[0])
            role = str(e.get("role") or (nbr_state.split("/")[1] if "/" in nbr_state else ""))
            neighbors.append(
                OspfNeighbor(
                    router_id=router_id,
                    state=state,
                    role=role,
                    interface=str(e.get("ifaceName", "")).split(":")[0],
                    address=str(e.get("ifaceAddress", "")),
                )
            )
    return neighbors


def parse_bgp_summary(data: dict[str, Any]) -> list[BgpPeer]:
    """Parse ``show bgp ipv4 unicast summary json`` (or ``show bgp summary json``)."""
    if "peers" not in data and "ipv4Unicast" in data:
        data = data["ipv4Unicast"]
    local_as = int(data.get("as", 0))
    peers: list[BgpPeer] = []
    for address, p in data.get("peers", {}).items():
        peers.append(
            BgpPeer(
                address=address,
                remote_as=int(p.get("remoteAs", 0)),
                local_as=int(p.get("localAs", local_as)),
                state=str(p.get("state", "Unknown")),
                prefixes_received=int(p.get("pfxRcd", 0)),
                hostname=p.get("hostname"),
            )
        )
    return peers


def parse_bgp_rib(data: dict[str, Any]) -> dict[str, list[BgpPath]]:
    """Parse ``show bgp ipv4 unicast json`` into prefix -> paths."""
    rib: dict[str, list[BgpPath]] = {}
    for prefix, paths in data.get("routes", {}).items():
        rib[prefix] = [
            BgpPath(
                prefix=prefix,
                best=bool(p.get("bestpath", False)),
                valid=bool(p.get("valid", False)),
                as_path=str(p.get("path", "")),
                next_hop=(p.get("nexthops") or [{}])[0].get("ip"),
                peer=p.get("peerId"),
            )
            for p in paths
        ]
    return rib
