"""Single-instance run lock.

A daily cron firing while a previous (slow, browser-driven) run is still going
would drive the same browser profile and could double-submit applications. This
PID-based lock makes overlapping runs fail fast instead.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class AlreadyRunning(RuntimeError):
    """Raised when another run already holds the lock."""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextmanager
def run_lock(lock_file: Path) -> Iterator[None]:
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    if lock_file.exists():
        try:
            old = int(lock_file.read_text(encoding="utf-8").strip() or "0")
        except (ValueError, OSError):
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            raise AlreadyRunning(
                f"Another run is already in progress (pid {old}). "
                f"If that's wrong, delete {lock_file}."
            )
        # Stale lock from a dead process: reclaim it.

    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        yield
    finally:
        try:
            if lock_file.exists() and lock_file.read_text(encoding="utf-8").strip() == str(
                os.getpid()
            ):
                lock_file.unlink()
        except OSError:
            pass
