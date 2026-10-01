"""Jinja2 rendering of per-node FRR configs."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from netharness import constants


def make_env(template_dir: Path = constants.TEMPLATE_DIR) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,  # FRR config, not HTML
    )


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto ``base`` (dicts merge, everything else replaces)."""
    merged: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_vars(path: Path) -> dict[str, dict[str, Any]]:
    """Load a ``*.vars.yml`` file and return node -> variables.

    ``defaults`` is deep-merged under every node (node values win).
    """
    raw = yaml.safe_load(path.read_text()) or {}
    defaults: dict[str, Any] = raw.get("defaults") or {}
    return {
        name: deep_merge(defaults, node_vars or {})
        for name, node_vars in (raw.get("nodes") or {}).items()
    }


def render_node_config(
    name: str, node_vars: Mapping[str, Any], env: Environment | None = None
) -> str:
    env = env or make_env()
    return env.get_template("frr.conf.j2").render(name=name, node=node_vars)
