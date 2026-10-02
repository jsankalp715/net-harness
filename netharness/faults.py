"""Fault injection: link down/up and (optional) netem impairment.

All operations run *inside the router's network namespace* via ``docker exec``, which
works identically on native Linux and on Docker Desktop (where the host can't see the
container netns directly). Downing both ends of a veth pair models a cable cut: both
routers see carrier loss immediately.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from netharness.lab import Endpoint, Lab, Link

log = logging.getLogger(__name__)


class NetemUnavailable(RuntimeError):
    """The kernel lacks sch_netem (common on stock WSL2 kernels)."""


@dataclass(frozen=True)
class Netem:
    delay_ms: float | None = None
    jitter_ms: float | None = None
    loss_pct: float | None = None

    def args(self) -> list[str]:
        out: list[str] = []
        if self.delay_ms is not None:
            out += ["delay", f"{self.delay_ms}ms"]
            if self.jitter_ms is not None:
                out.append(f"{self.jitter_ms}ms")
        if self.loss_pct is not None:
            out += ["loss", f"{self.loss_pct}%"]
        if not out:
            raise ValueError("Netem needs at least delay_ms or loss_pct")
        return out


class FaultInjector:
    """Applies faults to a :class:`Lab` and remembers them so they can be reverted."""

    def __init__(self, lab: Lab) -> None:
        self.lab = lab
        self._down: set[Endpoint] = set()
        self._netem: set[Endpoint] = set()
        self._netem_supported: bool | None = None

    # ------------------------------------------------------------- link state
    def set_interface(self, ep: Endpoint, up: bool) -> None:
        state = "up" if up else "down"
        self.lab.exec(ep.node, ["ip", "link", "set", "dev", ep.interface, state])
        (self._down.discard if up else self._down.add)(ep)
        log.info("fault: %s %s", ep, state.upper())

    def link_down(self, link: Link) -> float:
        """Cut ``link`` (both ends). Returns the ``time.monotonic()`` of the cut."""
        self.set_interface(link.a, up=False)
        t = time.monotonic()
        self.set_interface(link.b, up=False)
        return t

    def link_up(self, link: Link) -> float:
        """Restore ``link`` (both ends). Returns the ``time.monotonic()`` of the restore."""
        self.set_interface(link.a, up=True)
        self.set_interface(link.b, up=True)
        return time.monotonic()

    @contextmanager
    def link_failure(self, link: Link) -> Iterator[float]:
        """``with faults.link_failure(link) as t_cut:`` — link is always restored on exit."""
        t = self.link_down(link)
        try:
            yield t
        finally:
            self.link_up(link)

    def isolate_node(self, node: str) -> float:
        """Cut every link of ``node`` (a partition). Returns the instant of the first cut.

        Links are cut one after another, so the partition is complete only once the last
        ``ip link`` returns; measuring from the *first* cut is the conservative choice.
        """
        links = self.lab.links_of(node)
        t = self.link_down(links[0])
        for link in links[1:]:
            self.link_down(link)
        return t

    def heal_node(self, node: str) -> float:
        """Restore every link of ``node``. Returns the instant the last link came up."""
        t = time.monotonic()
        for link in self.lab.links_of(node):
            t = self.link_up(link)
        return t

    # ------------------------------------------------------------- netem
    def netem_supported(self, ep: Endpoint) -> bool:
        """Probe once by adding (and removing) a zero-delay netem qdisc."""
        if self._netem_supported is None:
            res = self.lab.exec(
                ep.node,
                ["tc", "qdisc", "add", "dev", ep.interface, "root", "netem", "delay", "0ms"],
                check=False,
            )
            self._netem_supported = res.ok
            if res.ok:
                self.lab.exec(
                    ep.node, ["tc", "qdisc", "del", "dev", ep.interface, "root"], check=False
                )
            else:
                log.warning("netem unavailable: %s", res.stderr.strip())
        return self._netem_supported

    def apply_netem(self, ep: Endpoint, netem: Netem) -> None:
        if not self.netem_supported(ep):
            raise NetemUnavailable(f"sch_netem not available on {ep}")
        self.lab.exec(
            ep.node,
            ["tc", "qdisc", "replace", "dev", ep.interface, "root", "netem", *netem.args()],
        )
        self._netem.add(ep)
        log.info("fault: netem %s on %s", " ".join(netem.args()), ep)

    def clear_netem(self, ep: Endpoint) -> None:
        self.lab.exec(ep.node, ["tc", "qdisc", "del", "dev", ep.interface, "root"], check=False)
        self._netem.discard(ep)

    @contextmanager
    def impairment(self, ep: Endpoint, netem: Netem) -> Iterator[float]:
        self.apply_netem(ep, netem)
        t = time.monotonic()
        try:
            yield t
        finally:
            self.clear_netem(ep)

    # ------------------------------------------------------------- cleanup
    def restore_all(self) -> None:
        """Bring every downed interface back up and remove every netem qdisc."""
        for ep in list(self._netem):
            self.clear_netem(ep)
        for ep in list(self._down):
            self.set_interface(ep, up=True)
