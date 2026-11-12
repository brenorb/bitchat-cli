"""Replay two BitChat sessions acquiring the same fresh lease in a scratch directory."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from bitchat_cli.state import ActiveSessionLease


def main() -> None:
    with TemporaryDirectory(prefix="bitchat-radio-trace-") as scratch:
        path = Path(scratch) / "radio.pid"
        foreground = ActiveSessionLease(path, is_running=lambda _pid: True)
        daemon = ActiveSessionLease(path, is_running=lambda _pid: True)
        both_read = Barrier(2, timeout=3)

        def acquire(lease: ActiveSessionLease, pid: int):
            read_owner = lease.read_owner_pid

            def read_then_wait():
                owner = read_owner()
                both_read.wait()
                return owner

            with patch.object(lease, "read_owner_pid", read_then_wait):
                return lease.acquire(pid=pid)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(acquire, foreground, 1001)
            second = pool.submit(acquire, daemon, 1002)
            outcomes = [first.result(timeout=5), second.result(timeout=5)]

        print(f"Lease acquisition results: {outcomes}")
        assert outcomes == [(True, None), (True, None)], outcomes


if __name__ == "__main__":
    main()
