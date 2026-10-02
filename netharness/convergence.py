"""Polling utility that measures how long the network takes to reach a desired state."""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from typing import Protocol

from netharness import constants

log = logging.getLogger(__name__)


class SupportsDiagnostics(Protocol):
    def dump_routing_tables(self) -> str: ...


class ConvergenceTimeout(AssertionError):
    """The predicate never held within the timeout.

    Subclasses ``AssertionError`` so pytest reports it as a test *failure*, not an error.
    """

    def __init__(
        self, description: str, timeout: float, last_error: str | None, diagnostics: str
    ) -> None:
        self.description = description
        self.timeout = timeout
        self.last_error = last_error
        self.diagnostics = diagnostics
        msg = f"did not converge within {timeout:.1f}s: {description}"
        if last_error:
            msg += f"\nlast predicate error: {last_error}"
        if diagnostics:
            msg += f"\n\n----- diagnostic dump -----\n{diagnostics}"
        super().__init__(msg)


def wait_for_convergence(
    predicate: Callable[[], bool],
    timeout: float = constants.DEFAULT_CONVERGENCE_TIMEOUT,
    poll_interval: float = constants.DEFAULT_POLL_INTERVAL,
    *,
    lab: SupportsDiagnostics | None = None,
    description: str = "",
    start: float | None = None,
    consecutive: int = 1,
) -> float:
    """Poll ``predicate`` until it returns True; return the elapsed seconds.

    Args:
        predicate: zero-arg callable; exceptions count as "not converged yet"
            (vtysh can be briefly unavailable while daemons react to a fault).
        timeout: seconds before giving up.
        poll_interval: sleep between polls.
        lab: if given, its routing tables are dumped into the exception on timeout.
        description: human-readable goal, used in logs and the failure message.
        start: ``time.monotonic()`` reference point. Pass the instant a fault was
            injected so the returned time is the true convergence time.
        consecutive: require this many successive true polls (guards against flaps).
            The elapsed time reported is the *end* of the first poll in the stable run,
            i.e. the instant convergence was first observed (a conservative upper bound).

    Raises:
        ConvergenceTimeout: with a diagnostic dump of every node's routing table.
    """
    t0 = time.monotonic() if start is None else start
    deadline = time.monotonic() + timeout
    streak = 0
    first_true: float | None = None
    last_error: str | None = None
    desc = description or getattr(predicate, "__name__", "predicate")

    while True:
        now = time.monotonic()
        try:
            ok = bool(predicate())
            last_error = None
        except Exception as exc:  # predicate failures are retried until timeout
            ok = False
            last_error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        if ok:
            if streak == 0:
                # stamp when the poll *finished*: that's when convergence was observed.
                # (Stamping its start under-reports by the poll's duration, which is
                # ~1 s for predicates that query many nodes.)
                first_true = time.monotonic()
            streak += 1
            if streak >= consecutive:
                assert first_true is not None
                elapsed = first_true - t0
                log.info("converged in %.3fs: %s", elapsed, desc)
                return elapsed
        else:
            streak = 0
            first_true = None
        if now >= deadline:
            break
        time.sleep(poll_interval)

    diagnostics = ""
    if lab is not None:
        try:
            diagnostics = lab.dump_routing_tables()
        except Exception as exc:
            diagnostics = f"<diagnostic dump failed: {exc}>"
    raise ConvergenceTimeout(desc, timeout, last_error, diagnostics)
