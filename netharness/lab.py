"""The :class:`Lab` — one deployed containerlab topology of FRR routers."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import yaml

from netharness import constants
from netharness.clab import Containerlab
from netharness.parsers import (
    BfdPeer,
    BgpPath,
    BgpPeer,
    OspfNeighbor,
    Route,
    RoutingTable,
    parse_bfd_peers,
    parse_bgp_rib,
    parse_bgp_summary,
    parse_ospf_neighbors,
    parse_routes,
    selected_route,
    table_summary,
)
from netharness.render import load_vars, make_env, render_node_config
from netharness.shell import CommandError, CommandResult, run

log = logging.getLogger(__name__)

_REMOTE_CONFIG_PATH = "/tmp/netharness-frr.conf"


class LabError(RuntimeError):
    pass


@dataclass(frozen=True)
class Endpoint:
    node: str
    interface: str

    def __str__(self) -> str:
        return f"{self.node}:{self.interface}"


@dataclass(frozen=True)
class Link:
    a: Endpoint
    b: Endpoint

    def endpoint(self, node: str) -> Endpoint:
        if self.a.node == node:
            return self.a
        if self.b.node == node:
            return self.b
        raise KeyError(f"{node} is not an endpoint of {self}")

    def __str__(self) -> str:
        return f"{self.a} <-> {self.b}"


def resolve_topology(topology: str | Path) -> Path:
    """Accept a path or a bare name like ``ospf_triangle``."""
    path = Path(topology)
    if path.suffix in {".yml", ".yaml"}:
        return path.resolve()
    return (constants.TOPOLOGY_DIR / f"{topology}.clab.yml").resolve()


class Lab:
    """Deploy/destroy a topology, push rendered configs and query routing state.

    Usable as a context manager::

        with Lab("ospf_triangle") as lab:
            lab.get_routes("r1")
    """

    def __init__(
        self,
        topology: str | Path,
        *,
        vars_file: Path | None = None,
        clab: Containerlab | None = None,
        docker_bin: str = constants.DOCKER_BIN,
    ) -> None:
        self.topology_file = resolve_topology(topology)
        if not self.topology_file.exists():
            raise FileNotFoundError(self.topology_file)
        self.spec: dict[str, Any] = yaml.safe_load(self.topology_file.read_text(encoding="utf-8"))
        self.name: str = self.spec["name"]
        self.prefix: str = self.spec.get("prefix", "clab")
        self.nodes: list[str] = sorted(self.spec["topology"]["nodes"])
        self.links: list[Link] = [self._parse_link(lk) for lk in self.spec["topology"]["links"]]

        vars_path = vars_file or self.topology_file.with_name(
            self.topology_file.name.replace(".clab.yml", ".vars.yml")
        )
        self.node_vars: dict[str, dict[str, Any]] = load_vars(vars_path)
        missing = set(self.nodes) - set(self.node_vars)
        if missing:
            raise LabError(f"{vars_path.name} has no vars for nodes: {sorted(missing)}")

        self.clab = clab or Containerlab(self.topology_file)
        self.docker_bin = docker_bin
        self._env = make_env()
        self.deployed = False
        #: seconds from config push to steady state; set by whoever measures it
        self.initial_convergence_s: float | None = None

    # ------------------------------------------------------------------ topology
    @staticmethod
    def _parse_link(raw: dict[str, Any]) -> Link:
        a, b = (Endpoint(*ep.split(":", 1)) for ep in raw["endpoints"])
        return Link(a, b)

    def container(self, node: str) -> str:
        return f"{self.prefix}-{self.name}-{node}" if self.prefix else node

    def link_between(self, node_a: str, node_b: str) -> Link:
        for link in self.links:
            if {link.a.node, link.b.node} == {node_a, node_b}:
                return link
        raise KeyError(f"no link between {node_a} and {node_b} in {self.name}")

    def links_of(self, node: str) -> list[Link]:
        """Every link with ``node`` as an endpoint."""
        if node not in self.nodes:
            raise KeyError(f"{node} is not a node of {self.name}")
        return [lk for lk in self.links if node in (lk.a.node, lk.b.node)]

    # ------------------------------------------------------------------ lifecycle
    def deploy(self, *, configure: bool = True, ready_timeout: float = 120) -> None:
        self.clab.deploy(reconfigure=True)
        self.deployed = True
        self.wait_ready(timeout=ready_timeout)
        if configure:
            self.configure_all()

    def destroy(self) -> None:
        self.clab.destroy(cleanup=True)
        self.deployed = False

    def __enter__(self) -> Lab:
        self.deploy()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.destroy()

    def wait_ready(self, *, timeout: float = 120) -> None:
        """Block until every node's FRR answers on vtysh with bgpd/ospfd up."""
        deadline = time.monotonic() + timeout
        pending = set(self.nodes)
        while pending:
            for node in sorted(pending):
                res = self.exec(node, ["vtysh", "-c", "show daemons"], check=False)
                if res.ok and "ospfd" in res.stdout and "bgpd" in res.stdout:
                    pending.discard(node)
            if not pending:
                break
            if time.monotonic() > deadline:
                raise LabError(f"FRR not ready on {sorted(pending)} after {timeout}s")
            time.sleep(1)
        log.info("lab %s: all %d nodes ready", self.name, len(self.nodes))

    # ------------------------------------------------------------------ exec
    def exec(
        self,
        node: str,
        argv: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout: float = 60,
    ) -> CommandResult:
        """Run ``argv`` inside ``node``'s container (and network namespace)."""
        docker = [self.docker_bin, "exec"]
        if input_text is not None:
            docker.append("-i")
        return run(
            [*docker, self.container(node), *argv],
            check=check,
            input_text=input_text,
            timeout=timeout,
        )

    def vtysh(self, node: str, *commands: str, check: bool = True) -> str:
        argv = ["vtysh"]
        for cmd in commands:
            argv += ["-c", cmd]
        return self.exec(node, argv, check=check).stdout

    def vtysh_json(self, node: str, command: str) -> Any:
        out = self.vtysh(node, command if command.endswith(" json") else f"{command} json")
        try:
            return json.loads(out) if out.strip() else {}
        except json.JSONDecodeError as exc:
            raise LabError(f"{node}: non-JSON output for {command!r}: {out[:500]}") from exc

    # ------------------------------------------------------------------ config
    def render_config(self, node: str) -> str:
        return render_node_config(node, self.node_vars[node], self._env)

    def push_config(self, node: str, config: str) -> None:
        """Copy ``config`` into the container and apply it with ``vtysh -f``."""
        self.exec(node, ["sh", "-c", f"cat > {_REMOTE_CONFIG_PATH}"], input_text=config)
        try:
            res = self.exec(node, ["vtysh", "-f", _REMOTE_CONFIG_PATH])
        except CommandError as exc:
            raise LabError(f"{node}: vtysh rejected config:\n{exc}") from exc
        errors = [ln for ln in res.stdout.splitlines() if ln.lstrip().startswith("%")]
        if errors:
            raise LabError(f"{node}: vtysh reported errors:\n" + "\n".join(errors))
        self.vtysh(node, "write memory")
        log.info("%s: pushed %d config lines", node, len(config.splitlines()))

    def configure(self, node: str) -> None:
        self.push_config(node, self.render_config(node))

    def configure_all(self) -> None:
        for node in self.nodes:
            self.configure(node)

    # ------------------------------------------------------------------ state
    def get_routes(self, node: str) -> RoutingTable:
        return parse_routes(self.vtysh_json(node, "show ip route json"))

    def get_route(self, node: str, prefix: str) -> Route | None:
        """Selected+installed route for ``prefix`` on ``node`` (``None`` if absent)."""
        data = self.vtysh_json(node, f"show ip route {prefix} json")
        return selected_route(parse_routes(data), prefix)

    def get_ospf_neighbors(self, node: str) -> list[OspfNeighbor]:
        return parse_ospf_neighbors(self.vtysh_json(node, "show ip ospf neighbor json"))

    def get_bgp_peers(self, node: str) -> list[BgpPeer]:
        return parse_bgp_summary(self.vtysh_json(node, "show bgp ipv4 unicast summary json"))

    def get_bgp_rib(self, node: str) -> dict[str, list[BgpPath]]:
        return parse_bgp_rib(self.vtysh_json(node, "show bgp ipv4 unicast json"))

    def get_bfd_peers(self, node: str) -> list[BfdPeer]:
        return parse_bfd_peers(self.vtysh_json(node, "show bfd peers json"))

    def routing_tables(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Selected routes of every node, JSON-serialisable (used for logs)."""
        tables: dict[str, dict[str, dict[str, Any]]] = {}
        for node in self.nodes:
            try:
                tables[node] = table_summary(self.get_routes(node))
            except (CommandError, LabError) as exc:
                tables[node] = {"error": {"message": str(exc)}}
        return tables

    def dump_routing_tables(self) -> str:
        """Human-readable dump of every node's routing table + protocol state."""
        chunks: list[str] = []
        for node in self.nodes:
            chunks.append(f"===== {node} ({self.container(node)}) =====")
            for cmd in (
                "show ip route",
                "show ip ospf neighbor",
                "show bgp ipv4 unicast summary",
                "show bfd peers brief",
            ):
                try:
                    out = self.vtysh(node, cmd, check=False)
                except Exception as exc:  # diagnostics must never raise
                    out = f"<failed: {exc}>"
                chunks.append(f"--- {cmd}\n{out.rstrip()}")
        return "\n".join(chunks)
