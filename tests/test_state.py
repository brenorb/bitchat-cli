from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bitchat_cli.state import ActiveSessionLease, MessageStore, RuntimePaths, pid_is_running


def test_runtime_paths_are_stable(tmp_path: Path) -> None:
    paths = RuntimePaths(tmp_path)
    assert paths.state_path == tmp_path / "state.json"
    assert paths.daemon_pid_path == tmp_path / "daemon.pid"
    assert paths.daemon_log_path == tmp_path / "daemon.log"
    assert paths.inbox_path == tmp_path / "messages.jsonl"


def test_message_store_round_trip(tmp_path: Path) -> None:
    store = MessageStore(tmp_path / "messages.jsonl")
    ts = datetime(2026, 6, 23, 23, 0, 0, tzinfo=timezone.utc)
    store.append(
        direction="inbound",
        sender="bubbles",
        content="hello",
        timestamp=ts,
        peer_id="abcd",
    )

    rows = store.read_all()

    assert len(rows) == 1
    assert rows[0]["direction"] == "inbound"
    assert rows[0]["sender"] == "bubbles"
    assert rows[0]["content"] == "hello"
    assert rows[0]["peer_id"] == "abcd"
    assert rows[0]["timestamp"] == ts.isoformat()


def test_message_store_can_deduplicate_records_for_inbox_views(tmp_path: Path) -> None:
    store = MessageStore(tmp_path / "messages.jsonl")
    ts = datetime(2026, 6, 24, 0, 9, 44, 932000, tzinfo=timezone.utc)
    store.append(
        direction="inbound",
        sender="bubbles",
        content="Opa, ta escutando?",
        timestamp=ts,
        peer_id="abcd",
        message_id="sig:1234",
    )
    store.append(
        direction="inbound",
        sender="bubbles",
        content="Opa, ta escutando?",
        timestamp=ts,
        peer_id="abcd",
        message_id="sig:1234",
    )

    rows = store.read_all(deduplicate=True)

    assert len(rows) == 1
    assert rows[0]["message_id"] == "sig:1234"


def test_active_session_lease_blocks_live_pid_and_reclaims_stale_pid(tmp_path: Path) -> None:
    active_pids = {111}

    def is_running(pid: int) -> bool:
        return pid in active_pids

    lease = ActiveSessionLease(tmp_path / "radio.pid", is_running=is_running)

    acquired, owner_pid = lease.acquire(pid=111)
    assert acquired is True
    assert owner_pid is None

    acquired, owner_pid = lease.acquire(pid=222)
    assert acquired is False
    assert owner_pid == 111

    active_pids.clear()
    acquired, owner_pid = lease.acquire(pid=222)
    assert acquired is True
    assert owner_pid is None


def test_pid_is_running_for_current_process() -> None:
    import os

    assert pid_is_running(os.getpid()) is True
