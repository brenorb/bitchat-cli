from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bitchat_cli.state import MessageStore, RuntimePaths, pid_is_running


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


def test_pid_is_running_for_current_process() -> None:
    import os

    assert pid_is_running(os.getpid()) is True
