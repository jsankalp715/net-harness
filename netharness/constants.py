"""Pinned versions and harness-wide defaults."""

from __future__ import annotations

import os
from pathlib import Path

#: Router image. Must match the ``image:`` in every topologies/*.clab.yml (unit-tested).
FRR_IMAGE = "quay.io/frrouting/frr:10.5.5"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOPOLOGY_DIR = PROJECT_ROOT / "topologies"
TEMPLATE_DIR = PROJECT_ROOT / "templates"
RESULTS_DIR = Path(os.environ.get("NETHARNESS_RESULTS_DIR", PROJECT_ROOT / "results"))

#: containerlab binary; ``CLAB_SUDO=1`` prefixes it with ``sudo -E`` (needed on CI runners).
CLAB_BIN = os.environ.get("CLAB_BIN", "containerlab")
CLAB_SUDO = os.environ.get("CLAB_SUDO", "0") == "1"
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")

#: Default convergence timeout (seconds); CI may raise it on slow runners.
DEFAULT_CONVERGENCE_TIMEOUT = float(os.environ.get("NETHARNESS_CONVERGENCE_TIMEOUT", "60"))
DEFAULT_POLL_INTERVAL = 0.25
