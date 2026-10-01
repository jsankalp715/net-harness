"""Thin wrapper around the containerlab CLI."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from netharness import constants
from netharness.shell import CommandResult, run

log = logging.getLogger(__name__)


class Containerlab:
    """Invoke ``containerlab`` (optionally via sudo) for a single topology file."""

    def __init__(
        self,
        topology: Path,
        *,
        binary: str = constants.CLAB_BIN,
        sudo: bool = constants.CLAB_SUDO,
    ) -> None:
        self.topology = topology.resolve()
        self.binary = binary
        self.sudo = sudo

    @staticmethod
    def available(binary: str = constants.CLAB_BIN) -> bool:
        return shutil.which(binary) is not None

    def _cmd(self, *args: str) -> list[str]:
        prefix = ["sudo", "-E"] if self.sudo else []
        return [*prefix, self.binary, *args, "-t", str(self.topology)]

    def deploy(self, *, reconfigure: bool = True, timeout: float = 600) -> CommandResult:
        args = ["deploy"]
        if reconfigure:
            args.append("--reconfigure")
        log.info("containerlab deploy %s", self.topology.name)
        return run(self._cmd(*args), timeout=timeout)

    def destroy(self, *, cleanup: bool = True, timeout: float = 300) -> CommandResult:
        args = ["destroy"]
        if cleanup:
            args.append("--cleanup")
        log.info("containerlab destroy %s", self.topology.name)
        return run(self._cmd(*args), timeout=timeout, check=False)

    def inspect(self) -> list[dict[str, Any]]:
        """Return the container list from ``containerlab inspect --format json``."""
        result = run(self._cmd("inspect", "--format", "json"), check=False)
        if not result.ok or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        # clab >= 0.60 keys containers by lab name; older versions use {"containers": [...]}.
        if isinstance(data, dict):
            if "containers" in data:
                return list(data["containers"])
            return [c for containers in data.values() for c in containers]
        return list(data)
