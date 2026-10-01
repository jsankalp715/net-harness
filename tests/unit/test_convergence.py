from __future__ import annotations

import time

import pytest

from netharness.convergence import ConvergenceTimeout, wait_for_convergence


class FakeLab:
    def __init__(self) -> None:
        self.dumped = 0

    def dump_routing_tables(self) -> str:
        self.dumped += 1
        return "===== r1 =====\nO>* 10.0.0.2/32 via eth1"


def test_returns_elapsed_once_true() -> None:
    deadline = time.monotonic() + 0.2
    elapsed = wait_for_convergence(lambda: time.monotonic() >= deadline, 2, 0.01)
    assert 0.15 <= elapsed < 1.0


def test_start_reference_is_honoured() -> None:
    start = time.monotonic() - 5.0
    elapsed = wait_for_convergence(lambda: True, 1, 0.01, start=start)
    assert elapsed >= 5.0


def test_timeout_raises_with_diagnostics() -> None:
    lab = FakeLab()
    with pytest.raises(ConvergenceTimeout) as exc:
        wait_for_convergence(lambda: False, 0.1, 0.02, lab=lab, description="r1 learns r2")
    assert lab.dumped == 1
    msg = str(exc.value)
    assert "r1 learns r2" in msg and "10.0.0.2/32" in msg
    assert isinstance(exc.value, AssertionError)  # pytest shows it as a failure


def test_predicate_exceptions_are_retried_and_reported() -> None:
    calls = {"n": 0}

    def flaky() -> bool:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("vtysh not ready")
        return True

    assert wait_for_convergence(flaky, 1, 0.01) >= 0
    with pytest.raises(ConvergenceTimeout, match="vtysh not ready"):
        wait_for_convergence(
            lambda: (_ for _ in ()).throw(RuntimeError("vtysh not ready")), 0.05, 0.01
        )


def test_consecutive_resets_on_flap() -> None:
    seq = iter([True, False, True, True, True])
    calls = []

    def pred() -> bool:
        calls.append(1)
        return next(seq)

    wait_for_convergence(pred, 1, 0.001, consecutive=3)
    assert len(calls) == 5
