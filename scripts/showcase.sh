#!/usr/bin/env bash
# Guided presenter script: the whole project demo, in order, with pauses for talking.
#
#   bash scripts/showcase.sh            # interactive: Enter = next act, s = skip act
#   bash scripts/showcase.sh --quick    # skip the ~2 min test-suite act
#   bash scripts/showcase.sh --auto     # no pauses (rehearsal / unattended)
#   bash scripts/showcase.sh --no-browser   # don't open browser tabs in Act 5
#
# Acts:  0 preflight (Docker)  1 live demo  2 inside a router  3 real test suite
#        4 evidence (JSON logs)  5 CI + trends in the browser
# Works from Git Bash on Windows (Docker Desktop) and from a Linux/macOS shell.
set -uo pipefail

AUTO=0
QUICK=0
BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --auto) AUTO=1 ;;
    --quick) QUICK=1 ;;
    --no-browser) BROWSER=0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
REPO_URL="https://github.com/jsankalp715/net-harness"
LAB_ROUTER="clab-ospf-tri-r1"
RUN=(bash scripts/run_in_docker.sh)

B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; C=$'\033[36m'; D=$'\033[2m'; R=$'\033[0m'

act() { printf '\n%s━━━ Act %s: %s ━━━━━━%s\n' "$B$C" "$1" "$2" "$R"; }  # stays < 80 columns
say() { printf '  %s\n' "$*"; }
hint() { printf '  %s💬 %s%s\n' "$Y" "$*" "$R"; }
ok() { printf '  %s✔ %s%s\n' "$G" "$*" "$R"; }

# Returns 1 if the presenter types "s" (skip this act).
pause() {
  [[ $AUTO == 1 ]] && return 0
  local ans
  read -r -p "  ${D}▶ Enter = go · s = skip this act ·${R} " ans || true
  [[ $ans != s && $ans != S ]]
}

is_windows() { [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; }

docker_ok() {
  local v
  v=$(timeout 20 docker info --format '{{.ServerVersion}}' 2>/dev/null) && [[ -n $v ]]
}

open_url() {
  if is_windows; then explorer.exe "$1" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null; then xdg-open "$1" >/dev/null 2>&1 || true
  elif command -v open >/dev/null; then open "$1" || true
  else say "open: $1"; fi
}

cleanup_lab() {
  "${RUN[@]}" /opt/venv/bin/python -m netharness destroy ospf_triangle >/dev/null 2>&1 || true
}

# ------------------------------------------------------------------------- Act 0
act 0 "Preflight"
if docker_ok; then
  ok "Docker is running"
else
  say "Docker isn't answering, so I'm starting it..."
  if is_windows; then
    powershell.exe -ExecutionPolicy Bypass -File "$(cygpath -w "$ROOT/scripts/start_docker_windows.ps1")"
  fi
  if ! docker_ok; then
    printf '  %sDocker is still not available. Start it, then re-run this script.%s\n' "$B" "$R"
    exit 1
  fi
  ok "Docker is running"
fi
say "Preparing the runner environment (first time only takes ~30 s)..."
"${RUN[@]}" make setup >/dev/null 2>&1 && ok "Ready" || { say "setup failed: run 'bash scripts/run_in_docker.sh make setup'"; exit 1; }
cleanup_lab  # in case a previous run was interrupted
echo
say "${B}net-harness${R}: break a network on purpose, measure how fast it heals,"
say "and fail CI when it gets slower."
hint "Start with the 30-second pitch, then press Enter."
pause || true

# ------------------------------------------------------------------------- Act 1
act 1 "Live demo: build a network, break it, watch it heal"
hint "Narrate each step: build → normal path → cut → repair → silent failure."
if pause; then
  "${RUN[@]}" /opt/venv/bin/python scripts/demo.py --keep
  KEPT=1
else
  KEPT=0
fi

# ------------------------------------------------------------------------- Act 2
act 2 "Inside a router (real FRR CLI)"
if [[ $KEPT == 1 ]]; then
  hint "\"Every number came from these routers. Here's r1's own view.\""
  if pause; then
    for cmd in "show ip ospf neighbor" "show ip route ospf" "show ipv6 route ospf6"; do
      printf '\n  %sr1# %s%s\n' "$B" "$cmd" "$R"
      docker exec "$LAB_ROUTER" vtysh -c "$cmd" 2>/dev/null \
        | grep -vE '^Codes|^ +[A-Za-z>].* - |^\s*$|^IPv[46] unicast' | sed 's/^/    /'
    done
    hint "Point out: two neighbours Full · two next hops to 10.1.23.0/30 = ECMP · IPv6 too."
    if [[ $AUTO == 0 ]] && [[ -t 0 ]]; then
      read -r -p "  ${D}▶ Open an interactive router shell? (type 'exit' to leave) [y/N]${R} " ans || true
      if [[ ${ans:-n} == [yY] ]]; then
        if is_windows; then winpty docker exec -it "$LAB_ROUTER" vtysh
        else docker exec -it "$LAB_ROUTER" vtysh; fi
      fi
    fi
  fi
  say "${D}Removing the demo lab...${R}"
  cleanup_lab
else
  say "Skipped (the live demo was skipped, so there's no lab running)."
fi

# ------------------------------------------------------------------------- Act 3
act 3 "The real test suite (what CI runs on every push)"
if [[ $QUICK == 1 ]]; then
  say "Skipped (--quick). Act 4 shows the most recent results instead."
else
  hint "\"These are the actual failover tests: IPv4 and IPv6, about 2 minutes.\""
  if pause; then
    PYTEST_ARGS="-q -o log_cli=false tests/integration/test_failover.py -k ospf" \
      "${RUN[@]}" make test-integration 2>&1 \
      | grep -E --line-buffered 'passed|failed|error|convergence times|s  (failover|restore)' \
      | sed -E 's#tests/integration/##; s/^/  /'
  fi
fi

# ------------------------------------------------------------------------- Act 4
act 4 "Evidence: every scenario writes a structured JSON log"
hint "\"Routing tables before, during and after each failure, plus the timings.\""
if pause; then
  if [[ -f results/summary.json ]]; then
    "${RUN[@]}" python3 scripts/show_results.py 2>/dev/null | sed 's/^/  /'
    hint "Logs are in results/. CI uploads them as an artifact on every run."
  else
    say "No results yet: run Act 3 once (without --quick)."
  fi
fi

# ------------------------------------------------------------------------- Act 5
act 5 "CI, regression check and trends"
hint "\"CI runs 162 tests on every push and fails if any timing regresses.\""
if [[ $BROWSER == 0 ]]; then
  say "Skipped (--no-browser): $REPO_URL"
elif pause; then
  open_url "$REPO_URL"
  open_url "$REPO_URL/actions"
  open_url "$REPO_URL/blob/main/docs/FINDINGS.md"
  if [[ -f results/trends/index.html ]]; then
    if is_windows; then open_url "$(cygpath -w "$ROOT/results/trends/index.html")"
    else open_url "$ROOT/results/trends/index.html"; fi
    ok "Opened: repo · CI runs · findings · trend chart"
  else
    ok "Opened: repo · CI runs · findings (run 'make trends' for the trend chart)"
  fi
  hint "Show: the README results table → a green run → FINDINGS (pick a story) → trends."
fi

echo
ok "${B}Demo complete.${R}"
hint "Close with what you'd do next: route-policy tests, nightly CI, bigger fabrics."
