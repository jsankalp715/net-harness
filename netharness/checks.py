"""Reusable convergence predicates for :func:`wait_for_convergence`.

Each factory returns a zero-arg callable that queries the live lab, so it can be
polled. Predicates get a readable ``__name__`` used in logs/failure messages.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from netharness.lab import Lab

Predicate = Callable[[], bool]


def _named(fn: Predicate, name: str) -> Predicate:
    fn.__name__ = name
    fn.__qualname__ = name
    return fn


def route_via(
    lab: Lab,
    node: str,
    prefix: str,
    interfaces: str | Iterable[str] | None = None,
    protocol: str | None = None,
) -> Predicate:
    """``node`` has a selected+installed route to ``prefix`` [out of exactly ``interfaces``]."""
    want = {interfaces} if isinstance(interfaces, str) else (set(interfaces or []) or None)

    def check() -> bool:
        route = lab.get_route(node, prefix)
        if route is None:
            return False
        if protocol is not None and route.protocol != protocol:
            return False
        return want is None or route.interfaces == want

    via = f" via {sorted(want)}" if want else ""
    proto = f" ({protocol})" if protocol else ""
    return _named(check, f"{node} -> {prefix}{via}{proto}")


def route_absent(lab: Lab, node: str, prefix: str) -> Predicate:
    return _named(lambda: lab.get_route(node, prefix) is None, f"{node} has no {prefix}")


def ospf_neighbors_full(lab: Lab, node: str, router_ids: Iterable[str]) -> Predicate:
    expected = set(router_ids)

    def check() -> bool:
        full = {n.router_id for n in lab.get_ospf_neighbors(node) if n.is_full}
        return expected <= full

    return _named(check, f"{node} OSPF Full with {sorted(expected)}")


def bgp_established(lab: Lab, node: str, peers: Iterable[str]) -> Predicate:
    expected = set(peers)

    def check() -> bool:
        up = {p.address for p in lab.get_bgp_peers(node) if p.established}
        return expected <= up

    return _named(check, f"{node} BGP Established with {sorted(expected)}")


def bfd_peers_up(lab: Lab, node: str, peers: Iterable[str]) -> Predicate:
    expected = set(peers)

    def check() -> bool:
        up = {p.peer for p in lab.get_bfd_peers(node) if p.up}
        return expected <= up

    return _named(check, f"{node} BFD up with {sorted(expected)}")


def full_loopback_reachability(lab: Lab) -> Predicate:
    """Every node has a selected route to every other node's loopback.

    Pairs listed in the vars file's ``expect.unreachable_loopbacks`` are skipped.
    """
    loopbacks = {n: str(lab.node_vars[n]["loopback"]) for n in lab.nodes}

    def check() -> bool:
        for node in lab.nodes:
            table = lab.get_routes(node)
            for other, prefix in loopbacks.items():
                if other == node or frozenset((node, other)) in lab.unreachable_pairs:
                    continue
                if not any(r.selected and r.installed for r in table.get(prefix, [])):
                    return False
        return True

    return _named(check, f"{lab.name}: full loopback reachability")


def routing_stable(lab: Lab) -> Predicate:
    """True once every node's selected routes are identical to the previous poll.

    Stateful: use with ``consecutive=N`` to require N unchanged polls in a row. Guards
    against starting a scenario while the previous one's reconvergence is in flight.
    """
    previous: dict[str, dict[str, dict[str, Any]]] = {}

    def check() -> bool:
        nonlocal previous
        current = lab.routing_tables()
        stable = bool(previous) and current == previous
        previous = current
        return stable

    return _named(check, f"{lab.name}: routing tables stable")


def steady_state(lab: Lab) -> Predicate:
    """Full loopback reachability *and* unchanged routing tables."""
    return all_of(full_loopback_reachability(lab), routing_stable(lab))


def prefixes_withdrawn(lab: Lab, nodes: Iterable[str], prefixes: Iterable[str]) -> Predicate:
    """No node in ``nodes`` has a selected+installed route for any of ``prefixes``.

    Exact-prefix match on the full table: ``show ip route <prefix>`` would fall back to a
    covering route (e.g. the default route via eth0) and report the prefix as present.
    """
    nodes, prefixes = sorted(nodes), sorted(prefixes)

    def check() -> bool:
        for node in nodes:
            table = lab.get_routes(node)
            for prefix in prefixes:
                if any(r.selected and r.installed for r in table.get(prefix, [])):
                    return False
        return True

    return _named(check, f"{prefixes} withdrawn on {nodes}")


def only_connected_routes(lab: Lab, node: str, protocols: Iterable[str]) -> Predicate:
    """``node`` has no selected route learned via any of ``protocols`` (it is isolated)."""
    protos = set(protocols)

    def check() -> bool:
        table = lab.get_routes(node)
        return not any(
            r.selected and r.protocol in protos for routes in table.values() for r in routes
        )

    return _named(check, f"{node} has no {sorted(protos)} routes")


def all_of(*predicates: Predicate) -> Predicate:
    def check() -> bool:
        return all(p() for p in predicates)

    return _named(check, " AND ".join(p.__name__ for p in predicates))
