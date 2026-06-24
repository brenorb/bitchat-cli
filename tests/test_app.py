from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from bitchat_cli import app
from bitchat_cli.crypto import Identity
from bitchat_cli.peripheral import NullPeripheralEndpoint
from bitchat_cli.state import MessageStore, PeerStore, RuntimePaths


def test_doctor_payload_reports_mesh_relay_support(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimePaths(tmp_path)
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "peripheral_supported", lambda: False)

    payload = app.doctor_payload(app.StateStore(runtime.state_path))

    assert payload["scope"]["mesh_relay"] is True
    assert payload["scope"]["local_sync"] is True
    assert payload["scope"]["ble_peripheral"] is False


class FakePeripheralEndpoint(NullPeripheralEndpoint):
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(
        self,
        data: bytes,
        *,
        target_addresses: set[str] | None = None,
        exclude_addresses: set[str] | None = None,
    ) -> None:
        self.sent.append(data)


def test_build_transport_attaches_platform_peripheral_endpoint(monkeypatch) -> None:
    endpoint = FakePeripheralEndpoint()
    calls: list[tuple[str, object]] = []

    def fake_create_peripheral_endpoint(**kwargs):
        calls.append(("create", kwargs["system_log"]))
        return endpoint

    monkeypatch.setattr(app, "create_peripheral_endpoint", fake_create_peripheral_endpoint)

    transport = app._build_transport(
        nickname="local",
        identity=Identity.generate(),
        scan_interval=5.0,
        system_log=lambda _message: None,
        message_log=lambda *_args: None,
        peer_log=None,
        private_message_log=None,
        receipt_log=None,
        background_mode=False,
    )

    asyncio.run(transport.send_public_message("hello app"))

    assert calls != []
    assert transport.peripheral_endpoint is endpoint
    assert endpoint.sent != []


def test_run_peers_lists_persisted_peers(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    identity = Identity.generate()
    PeerStore(runtime.peers_path).upsert(
        peer_id=identity.peer_id,
        nickname="bubbles",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        addresses={"A1", "B2"},
        last_seen=datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_peers(argparse.Namespace(json=False))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "bubbles" in captured.out
    assert identity.peer_id in captured.out
    assert "A1, B2" in captured.out


def test_run_peers_json_returns_known_peers(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    identity = Identity.generate()
    PeerStore(runtime.peers_path).upsert(
        peer_id=identity.peer_id,
        nickname="bubbles",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        addresses={"A1"},
        last_seen=datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_peers(argparse.Namespace(json=True))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"peer_id"' in captured.out
    assert identity.peer_id in captured.out


def test_run_inbox_ignores_private_dm_records(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    store = MessageStore(runtime.inbox_path)
    timestamp = datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc)
    store.append(
        direction="inbound",
        sender="bubbles",
        content="public hello",
        timestamp=timestamp,
        peer_id="abcd",
        scope="public",
    )
    store.append(
        direction="inbound",
        sender="bubbles",
        content="secret hello",
        timestamp=timestamp,
        peer_id="abcd",
        message_id="dm-1",
        scope="dm",
        conversation_id="abcd",
        message_kind="private_message",
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_inbox(argparse.Namespace(json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "public hello" in captured.out
    assert "secret hello" not in captured.out


def test_run_dm_inbox_summarizes_private_conversations(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    identity = Identity.generate()
    PeerStore(runtime.peers_path).upsert(
        peer_id=identity.peer_id,
        nickname="bubbles",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        addresses={"A1"},
        last_seen=datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc),
    )
    store = MessageStore(runtime.inbox_path)
    store.append(
        direction="inbound",
        sender="bubbles",
        content="secret hello",
        timestamp=datetime(2026, 6, 24, 0, 10, 0, tzinfo=timezone.utc),
        peer_id=identity.peer_id,
        message_id="dm-1",
        scope="dm",
        conversation_id=identity.peer_id,
        message_kind="private_message",
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_dm_inbox(argparse.Namespace(json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "bubbles" in captured.out
    assert "secret hello" in captured.out


def test_run_dm_history_filters_one_conversation(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    alice = Identity.generate()
    bubbles = Identity.generate()
    peer_store = PeerStore(runtime.peers_path)
    peer_store.upsert(
        peer_id=alice.peer_id,
        nickname="alice",
        noise_public_key=alice.noise_public_key,
        signing_public_key=alice.signing_public_key,
        addresses={"A1"},
        last_seen=datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc),
    )
    peer_store.upsert(
        peer_id=bubbles.peer_id,
        nickname="bubbles",
        noise_public_key=bubbles.noise_public_key,
        signing_public_key=bubbles.signing_public_key,
        addresses={"B1"},
        last_seen=datetime(2026, 6, 24, 0, 9, 45, tzinfo=timezone.utc),
    )
    store = MessageStore(runtime.inbox_path)
    store.append(
        direction="outbound",
        sender="clanker",
        content="for alice",
        timestamp=datetime(2026, 6, 24, 0, 10, 0, tzinfo=timezone.utc),
        peer_id=alice.peer_id,
        message_id="dm-1",
        scope="dm",
        conversation_id=alice.peer_id,
        message_kind="private_message",
        target_peer_id=alice.peer_id,
    )
    store.append(
        direction="inbound",
        sender="bubbles",
        content="for bubbles",
        timestamp=datetime(2026, 6, 24, 0, 10, 1, tzinfo=timezone.utc),
        peer_id=bubbles.peer_id,
        message_id="dm-2",
        scope="dm",
        conversation_id=bubbles.peer_id,
        message_kind="private_message",
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_dm_history(argparse.Namespace(target="@alice", json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "for alice" in captured.out
    assert "for bubbles" not in captured.out


def test_run_dm_send_uses_daemon_command_and_resolves_nickname(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    identity = Identity.generate()
    PeerStore(runtime.peers_path).upsert(
        peer_id=identity.peer_id,
        nickname="bubbles",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        addresses={"A1"},
        last_seen=datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc),
    )
    seen: list[dict[str, object]] = []

    def fake_send_daemon_command(_runtime: RuntimePaths, payload: dict[str, object]) -> dict[str, object]:
        seen.append(payload)
        return {"ok": True, "message_id": "dm-1"}

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "_send_daemon_command", fake_send_daemon_command)

    exit_code = app.run_dm_send(argparse.Namespace(target="@bubbles", message="hello from cli"))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert seen == [
        {
            "command": "dm_send",
            "peer_id": identity.peer_id,
            "content": "hello from cli",
        }
    ]
    assert "dm-1" in captured.out


def test_run_send_uses_daemon_command(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    seen: list[dict[str, object]] = []

    def fake_send_daemon_command(_runtime: RuntimePaths, payload: dict[str, object]) -> dict[str, object]:
        seen.append(payload)
        return {"ok": True}

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "_send_daemon_command", fake_send_daemon_command)

    exit_code = app.run_send(argparse.Namespace(message="hello #mesh"))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert seen == [
        {
            "command": "public_send",
            "content": "hello #mesh",
        }
    ]
    assert "queued public message" in captured.out


def test_run_daemon_start_uses_repo_src_in_pythonpath(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    seen: dict[str, object] = {}

    class FakeProcess:
        pid = 43210

    def fake_popen(command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        seen["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app.subprocess, "Popen", fake_popen)

    exit_code = app.run_daemon_start(argparse.Namespace(nickname="clanker", scan_interval=5.0))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert seen["command"][:3] == [app.sys.executable, "-m", "bitchat_cli.cli"]
    assert str((Path(app.__file__).resolve().parents[1])) in str(seen["env"]["PYTHONPATH"])
    assert "43210" in captured.out


def test_run_daemon_status_includes_live_runtime_fields(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    runtime.root.mkdir(parents=True, exist_ok=True)
    runtime.daemon_pid_path.write_text("43210\n", encoding="utf-8")
    seen: list[dict[str, object]] = []

    def fake_send_daemon_command(_runtime: RuntimePaths, payload: dict[str, object]) -> dict[str, object]:
        seen.append(payload)
        return {
            "ok": True,
            "runtime": {
                "connected_links": 1,
                "known_peers": 2,
                "peripheral_advertising": True,
                "peripheral_subscribers": 3,
            },
        }

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "pid_is_running", lambda _pid: True)
    monkeypatch.setattr(app, "_send_daemon_command", fake_send_daemon_command)

    exit_code = app.run_daemon_status(argparse.Namespace())

    captured = capsys.readouterr()
    assert exit_code == 0
    assert seen == [{"command": "runtime_status"}]
    assert "connected_links: 1" in captured.out
    assert "known_peers:     2" in captured.out
    assert "peripheral_adv:  True" in captured.out
    assert "peripheral_subs: 3" in captured.out


def test_run_daemon_stop_waits_for_exit_and_cleans_runtime_files(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    runtime.root.mkdir(parents=True, exist_ok=True)
    runtime.daemon_pid_path.write_text("43210\n", encoding="utf-8")
    runtime.radio_pid_path.write_text("43210\n", encoding="utf-8")
    seen: list[tuple[int, int]] = []
    polls = iter([True, False])

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app.os, "kill", lambda pid, sig: seen.append((pid, sig)))
    monkeypatch.setattr(app, "pid_is_running", lambda pid: next(polls))
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)

    exit_code = app.run_daemon_stop(argparse.Namespace())

    captured = capsys.readouterr()
    assert exit_code == 0
    assert seen == [(43210, app.signal.SIGTERM)]
    assert runtime.daemon_pid_path.exists() is False
    assert runtime.radio_pid_path.exists() is False
    assert "stopped daemon (pid 43210)" in captured.out


def test_run_daemon_stop_reports_timeout_when_process_stays_up(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    runtime.root.mkdir(parents=True, exist_ok=True)
    runtime.daemon_pid_path.write_text("43210\n", encoding="utf-8")
    runtime.radio_pid_path.write_text("43210\n", encoding="utf-8")
    seen: list[tuple[int, int]] = []

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app.os, "kill", lambda pid, sig: seen.append((pid, sig)))
    monkeypatch.setattr(app, "pid_is_running", lambda _pid: True)
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)
    monotonic_values = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.1])
    monkeypatch.setattr(app.time, "monotonic", lambda: next(monotonic_values))

    exit_code = app.run_daemon_stop(argparse.Namespace())

    captured = capsys.readouterr()
    assert exit_code == 1
    assert seen == [(43210, app.signal.SIGTERM)]
    assert runtime.daemon_pid_path.exists() is True
    assert runtime.radio_pid_path.exists() is True
    assert "still shutting down" in captured.out


def test_install_stop_signal_handlers_prefers_event_loop_handlers() -> None:
    stop_event = asyncio.Event()
    seen: list[tuple[str, int, object | None]] = []

    class FakeLoop:
        def add_signal_handler(self, sig, callback) -> None:
            seen.append(("add", sig, callback))

        def remove_signal_handler(self, sig) -> None:
            seen.append(("remove", sig, None))

    original_get_running_loop = app.asyncio.get_running_loop
    app.asyncio.get_running_loop = lambda: FakeLoop()  # type: ignore[assignment]
    try:
        cleanup = app._install_stop_signal_handlers(stop_event)
        cleanup()
    finally:
        app.asyncio.get_running_loop = original_get_running_loop  # type: ignore[assignment]

    assert [item[:2] for item in seen] == [
        ("add", app.signal.SIGINT),
        ("add", app.signal.SIGTERM),
        ("remove", app.signal.SIGINT),
        ("remove", app.signal.SIGTERM),
    ]
    seen[0][2]()  # type: ignore[misc]
    assert stop_event.is_set() is True


def test_install_stop_signal_handlers_falls_back_to_signal_module(monkeypatch) -> None:
    stop_event = asyncio.Event()
    seen: list[tuple[str, int]] = []

    class FakeLoop:
        def add_signal_handler(self, _sig, _callback) -> None:
            raise NotImplementedError

    monkeypatch.setattr(app.asyncio, "get_running_loop", lambda: FakeLoop())
    monkeypatch.setattr(app.signal, "signal", lambda sig, handler: seen.append(("set", sig)) or "old-handler")

    cleanup = app._install_stop_signal_handlers(stop_event)
    cleanup()

    assert seen == [
        ("set", app.signal.SIGINT),
        ("set", app.signal.SIGTERM),
        ("set", app.signal.SIGINT),
        ("set", app.signal.SIGTERM),
    ]


def test_run_dm_history_emits_read_receipts_for_unread_inbound_messages(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    identity = Identity.generate()
    peer_store = PeerStore(runtime.peers_path)
    peer_store.upsert(
        peer_id=identity.peer_id,
        nickname="bubbles",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        addresses={"A1"},
        last_seen=datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc),
    )
    store = MessageStore(runtime.inbox_path)
    store.append(
        direction="inbound",
        sender="bubbles",
        content="first unread",
        timestamp=datetime(2026, 6, 24, 0, 10, 0, tzinfo=timezone.utc),
        peer_id=identity.peer_id,
        message_id="dm-1",
        scope="dm",
        conversation_id=identity.peer_id,
        message_kind="private_message",
    )
    store.append(
        direction="outbound",
        sender="clanker",
        content="read_receipt",
        timestamp=datetime(2026, 6, 24, 0, 10, 1, tzinfo=timezone.utc),
        peer_id=identity.peer_id,
        message_id="dm-2",
        scope="dm",
        conversation_id=identity.peer_id,
        message_kind="receipt",
        target_peer_id=identity.peer_id,
    )
    store.append(
        direction="inbound",
        sender="bubbles",
        content="already read",
        timestamp=datetime(2026, 6, 24, 0, 10, 2, tzinfo=timezone.utc),
        peer_id=identity.peer_id,
        message_id="dm-2",
        scope="dm",
        conversation_id=identity.peer_id,
        message_kind="private_message",
    )
    seen: list[dict[str, object]] = []

    def fake_send_daemon_command(_runtime: RuntimePaths, payload: dict[str, object]) -> dict[str, object]:
        seen.append(payload)
        return {"ok": True, "count": len(payload["message_ids"])}

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "_send_daemon_command", fake_send_daemon_command)

    exit_code = app.run_dm_history(argparse.Namespace(target="@bubbles", json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "first unread" in captured.out
    assert "already read" in captured.out
    assert seen == [
        {
            "command": "dm_mark_read",
            "peer_id": identity.peer_id,
            "message_ids": ["dm-1"],
        }
    ]
