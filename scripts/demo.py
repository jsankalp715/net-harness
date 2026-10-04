#!/usr/bin/env python3
"""Narrated live demo (~2 minutes): break a 3-router OSPF network and watch it heal.

    make demo                         # Linux host with containerlab
    bash scripts/run_in_docker.sh make demo      # Docker Desktop (Windows/macOS)
    python scripts/demo.py --keep     # leave the lab running afterwards to poke at it

Steps: deploy -> show the normal path -> cut a link (fast failover) -> restore it ->
silent 100% packet loss (only the 4 s dead interval can notice) -> clean up.
Every number printed is measured live; every routing line is real FRR output.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netharness import FaultInjector, Lab, Netem, wait_for_convergence
from netharness.checks import full_loopback_reachability, route_via, steady_state

BOLD, GREEN, DIM, RESET = "\033[1m", "\033[32m", "\033[2m", "\033[0m"
PREFIX = "10.0.0.2/32"  # r2's loopback, as seen from r1


def step(n: int, title: str) -> None:
    print(f"\n{BOLD}── Step {n}: {title}{RESET}")


def say(text: str) -> None:
    print(f"   {text}")


def result(text: str) -> None:
    print(f"   {GREEN}{BOLD}✔ {text}{RESET}")


def router_says(lab: Lab, node: str, prefix: str) -> None:
    """Print FRR's own view of the route (the lines that matter)."""
    out = lab.vtysh(node, f"show ip route {prefix}", check=False)
    for line in out.splitlines():
        if line.strip().startswith(("Known via", "*")):
            print(f"   {DIM}{node}# {line.strip()}{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Narrated live demo")
    parser.add_argument("--keep", action="store_true", help="leave the lab running at the end")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between steps")
    args = parser.parse_args()
    pause = args.pause

    lab = Lab("ospf_triangle")
    faults = FaultInjector(lab)
    link = lab.link_between("r1", "r2")

    print(
        f"{BOLD}net-harness live demo: break a network on purpose, measure how fast it heals{RESET}"
    )
    try:
        step(1, "Build the network")
        say("containerlab starts 3 FRR routers (r1, r2, r3) in a triangle; templates render")
        say("each router's OSPF config and the harness pushes it with vtysh...")
        lab.deploy(configure=False)
        t0 = time.monotonic()
        lab.configure_all()
        initial = wait_for_convergence(full_loopback_reachability(lab), lab=lab, start=t0)
        wait_for_convergence(steady_state(lab), lab=lab, consecutive=3)
        result(f"OSPF converged {initial:.2f} s after the configs were pushed")
        time.sleep(pause)

        step(2, "Normal path")
        say("r1 reaches r2 over their direct link (eth1, cost 10):")
        wait_for_convergence(route_via(lab, "r1", PREFIX, "eth1", "ospf"), lab=lab)
        router_says(lab, "r1", PREFIX)
        time.sleep(pause)

        step(3, "Cut the r1-r2 link (like a fibre cut)")
        say("Both ends of the link go down. The only other way is through r3 (cost 20)...")
        t_cut = faults.link_down(link)
        failover = wait_for_convergence(
            route_via(lab, "r1", PREFIX, "eth2", "ospf"), lab=lab, start=t_cut, consecutive=2
        )
        result(f"r1 rerouted via r3 in {failover:.2f} s")
        router_says(lab, "r1", PREFIX)
        time.sleep(pause)

        step(4, "Repair the link")
        t_up = faults.link_up(link)
        restore = wait_for_convergence(
            route_via(lab, "r1", PREFIX, "eth1", "ospf"), lab=lab, start=t_up, consecutive=2
        )
        result(f"traffic is back on the direct link after {restore:.2f} s")
        say("(slower than failover: the routers must re-form their OSPF adjacency first)")
        time.sleep(pause)

        step(5, "Silent failure: the link stays UP but drops 100% of packets")
        if not faults.netem_supported(link.a):
            say("netem isn't available in this kernel, so skipping this step")
        else:
            say("No 'link down' signal this time. OSPF can only notice when hellos stop")
            say("arriving for the 4 s dead interval...")
            faults.apply_netem(link.a, Netem(loss_pct=100))
            faults.apply_netem(link.b, Netem(loss_pct=100))
            t_loss = time.monotonic()
            silent = wait_for_convergence(
                route_via(lab, "r1", PREFIX, "eth2", "ospf"), lab=lab, start=t_loss, timeout=30
            )
            result(f"detected and rerouted after {silent:.2f} s, versus {failover:.2f} s for a cut")
            say("That gap is why the project adds BFD: in CI it detects this in ~0.6 s.")
            faults.restore_all()
            wait_for_convergence(steady_state(lab), lab=lab, consecutive=3)
        time.sleep(pause)

        print(f"\n{BOLD}Summary{RESET}")
        say(f"initial convergence {initial:.2f} s · link-cut failover {failover:.2f} s · "
            f"restore {restore:.2f} s")  # fmt: skip
        say("In CI, 162 tests like these run on every push, each writing a JSON log,")
        say("and the build fails if any timing regresses against the stored baseline.")
        return 0
    finally:
        faults.restore_all()
        if args.keep:
            say(f"\nLab left running. Try: docker exec -it {lab.container('r1')} vtysh")
        else:
            print(f"\n{DIM}Cleaning up the lab...{RESET}")
            lab.destroy()


if __name__ == "__main__":
    sys.exit(main())
