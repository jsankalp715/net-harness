"""Manual lab control, handy when debugging a scenario.

python -m netharness render ospf_triangle r1
python -m netharness deploy ospf_triangle
python -m netharness show ospf_triangle
python -m netharness destroy ospf_triangle
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from netharness.convergence import wait_for_convergence
from netharness.lab import Lab


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netharness")
    parser.add_argument("action", choices=["render", "deploy", "destroy", "show"])
    parser.add_argument("topology", help="topology name (e.g. ospf_triangle) or path")
    parser.add_argument("node", nargs="?", help="node for render/show (default: all)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    lab = Lab(args.topology)
    nodes = [args.node] if args.node else lab.nodes
    if args.action == "render":
        for node in nodes:
            print(f"! ---- {node}\n{lab.render_config(node)}")
    elif args.action == "deploy":
        lab.deploy()
        elapsed = wait_for_convergence(
            lambda: all(lab.get_routes(n) for n in lab.nodes), timeout=30, lab=lab
        )
        print(f"deployed {lab.name} ({len(lab.nodes)} nodes) in {elapsed:.2f}s after config")
    elif args.action == "destroy":
        lab.destroy()
    elif args.action == "show":
        print(json.dumps({n: lab.routing_tables()[n] for n in nodes}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
