from __future__ import annotations

import argparse
import asyncio
import io
from datetime import datetime, timezone
from pathlib import Path

from bitchat_cli import app, geochat
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
    assert payload["scope"]["file_transfer"] is True


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


class FakeDaemonClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.runtime_status_payload: dict[str, object] = {}
        self.responses: dict[str, dict[str, object]] = {}

    def runtime_status(self) -> dict[str, object]:
        self.calls.append({"command": "runtime_status"})
        return self.runtime_status_payload

    def send_dm(self, *, peer_id: str, content: str) -> dict[str, object]:
        self.calls.append({"command": "dm_send", "peer_id": peer_id, "content": content})
        return self.responses.get("dm_send", {"message_id": "dm-1"})

    def send_dm_file(self, *, peer_id: str, path: str) -> dict[str, object]:
        self.calls.append({"command": "dm_send_file", "peer_id": peer_id, "path": path})
        return self.responses.get("dm_send_file", {"transfer_id": "file-1"})

    def send_public(self, *, content: str) -> dict[str, object]:
        self.calls.append({"command": "public_send", "content": content})
        return self.responses.get("public_send", {"ok": True})

    def send_public_file(self, *, path: str) -> dict[str, object]:
        self.calls.append({"command": "public_send_file", "path": path})
        return self.responses.get("public_send_file", {"transfer_id": "file-1"})

    def send_geo(
        self,
        *,
        geohash: str,
        content: str,
        pow_difficulty: int,
        teleported: bool,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "command": "geo_send",
                "geohash": geohash,
                "content": content,
                "pow_difficulty": pow_difficulty,
                "teleported": teleported,
            }
        )
        return self.responses.get("geo_send", {"message_id": "geo-1"})

    def send_geo_dm(self, *, geohash: str, peer_pubkey: str, content: str) -> dict[str, object]:
        self.calls.append(
            {
                "command": "geo_dm_send",
                "geohash": geohash,
                "peer_pubkey": peer_pubkey,
                "content": content,
            }
        )
        return self.responses.get("geo_dm_send", {"message_id": "geo-dm-1"})

    def set_geo_watches(self, geohashes: list[str]) -> dict[str, object]:
        self.calls.append({"command": "geo_watch_set", "geohashes": geohashes})
        return self.responses.get("geo_watch_set", {"geohashes": geohashes})

    def mark_dm_read(self, *, peer_id: str, message_ids: list[str]) -> dict[str, object]:
        self.calls.append({"command": "dm_mark_read", "peer_id": peer_id, "message_ids": message_ids})
        return self.responses.get("dm_mark_read", {"count": len(message_ids)})


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


def test_build_transport_delays_peripheral_subscribe_announce(monkeypatch) -> None:
    endpoint = FakePeripheralEndpoint()
    callbacks: dict[str, object] = {}
    created_tasks: list[object] = []

    class FakeHandle:
        def __init__(self, delay: float, callback, args: tuple[object, ...]) -> None:
            self.delay = delay
            self.callback = callback
            self.args = args
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class FakeLoop:
        def __init__(self) -> None:
            self.handles: list[FakeHandle] = []

        def call_later(self, delay: float, callback, *args):
            handle = FakeHandle(delay, callback, args)
            self.handles.append(handle)
            return handle

    fake_loop = FakeLoop()

    def fake_create_task(coro):
        created_tasks.append(coro)
        return coro

    def fake_create_peripheral_endpoint(**kwargs):
        callbacks["subscribe"] = kwargs["on_subscribe"]
        callbacks["unsubscribe"] = kwargs["on_unsubscribe"]
        return endpoint

    monkeypatch.setattr(app, "create_peripheral_endpoint", fake_create_peripheral_endpoint)
    monkeypatch.setattr(app.asyncio, "get_running_loop", lambda: fake_loop)
    monkeypatch.setattr(app.asyncio, "create_task", fake_create_task)

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

    callbacks["subscribe"]("peripheral:A1")

    assert created_tasks == []
    assert len(fake_loop.handles) == 1
    assert fake_loop.handles[0].delay == app._PERIPHERAL_SUBSCRIBE_ANNOUNCE_DELAY_SECONDS

    fake_loop.handles[0].callback(*fake_loop.handles[0].args)

    assert len(created_tasks) == 1
    created_tasks[0].close()
    assert transport.peripheral_endpoint is endpoint


def test_build_transport_cancels_pending_subscribe_announce_on_unsubscribe(monkeypatch) -> None:
    endpoint = FakePeripheralEndpoint()
    callbacks: dict[str, object] = {}
    unsubscribed: list[str] = []

    class FakeHandle:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class FakeLoop:
        def __init__(self) -> None:
            self.handle = FakeHandle()

        def call_later(self, _delay: float, _callback, *_args):
            return self.handle

    fake_loop = FakeLoop()

    def fake_create_peripheral_endpoint(**kwargs):
        callbacks["subscribe"] = kwargs["on_subscribe"]
        callbacks["unsubscribe"] = kwargs["on_unsubscribe"]
        return endpoint

    monkeypatch.setattr(app, "create_peripheral_endpoint", fake_create_peripheral_endpoint)
    monkeypatch.setattr(app.asyncio, "get_running_loop", lambda: fake_loop)

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
    transport._handle_peripheral_unsubscribe = lambda address: unsubscribed.append(address)  # type: ignore[method-assign]

    callbacks["subscribe"]("peripheral:A1")
    callbacks["unsubscribe"]("peripheral:A1")

    assert fake_loop.handle.cancelled is True
    assert unsubscribed == ["peripheral:A1"]


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


def test_run_inbox_limits_public_view_to_mesh_conversation(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    store = MessageStore(runtime.inbox_path)
    timestamp = datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc)
    store.append(
        direction="inbound",
        sender="bubbles",
        content="mesh hello",
        timestamp=timestamp,
        peer_id="abcd",
        scope="public",
        conversation_id="mesh",
    )
    store.append(
        direction="inbound",
        sender="bubbles",
        content="legacy hello",
        timestamp=timestamp,
        peer_id="abcd",
        scope="public",
    )
    store.append(
        direction="inbound",
        sender="bubbles",
        content="other channel",
        timestamp=timestamp,
        peer_id="abcd",
        scope="public",
        conversation_id="somewhere-else",
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_inbox(argparse.Namespace(json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "mesh hello" in captured.out
    assert "legacy hello" in captured.out
    assert "other channel" not in captured.out


def test_run_inbox_formats_public_file_records(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    store = MessageStore(runtime.inbox_path)
    timestamp = datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc)
    store.append(
        direction="inbound",
        sender="bubbles",
        content=str(runtime.incoming_images_dir / "photo.jpg"),
        timestamp=timestamp,
        peer_id="abcd",
        message_id="file-1",
        scope="public",
        conversation_id="mesh",
        message_kind="public_file",
        attachment_name="photo.jpg",
        attachment_mime="image/jpeg",
        attachment_size_bytes=2048,
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_inbox(argparse.Namespace(json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[image]" in captured.out
    assert "photo.jpg" in captured.out
    assert "2.0 KB" in captured.out


def test_run_geo_inbox_filters_to_requested_geohash(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    store = MessageStore(runtime.inbox_path)
    timestamp = datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc)
    store.append(
        direction="inbound",
        sender="bubbles#aa11",
        content="geo hello",
        timestamp=timestamp,
        peer_id="a" * 64,
        message_id="geo-1",
        scope="public",
        conversation_id="u4pruy",
        message_kind="geohash_message",
    )
    store.append(
        direction="inbound",
        sender="bubbles#bb22",
        content="other geo",
        timestamp=timestamp,
        peer_id="b" * 64,
        message_id="geo-2",
        scope="public",
        conversation_id="u4pruv",
        message_kind="geohash_message",
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_geo_inbox(argparse.Namespace(geohash="#u4pruy", json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "#u4pruy" in captured.out
    assert "geo hello" in captured.out
    assert "other geo" not in captured.out


def test_run_geo_watch_add_persists_normalized_geohash(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    fake_client = FakeDaemonClient()
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: fake_client)

    exit_code = app.run_geo_watch_add(argparse.Namespace(geohash="#U4PrUy"))

    captured = capsys.readouterr()
    state = app.StateStore(runtime.state_path).load()
    assert exit_code == 0
    assert state.watched_geohashes == ["u4pruy"]
    assert "u4pruy" in captured.out


def test_record_geohash_event_persists_presence_without_printing(tmp_path: Path) -> None:
    store = MessageStore(tmp_path / "messages.jsonl")
    log_handle = io.StringIO()

    app._record_geohash_event(
        {
            "id": "evt-1",
            "pubkey": "ab" * 32,
            "created_at": 1_719_188_400,
            "kind": 20001,
            "tags": [["g", "u4pruy"], ["n", "bubbles"]],
            "content": "",
        },
        message_store=store,
        log_handle=log_handle,
    )

    rows = store.read_all()
    assert rows == [
        {
            "recorded_at": rows[0]["recorded_at"],
            "timestamp": "2024-06-24T00:20:00+00:00",
            "direction": "inbound",
            "sender": "bubbles#abab",
            "peer_id": "ab" * 32,
            "content": "",
            "message_id": "evt-1",
            "scope": "public",
            "conversation_id": "u4pruy",
            "message_kind": "geohash_presence",
            "target_peer_id": None,
            "delivery_state": None,
            "pow_difficulty": None,
            "teleported": False,
        }
    ]
    assert log_handle.getvalue() == ""


def test_record_geohash_event_persists_pow_and_teleport_metadata(tmp_path: Path) -> None:
    store = MessageStore(tmp_path / "messages.jsonl")
    log_handle = io.StringIO()

    app._record_geohash_event(
        {
            "id": "evt-2",
            "pubkey": "cd" * 32,
            "created_at": 1_719_188_401,
            "kind": 20000,
            "tags": [["g", "u4pruy"], ["n", "bubbles"], ["nonce", "1", "12"], ["t", "teleport"]],
            "content": "hello geo",
        },
        message_store=store,
        log_handle=log_handle,
    )

    rows = store.read_all()
    assert rows[0]["message_kind"] == "geohash_message"
    assert rows[0]["pow_difficulty"] is not None
    assert rows[0]["pow_difficulty"] >= 0
    assert rows[0]["teleported"] is True
    assert "hello geo" in log_handle.getvalue()


def test_run_geo_send_passes_pow_difficulty_to_daemon(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    fake_client = FakeDaemonClient()
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: fake_client)

    exit_code = app.run_geo_send(
        argparse.Namespace(geohash="#U4PrUy", message="hello geo", pow=12, teleport=True)
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert fake_client.calls == [
        {
            "command": "geo_send",
            "geohash": "u4pruy",
            "content": "hello geo",
            "pow_difficulty": 12,
            "teleported": True,
        }
    ]
    assert "queued geohash message" in captured.out


def test_run_geo_inbox_displays_pow_and_teleport_metadata(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    store = MessageStore(runtime.inbox_path)
    timestamp = datetime(2026, 6, 24, 0, 9, 44, tzinfo=timezone.utc)
    store.append(
        direction="inbound",
        sender="bubbles#aa11",
        content="geo hello",
        timestamp=timestamp,
        peer_id="a" * 64,
        message_id="geo-1",
        scope="public",
        conversation_id="u4pruy",
        message_kind="geohash_message",
        pow_difficulty=10,
        teleported=True,
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_geo_inbox(argparse.Namespace(geohash="#u4pruy", json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[10b]" in captured.out
    assert "[teleport]" in captured.out


def test_run_geo_who_lists_recent_participants_and_excludes_self(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    state_store = app.StateStore(runtime.state_path)
    state = state_store.load()
    state.nostr_device_seed_hex = "11" * 32
    state_store.save(state)

    self_pubkey = geochat.derive_nostr_identity(bytes.fromhex(state.nostr_device_seed_hex), "u4pruy").public_key_hex
    store = MessageStore(runtime.inbox_path)
    store.append(
        direction="inbound",
        sender="bubbles#931e",
        content="",
        timestamp=datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc),
        peer_id="22" * 32,
        message_id="geo-presence-1",
        scope="public",
        conversation_id="u4pruy",
        message_kind="geohash_presence",
    )
    store.append(
        direction="inbound",
        sender="bubbles#931e",
        content="hello geo",
        timestamp=datetime(2026, 6, 24, 12, 1, 0, tzinfo=timezone.utc),
        peer_id="22" * 32,
        message_id="geo-msg-1",
        scope="public",
        conversation_id="u4pruy",
        message_kind="geohash_message",
        teleported=True,
    )
    store.append(
        direction="inbound",
        sender="clanker#1234",
        content="self echo",
        timestamp=datetime(2026, 6, 24, 12, 1, 30, tzinfo=timezone.utc),
        peer_id=self_pubkey,
        message_id="geo-self-1",
        scope="public",
        conversation_id="u4pruy",
        message_kind="geohash_message",
    )
    store.append(
        direction="inbound",
        sender="oldie#0001",
        content="",
        timestamp=datetime(2026, 6, 24, 11, 40, 0, tzinfo=timezone.utc),
        peer_id="33" * 32,
        message_id="geo-old-1",
        scope="public",
        conversation_id="u4pruy",
        message_kind="geohash_presence",
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(
        app,
        "datetime",
        type(
            "FrozenDateTime",
            (),
            {
                "now": staticmethod(lambda tz=None: datetime(2026, 6, 24, 12, 4, 0, tzinfo=tz or timezone.utc)),
                "fromtimestamp": staticmethod(datetime.fromtimestamp),
                "fromisoformat": staticmethod(datetime.fromisoformat),
            },
        ),
    )

    exit_code = app.run_geo_who(argparse.Namespace(geohash="#u4pruy", json=False))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "bubbles#931e" in captured.out
    assert "teleport" in captured.out
    assert "clanker#1234" not in captured.out
    assert "oldie#0001" not in captured.out


def test_run_geo_watch_list_prints_configured_geohashes(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    state_store = app.StateStore(runtime.state_path)
    state = state_store.load()
    state.watched_geohashes = ["u4pruy", "u4xj"]
    state_store.save(state)
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_geo_watch_list(argparse.Namespace(json=False))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "#u4pruy" in captured.out
    assert "#u4xj" in captured.out


def test_run_geo_channel_set_persists_metadata(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_geo_channel_set(
        argparse.Namespace(
            geohash="#u4pruy",
            name="Home",
            note="quiet after 9pm",
            bookmark=True,
            unbookmark=False,
            select=True,
            unselect=False,
            teleport=True,
            no_teleport=False,
        )
    )

    state = app.StateStore(runtime.state_path).load()
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "saved channel metadata" in captured.out
    assert state.geohash_channels["u4pruy"]["name"] == "Home"
    assert state.geohash_channels["u4pruy"]["bookmarked"] is True
    assert state.geohash_channels["u4pruy"]["selected"] is True
    assert state.geohash_channels["u4pruy"]["teleport"] is True


def test_run_geo_channel_show_reports_activity_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    state = app.StateStore(runtime.state_path).load()
    state.geohash_channels = {"u4pruy": {"name": "Home", "bookmarked": True}}
    app.StateStore(runtime.state_path).save(state)
    store = MessageStore(runtime.inbox_path)
    store.append(
        direction="inbound",
        sender="bubbles#1234",
        content="hello",
        timestamp=datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc),
        peer_id="22" * 32,
        message_id="geo-msg-show-1",
        scope="public",
        conversation_id="u4pruy",
        message_kind="geohash_message",
        teleported=True,
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_geo_channel_show(argparse.Namespace(geohash="#u4pruy", json=False))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Home" in captured.out
    assert "message_count" in captured.out
    assert "teleport_count" in captured.out


def test_run_geo_participant_block_creates_social_overlay_from_geohash_history(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    MessageStore(runtime.inbox_path).append(
        direction="inbound",
        sender="bubbles#1234",
        content="hello",
        timestamp=datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc),
        peer_id="22" * 32,
        message_id="geo-msg-block-1",
        scope="public",
        conversation_id="u4pruy",
        message_kind="geohash_message",
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_geo_participant_block(argparse.Namespace(geohash="#u4pruy", target="@bubbles#1234"))

    captured = capsys.readouterr()
    row = app._social_store(runtime).get("22" * 32)
    assert exit_code == 0
    assert "blocked" in captured.out
    assert row is not None
    assert row["blocked"] is True


def test_run_geo_dm_send_uses_daemon_command_and_participant_pubkey(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    app._social_store(runtime).upsert(
        peer_id="22" * 32,
        nickname="bubbles#1234",
        nostr_pubkey_hex="22" * 32,
    )
    fake_client = FakeDaemonClient()
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: fake_client)

    exit_code = app.run_geo_dm_send(argparse.Namespace(geohash="#u4pruy", target="@bubbles#1234", message="hello geo dm"))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert fake_client.calls == [
        {
            "command": "geo_dm_send",
            "geohash": "u4pruy",
            "peer_pubkey": "22" * 32,
            "content": "hello geo dm",
        }
    ]
    assert "queued geo dm" in captured.out


def test_run_geo_dm_history_filters_one_geohash_conversation(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    app._social_store(runtime).upsert(
        peer_id="22" * 32,
        nickname="bubbles#1234",
        nostr_pubkey_hex="22" * 32,
    )
    store = MessageStore(runtime.inbox_path)
    store.append(
        direction="inbound",
        sender="bubbles#1234",
        content="hello geo dm",
        timestamp=datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc),
        peer_id="22" * 32,
        message_id="geo-dm-1",
        scope="geo_dm",
        conversation_id=f"u4pruy:{'22' * 32}",
        message_kind="private_message",
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_geo_dm_history(argparse.Namespace(geohash="#u4pruy", target="@bubbles#1234", json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "hello geo dm" in captured.out


def test_run_geo_dm_history_uses_nostr_identity_for_social_nickname(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    app._social_store(runtime).upsert(
        peer_id="33" * 8,
        nickname="anon4708",
        nostr_pubkey_hex="44" * 32,
    )
    store = MessageStore(runtime.inbox_path)
    store.append(
        direction="outbound",
        sender="clanker",
        content="geo dm sent by nickname",
        timestamp=datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc),
        peer_id="44" * 32,
        message_id="geo-dm-nostr-1",
        scope="geo_dm",
        conversation_id=f"u4pruy:{'44' * 32}",
        message_kind="private_message",
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_geo_dm_history(
        argparse.Namespace(geohash="#u4pruy", target="@anon4708", json=False, tail=None)
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "geo dm sent by nickname" in captured.out


def test_run_geo_notes_list_renders_fetched_notes(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    state = app.StateStore(runtime.state_path).load()
    state.nostr_device_seed_hex = "11" * 32
    app.StateStore(runtime.state_path).save(state)
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    
    class FakeNote:
        def __init__(self) -> None:
            self.event_id = "note-1"
            self.pubkey = "22" * 32
            self.geohash = "u4pruydq"
            self.content = "watch the back door"
            self.created_at = 1_719_230_400
            self.nickname = "bubbles"

        @property
        def display_name(self) -> str:
            return "bubbles#2222"

    async def fake_fetch_location_notes(**_kwargs):
        return [FakeNote()]

    monkeypatch.setattr(app, "fetch_location_notes", fake_fetch_location_notes)

    exit_code = app.run_geo_notes_list(argparse.Namespace(geohash="#u4pruydq", json=False, limit=5))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "watch the back door" in captured.out
    assert "bubbles#2222" in captured.out


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


def test_run_dm_history_formats_private_file_records(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    peer = Identity.generate()
    PeerStore(runtime.peers_path).upsert(
        peer_id=peer.peer_id,
        nickname="bubbles",
        noise_public_key=peer.noise_public_key,
        signing_public_key=peer.signing_public_key,
        addresses={"B1"},
        last_seen=datetime(2026, 6, 24, 0, 9, 45, tzinfo=timezone.utc),
    )
    store = MessageStore(runtime.inbox_path)
    store.append(
        direction="inbound",
        sender="bubbles",
        content=str(runtime.incoming_files_dir / "note.txt"),
        timestamp=datetime(2026, 6, 24, 0, 10, 1, tzinfo=timezone.utc),
        peer_id=peer.peer_id,
        message_id="file-1",
        scope="dm",
        conversation_id=peer.peer_id,
        message_kind="private_file",
        attachment_name="note.txt",
        attachment_mime="text/plain",
        attachment_size_bytes=12,
    )
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: FakeDaemonClient())

    exit_code = app.run_dm_history(argparse.Namespace(target="@bubbles", json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[file]" in captured.out
    assert "note.txt" in captured.out


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
    fake_client = FakeDaemonClient()

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: fake_client)

    exit_code = app.run_dm_send(argparse.Namespace(target="@bubbles", message="hello from cli"))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert fake_client.calls == [
        {
            "command": "dm_send",
            "peer_id": identity.peer_id,
            "content": "hello from cli",
        }
    ]
    assert "dm-1" in captured.out
    assert "via" in captured.out


def test_run_dm_send_file_uses_daemon_command_and_resolves_nickname(tmp_path: Path, monkeypatch, capsys) -> None:
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
    fake_client = FakeDaemonClient()

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: fake_client)

    exit_code = app.run_dm_send_file(argparse.Namespace(target="@bubbles", path="/tmp/photo.jpg"))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert fake_client.calls == [
        {
            "command": "dm_send_file",
            "peer_id": identity.peer_id,
            "path": "/tmp/photo.jpg",
        }
    ]
    assert "file-1" in captured.out


def test_run_social_verify_marks_linked_peer_verified(tmp_path: Path, monkeypatch, capsys) -> None:
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

    exit_code = app.run_social_verify(argparse.Namespace(target="@bubbles", payload=None))

    captured = capsys.readouterr()
    row = app._social_store(runtime).get(identity.peer_id)
    assert exit_code == 0
    assert "verified" in captured.out
    assert row is not None
    assert row["verified_at"] is not None


def test_run_topic_join_persists_local_topic_metadata(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)

    exit_code = app.run_topic_join(argparse.Namespace(channel="btc", password="secret", retain=True))

    captured = capsys.readouterr()
    state = app.StateStore(runtime.state_path).load()
    assert exit_code == 0
    assert "joined #btc" in captured.out
    assert state.topic_channels["#btc"]["joined"] is True
    assert state.topic_channels["#btc"]["password_set"] is True
    assert state.topic_channels["#btc"]["retention"] is True


def test_run_send_uses_daemon_command(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    fake_client = FakeDaemonClient()

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: fake_client)

    exit_code = app.run_send(argparse.Namespace(message="hello #mesh"))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert fake_client.calls == [
        {
            "command": "public_send",
            "content": "hello #mesh",
        }
    ]
    assert "queued public message" in captured.out


def test_run_send_file_uses_daemon_command(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    fake_client = FakeDaemonClient()

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: fake_client)

    exit_code = app.run_send_file(argparse.Namespace(path="/tmp/photo.jpg"))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert fake_client.calls == [
        {
            "command": "public_send_file",
            "path": "/tmp/photo.jpg",
        }
    ]
    assert "queued public file" in captured.out


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
    monkeypatch.setattr(app, "_wait_for_daemon_ready", lambda *_: True)

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
    fake_client = FakeDaemonClient()
    fake_client.runtime_status_payload = {
        "connected_links": 1,
        "known_peers": 2,
        "peripheral_advertising": True,
        "peripheral_subscribers": 3,
        "peripheral_addresses": ["peripheral:B2", "peripheral:C1"],
        "peripheral_recent_events": [
            {"event": "subscribe", "address": "peripheral:B2"},
            {"event": "write_decoded", "address": "peripheral:B2", "bytes": 23, "total_bytes": 87},
        ],
    }

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "pid_is_running", lambda _pid: True)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: fake_client)

    exit_code = app.run_daemon_status(argparse.Namespace())

    captured = capsys.readouterr()
    assert exit_code == 0
    assert fake_client.calls == [{"command": "runtime_status"}]
    assert "connected_links: 1" in captured.out
    assert "known_peers:     2" in captured.out
    assert "peripheral_adv:  True" in captured.out
    assert "peripheral_subs: 3" in captured.out
    assert "peripheral_addrs: peripheral:B2, peripheral:C1" in captured.out
    assert "peripheral_recent: subscribe peripheral:B2" in captured.out
    assert "peripheral_recent: write_decoded peripheral:B2 bytes=23 total_bytes=87" in captured.out


def test_run_daemon_status_reports_stale_pid_file(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = RuntimePaths(tmp_path)
    runtime.root.mkdir(parents=True, exist_ok=True)
    runtime.daemon_pid_path.write_text("43210\n", encoding="utf-8")

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "pid_is_running", lambda _pid: False)

    exit_code = app.run_daemon_status(argparse.Namespace())

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "process 43210 is not running" in captured.out


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
    fake_client = FakeDaemonClient()

    monkeypatch.setattr(app, "RuntimePaths", lambda: runtime)
    monkeypatch.setattr(app, "DaemonClient", lambda _runtime: fake_client)

    exit_code = app.run_dm_history(argparse.Namespace(target="@bubbles", json=False, tail=None))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "first unread" in captured.out
    assert "already read" in captured.out
    assert fake_client.calls == [
        {
            "command": "dm_mark_read",
            "peer_id": identity.peer_id,
            "message_ids": ["dm-1"],
        }
    ]


def test_topic_list_json_does_not_expose_password(tmp_path, monkeypatch, capsys):
    import json
    runtime = RuntimePaths(tmp_path)
    store = app.StateStore(runtime.state_path)
    state = store.load()
    state.topic_channels = {'#friends': {'password': 'do-not-print', 'password_set': True}}
    store.save(state)
    monkeypatch.setattr(app, 'StateStore', lambda: store)
    assert app.run_topic_list(argparse.Namespace(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{'channel': '#friends', 'password_set': True}]
