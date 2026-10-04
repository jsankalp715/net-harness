# What the harness found

`net-harness` breaks virtual networks on purpose and measures how fast routing recovers.
This page covers what that turned up: two problems in my own setup, one in my own
measurement code, a clear number for what BFD is worth, and why IPv6 was at first 2×
slower than IPv4. All numbers are from GitHub
Actions (CI run 18) unless stated otherwise.

**Setup in one paragraph.** Routers are FRR 10.5.5 containers wired together by
containerlab. Configs are rendered from Jinja2 templates and pushed with `vtysh`.
A pytest scenario injects a fault: it downs an interface inside a router's network
namespace, or uses `tc netem` to drop packets while the link stays up. It then polls every
router's routing table as JSON until the expected state appears. The elapsed time is the
**convergence time**, and CI compares it with a stored baseline.

---

## 1. Default OSPF timers made recovery stall for 5 seconds

**Symptom.** The first full run produced *bimodal* OSPF timings. A link restore took
either about 1.1 s **or** about 5.0 s, and one failover took 2.4 s where its twin took
0.1 s. Which one you got depended on the test order.

**Investigation.** A probe flapped the same links repeatedly. The 5 s outliers matched
two RFC 2328 defaults, both 5 s:

- **MinLSInterval.** A router can't re-originate its link-state advertisement within 5 s
  of the previous one. A link that goes down and comes back up within 5 s can't be
  re-advertised until the interval expires. Back-to-back tests (restore, then the next
  cut) hit this, which is why the result depended on test order.
- **Retransmit interval.** When a link comes up, its two ends don't come up at exactly the
  same instant. A database-description or update packet sent toward an end that isn't up
  yet is lost, and the retry waits 5 s.

**Fix.** `timers throttle lsa all 100`, `timers lsa min-arrival 50` and
`ip ospf retransmit-interval 1`, all set in the topology's vars file.

**Result.** 8 of 8 flap cycles then landed at **0.10–0.13 s failover** and
**1.12–1.17 s restore** (measured locally). The remaining ~1 s is the hello interval,
which is expected.

**Takeaway.** Protocol defaults are tuned for stability on slow or large networks, not
for fast recovery. A bimodal distribution is a signal, not noise: two clusters mean two
mechanisms.

---

## 2. My own measurement under-reported, and a 0.000 s result gave it away

**Symptom.** In the spine-leaf fabric, recovery after a spine failure reported
**0.000 s**. That's impossible: BGP needs at least about 1 s to re-establish a session.

**Two bugs:**

1. `wait_for_convergence()` recorded the time at the **start** of the poll that first saw
   the target state. That's harmless when the poll checks one route (about 0.1 s). But the
   fabric-wide check looks up 12 routes across 4 routers, so the poll itself took about
   1.2 s, and that whole second disappeared from the measurement.
2. `heal_node()` timed recovery from the **last** link restored. Bringing 8 interface ends
   back up takes about 1 s, and BGP was already reconverging on the first links during
   that time.

**Fix.** Convergence is now stamped at the **end** of the first successful poll, the
moment it was actually observed, which makes every value a conservative upper bound.
Recovery is timed from the **first** link restored, mirroring how failures are timed from
the first cut. A unit test now proves that a slow poll can't be reported as instant.

**Impact.** Single-route measurements rose by about 0.05 s; for example, OSPF failover
went from 0.03 s to 0.07 s on CI, which shows as a step on the trend chart. Measurements
that check many routers rose more: OSPF partition withdrawal went from 0.20 s to 0.94 s
(locally). The baseline was regenerated under the corrected method.

**Takeaway.** A test harness is a measuring instrument, and instruments need validating
too. A result that looks too good deserves the same suspicion as one that looks too bad.

---

## 3. BFD detects silent failures 5–11× faster

When a cable is cut, routers notice almost instantly from loss of carrier. The dangerous
case is a **silent failure**: the link stays up but packets disappear (a flaky optic, a
congested or misprogrammed device). Only protocol timers notice it, and until they do,
traffic is black-holed.

The harness reproduces this with `tc netem loss 100%` on both ends of a link, then
compares detection with and without BFD (200 ms × 3, about 600 ms to declare the peer
down):

| Protocol | Timer only | With BFD | Speed-up |
|---|---:|---:|---:|
| OSPF (dead interval 4 s) | 3.26 s | **0.62 s** | ~5× |
| BGP (hold time 9 s) | 7.05 s | **0.63 s** | ~11× |

The test bounds each case on **both** sides. The timer-only BGP case must take at least
5 s, which proves it was the hold timer, not something faster, that detected the failure.
Without that lower bound, a test could pass for the wrong reason.

---

## 4. Withdrawal works, and BGP path hunting settles quickly

Every failover test has a backup path. The partition tests check the other half of
correctness: when a destination becomes **unreachable**, every router must *withdraw*
it. A stale route silently black-holes traffic, which is worse than no route at all.

Isolating a router (cutting all of its links) leads to full withdrawal in about **0.5 s**
for both OSPF and BGP, and reachability returns about **1.2–1.4 s** after healing. In the
BGP ring, r1 reaches the isolated router along two paths, so BGP can briefly switch to the
doomed alternate path before converging ("path hunting"). The test requires the withdrawn
state to **hold for 3 s**, so it can't pass in a gap between hunting steps.

## 5. Spine-leaf: ECMP and loop prevention behave as designed

In the 2-spine / 4-leaf fabric:
- Every leaf load-balances to every other leaf over **both** spines (ECMP).
- Losing one uplink collapses the affected paths in **0.29 s**.
- Losing a whole spine drains all 12 leaf-to-leaf paths in **0.73 s** and restores ECMP
  in **1.78 s**.

The first deploy "failed" a generic check that every router can reach every other
router's loopback. That turned out to be correct behaviour. The spines share one AS
(the RFC 7938 design), so a route between them must go spine → leaf → spine, and the
receiving spine rejects it because its own AS appears in the path. Rather than weaken the
check, the topology now *declares* that pair as unreachable by design, and a test asserts
that it really is.

---

## 6. IPv6 recovered 2× slower than IPv4, and the cause was DAD, not routing

**Symptom.** After making the OSPF and BGP labs dual-stack, all 162 tests passed, but the
regression check flagged partition *heal* times (BGP 1.40 s → 4.98 s). The IPv6 restores
explained it. OSPFv3 restore took **2.71 s** against 1.21 s for OSPFv2, and BGP over IPv6
took **3.70 s** against 1.50 s for IPv4.

**Hypothesis.** IPv6 **Duplicate Address Detection**. When an interface comes back up,
Linux marks its IPv6 addresses *tentative* for about a second while it checks that nobody
else is using them. OSPFv3 can't form an adjacency, and BGP can't open its TCP session,
on a tentative address.

**Experiment.** I disabled DAD in the lab (`accept_dad=0`, `dad_transmits=0`) and
re-measured only the restore tests:

| Restore | IPv4 | IPv6 with DAD | IPv6, DAD off |
|---|---:|---:|---:|
| OSPF | 1.05 s | 2.71 s | **1.33 s** |
| BGP | 1.41 s | 3.70 s | **1.40 s** |

That confirmed it: DAD added roughly **1.4–2.3 s to every IPv6 recovery**, and without it
IPv6 matches IPv4.

**Decision.** The labs now run with DAD off, documented in the topology files, so the
harness measures the routing protocols and IPv4 and IPv6 stay comparable. On
point-to-point links with configured addresses a duplicate can't occur. Production
networks usually keep DAD on; *optimistic DAD* (RFC 4429) is the standard way to avoid
this delay without giving up the check.

**Takeaway.** "IPv6 converges slower" sounded like a routing problem, but the delay was
in the host's IPv6 stack, below the routing protocols. A single controlled change was
enough to separate the two.

---

## Open observation

**OSPF + BFD restore after a silent failure is noisy.** On CI it ranges from about 0.3 s
to 1.15 s between runs (see the trend chart), while most metrics stay within ±0.05 s. The
likely cause is that after the loss clears, adjacency recovery waits for the next 1 s
hello, so the result depends on where in the hello cycle the loss ends. That is **not yet
verified**. A test that clears the loss at controlled points in the hello phase would
settle it.

## Caveats

- **Containers, not hardware.** FRR is production routing software, so the protocol
  behaviour is real. There is no hardware forwarding plane, though, so absolute numbers
  are lower than on physical routers, where programming the forwarding hardware adds time.
  The harness is most useful for **relative** comparisons and for catching regressions.
- **Resolution.** Polling every 0.25 s through `docker exec` makes each value a
  conservative upper bound, good to about ±0.3 s. That's why regressions are only flagged
  above +100% **and** +1 s.
- **Lab-tuned timers.** Hello 1 s / dead 4 s and BGP 3 s / 9 s are faster than many
  production defaults. They're chosen so the suite runs in minutes.
