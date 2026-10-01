"""Small subprocess wrapper so every external call is logged and errors are uniform."""

from __future__ import annotations

import logging
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

log = logging.getLogger(__name__)


class CommandError(RuntimeError):
    """Raised when a command exits non-zero and ``check=True``."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        super().__init__(
            f"command failed (rc={result.returncode}): {shlex.join(result.argv)}\n"
            f"stdout: {result.stdout.strip()[-2000:]}\n"
            f"stderr: {result.stderr.strip()[-2000:]}"
        )


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(
    argv: Sequence[str],
    *,
    check: bool = True,
    input_text: str | None = None,
    timeout: float | None = 300,
    quiet: bool = False,
) -> CommandResult:
    """Run ``argv`` (no shell), capture output, optionally raise :class:`CommandError`."""
    if not quiet:
        log.debug("$ %s", shlex.join(argv))
    proc = subprocess.run(
        list(argv),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(tuple(argv), proc.returncode, proc.stdout, proc.stderr)
    if check and not result.ok:
        raise CommandError(result)
    return result
