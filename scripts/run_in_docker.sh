#!/usr/bin/env bash
# Run the harness inside a privileged runner container (containerlab + python).
#
# Use this on hosts where containerlab can't run natively, e.g. Docker Desktop on
# Windows (Git Bash) or macOS. On a Linux host with containerlab just run `make test`.
#
#   scripts/run_in_docker.sh                 # == make test
#   scripts/run_in_docker.sh make lint
#   PYTEST_ARGS="-m ospf -x" scripts/run_in_docker.sh
#
# containerlab resolves bind-mount paths (e.g. configs/daemons) relative to the topology
# file and hands them to the Docker daemon, so the project must be mounted at a path that
# is identical inside this container and on the Docker host/VM.
set -euo pipefail

IMAGE="${RUNNER_IMAGE:-netharness-runner:clab-0.79.0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    # Docker Desktop exposes Windows drives inside its VM at /run/desktop/mnt/host/<drive>/
    export MSYS_NO_PATHCONV=1
    win="$(cd "$ROOT" && pwd -W)"                     # e.g. D:/code review/net-harness
    drive="$(printf '%s' "${win:0:1}" | tr '[:upper:]' '[:lower:]')"
    HOSTPATH="/run/desktop/mnt/host/${drive}${win:2}"
    ;;
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null && [[ "$ROOT" == /mnt/* ]]; then
      HOSTPATH="/run/desktop/mnt/host${ROOT#/mnt}"   # WSL distro using Docker Desktop
    else
      HOSTPATH="$ROOT"
    fi
    ;;
  *)
    HOSTPATH="$ROOT"
    ;;
esac

(cd "$ROOT/docker" && docker build -q -t "$IMAGE" -f runner.Dockerfile . >/dev/null)

if [[ $# -eq 0 ]]; then
  set -- make test
fi

tty_flag=()
[[ -t 0 && -t 1 ]] && tty_flag=(-t)

exec docker run --rm "${tty_flag[@]}" \
  --privileged --network host --pid host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/run/netns:/var/run/netns \
  -v /var/lib/docker/containers:/var/lib/docker/containers \
  -v "$HOSTPATH:$HOSTPATH" -w "$HOSTPATH" \
  -v netharness-venv:/opt/venv \
  -e VENV=/opt/venv -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTEST_ARGS="${PYTEST_ARGS:-}" \
  "$IMAGE" "$@"
