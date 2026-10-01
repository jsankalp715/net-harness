# CLAUDE.md — net-harness

Automated network test harness: spins up containerlab topologies of FRR routers,
configures OSPF/BGP, injects link faults, and asserts routing-table convergence.
Everything is driven by pytest.

## Hard constraints

- **Python 3.11+**, always inside a venv (`make setup` creates `.venv`, override with `VENV=`).
- **containerlab + Docker** for topologies. Router image is pinned:
  `quay.io/frrouting/frr:10.5.5` — change it only in `netharness/constants.py` *and* the
  `topologies/*.clab.yml` files (a unit test enforces they match).
- **Topologies** are containerlab YAML in `topologies/<name>.clab.yml`; per-node template
  variables live next to them in `topologies/<name>.vars.yml`. A variant topology's vars
  file may start with `extends: <base>.vars.yml` and state only the delta (see `*_bfd`).
- **Router configs are rendered from Jinja2** templates in `templates/` and pushed with
  `vtysh -f` after deploy. Never hand-write `frr.conf` files.
- The FRR `daemons` file (`configs/daemons`) is static and bind-mounted; it is not templated.
- **Type hints everywhere**; `mypy --strict` on `netharness/` must pass. `ruff` for lint/format.
- **Makefile targets**: `setup`, `test`, `lint`, `clean` (plus helpers). `make test` is the
  single command that runs the whole suite on a Linux host with containerlab.
  On Docker Desktop hosts (Windows/macOS) use `scripts/run_in_docker.sh`, which runs the
  same `make test` inside a privileged runner container.
- **CI**: GitHub Actions (`.github/workflows/ci.yml`) runs lint, unit and integration tests on
  every push and uploads `results/` as an artifact.

## Layout

```
netharness/            library code (importable package)
  constants.py         pinned images, defaults
  shell.py             subprocess wrapper (CommandResult, run())
  clab.py              thin containerlab CLI wrapper (deploy/destroy/inspect)
  lab.py               Lab class: deploy/destroy, render+push configs, vtysh helpers
  parsers.py           pure functions: vtysh JSON -> dataclasses (unit-tested)
  render.py            Jinja2 environment + render_node_config()
  convergence.py       wait_for_convergence() + ConvergenceTimeout
  faults.py            link down/up, netem (optional, capability-probed)
  results.py           ScenarioRecord + JSON writer used by the pytest hook
topologies/            *.clab.yml + *.vars.yml
templates/             frr.conf.j2 and partials (_interfaces, _ospf, _bgp)
configs/daemons        static FRR daemons file
tests/unit/            no Docker needed (parsers, templates, convergence, baseline script)
tests/integration/     real labs; markers: ospf, bgp, bfd, failure, netem
tests/conftest.py      lab fixtures + JSON-per-scenario reporting hook
scripts/               run_in_docker.sh, compare_baseline.py
baseline/              convergence_baseline.json (checked in)
results/               generated per-run JSON logs (git-ignored)
docker/runner.Dockerfile  runner image (clab + python) for Docker Desktop hosts
```

## Conventions

- Lab fixtures are **module-scoped**: one deploy per test module, destroyed at module end.
- Every scenario must leave the lab as it found it (links back up, netem removed).
- Integration tests use the `scenario` fixture to record routing tables before/after and the
  measured convergence time; the hook in `tests/conftest.py` writes `results/<test>.json`.
- Always pass `encoding="utf-8"` to read_text/write_text (Windows defaults to cp1252).
- Interface names: `eth0` is containerlab management; data links start at `eth1`.
- Add a scenario: see "Adding a scenario" in README.md.
