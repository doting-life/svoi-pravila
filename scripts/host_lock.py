"""Run a command while holding an exclusive host-wide lock.

Usage: host_lock.py --name NAME --timeout SECONDS -- COMMAND [ARG...]

The lock is an ``fcntl.flock`` on ``${XDG_CACHE_HOME:-$HOME/.cache}/svoi-pravila/locks/NAME.lock``.
The kernel releases it when the process ends, so there are no stale locks. When the lock is
busy one line is printed and the call blocks until it is free or the timeout expires (exit 124).
Otherwise the exit code of the command is passed through.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from types import FrameType

TIMEOUT_EXIT = 124
USAGE_EXIT = 2
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class LockTimeoutError(Exception):
    """Raised when the lock was not acquired within the timeout."""


def _lock_path(name: str) -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "svoi-pravila" / "locks" / f"{name}.lock"


def _on_alarm(_signum: int, _frame: FrameType | None) -> None:
    raise LockTimeoutError


def _acquire(fd: int, name: str, timeout: int) -> None:
    """Take the lock; when it is busy print one line and block until free or timeout."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        return
    print(f"ci: {name} waiting for host lock", flush=True)
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(timeout)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _exit_code(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def main(argv: list[str]) -> int:
    """Run the command under the named lock and return the exit code to use."""
    parser = argparse.ArgumentParser(prog="host_lock.py")
    parser.add_argument("--name", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.timeout < 1 or _NAME_RE.fullmatch(args.name) is None:
        print(
            "usage: host_lock.py --name NAME --timeout SECONDS -- COMMAND [ARG...]",
            file=sys.stderr,
        )
        return USAGE_EXIT

    path = _lock_path(args.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            _acquire(fd, args.name, args.timeout)
        except LockTimeoutError:
            print(
                f"ci: {args.name} host lock not acquired within {args.timeout}s ({path})",
                file=sys.stderr,
            )
            return TIMEOUT_EXIT
        return _exit_code(subprocess.run(command, check=False).returncode)
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
