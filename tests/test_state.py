from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bitchat_cli.crypto import Identity
from bitchat_cli.state import ActiveSessionLease, MessageStore, PeerStore, RuntimePaths, StateStore, pid_is_running


def test_runtime_paths_are_stable(tmp_path: Path) -> None:
    paths = RuntimePaths(tmp_path)
    assert paths.state_path == tmp_path / "state.json"
    assert paths.daemon_pid_path == tmp_path / "daemon.pid"
    assert paths.daemon_log_path == tmp_path / "daemon.log"
    assert paths.inbox_path == tmp_path / "messages.jsonl"
    assert paths.peers_path == tmp_path / "peers.json"
    assert paths.daemon_socket_path == tmp_path / "daemon.sock"
    assert paths.georelays_cache_path == tmp_path / "georelays_cache.csv"
    assert paths.incoming_files_dir == tmp_path / "files" / "incoming"
    assert paths.incoming_images_dir == tmp_path / "images" / "incoming"


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


def test_message_store_skips_persisting_duplicate_message_ids(tmp_path: Path) -> None:
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

    rows = store.read_all()

    assert len(rows) == 1
    assert rows[0]["message_id"] == "sig:1234"


def test_message_store_reuses_existing_file_keys_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "messages.jsonl"
    first = MessageStore(path)
    ts = datetime(2026, 6, 24, 0, 9, 44, 932000, tzinfo=timezone.utc)
    first.append(
        direction="inbound",
        sender="bubbles",
        content="Ping - 1",
        timestamp=ts,
        peer_id="abcd",
        message_id="sig:abcd",
    )

    second = MessageStore(path)
    second.append(
        direction="inbound",
        sender="bubbles",
        content="Ping - 1",
        timestamp=ts,
        peer_id="abcd",
        message_id="sig:abcd",
    )

    rows = second.read_all()

    assert len(rows) == 1
    assert rows[0]["message_id"] == "sig:abcd"


def test_message_store_persists_scope_and_conversation_metadata(tmp_path: Path) -> None:
    store = MessageStore(tmp_path / "messages.jsonl")
    ts = datetime(2026, 6, 24, 1, 0, 0, tzinfo=timezone.utc)
    store.append(
        direction="inbound",
        sender="bubbles",
        content="secret hello",
        timestamp=ts,
        peer_id="abcd",
        message_id="dm-1",
        scope="dm",
        conversation_id="abcd",
        message_kind="private_message",
        target_peer_id="abcd",
        delivery_state="delivered",
        pow_difficulty=12,
        teleported=True,
        attachment_name="photo.jpg",
        attachment_mime="image/jpeg",
        attachment_size_bytes=12345,
    )

    rows = store.read_all()

    assert rows[0]["scope"] == "dm"
    assert rows[0]["conversation_id"] == "abcd"
    assert rows[0]["message_kind"] == "private_message"
    assert rows[0]["target_peer_id"] == "abcd"
    assert rows[0]["delivery_state"] == "delivered"
    assert rows[0]["pow_difficulty"] == 12
    assert rows[0]["teleported"] is True
    assert rows[0]["attachment_name"] == "photo.jpg"
    assert rows[0]["attachment_mime"] == "image/jpeg"
    assert rows[0]["attachment_size_bytes"] == 12345


def test_peer_store_upserts_replaces_addresses_on_newer_observation(tmp_path: Path) -> None:
    store = PeerStore(tmp_path / "peers.json")
    identity = Identity.generate()
    first_seen = datetime(2026, 6, 24, 0, 0, 0, tzinfo=timezone.utc)
    latest_seen = datetime(2026, 6, 24, 0, 5, 0, tzinfo=timezone.utc)

    store.upsert(
        peer_id=identity.peer_id,
        nickname="bubbles",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        addresses={"A1"},
        last_seen=first_seen,
    )
    store.upsert(
        peer_id=identity.peer_id,
        nickname="bubbles-renamed",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        addresses={"B2"},
        last_seen=latest_seen,
    )

    rows = store.read_all()

    assert len(rows) == 1
    assert rows[0]["peer_id"] == identity.peer_id
    assert rows[0]["nickname"] == "bubbles-renamed"
    assert rows[0]["noise_public_key"] == identity.noise_public_key.hex()
    assert rows[0]["signing_public_key"] == identity.signing_public_key.hex()
    assert rows[0]["addresses"] == ["B2"]
    assert rows[0]["last_seen"] == latest_seen.isoformat()


def test_peer_store_preserves_newer_addresses_when_older_observation_arrives(tmp_path: Path) -> None:
    store = PeerStore(tmp_path / "peers.json")
    identity = Identity.generate()
    latest_seen = datetime(2026, 6, 24, 0, 5, 0, tzinfo=timezone.utc)
    older_seen = datetime(2026, 6, 24, 0, 0, 0, tzinfo=timezone.utc)

    store.upsert(
        peer_id=identity.peer_id,
        nickname="fresh",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        addresses={"B2"},
        last_seen=latest_seen,
    )
    store.upsert(
        peer_id=identity.peer_id,
        nickname="stale",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        addresses={"A1"},
        last_seen=older_seen,
    )

    rows = store.read_all()

    assert rows[0]["nickname"] == "fresh"
    assert rows[0]["addresses"] == ["B2"]
    assert rows[0]["last_seen"] == latest_seen.isoformat()


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


def test_state_store_backfills_nostr_seed_and_geo_watches_for_legacy_state(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    identity = Identity.generate()
    path.write_text(
        "{\n"
        f'  "nickname": "legacy",\n'
        f'  "noise_private_key": "{identity.serialize()["noise_private_key"]}",\n'
        f'  "signing_private_key": "{identity.serialize()["signing_private_key"]}"\n'
        "}\n",
        encoding="utf-8",
    )

    store = StateStore(path)
    loaded = store.load()
    reloaded = store.load()

    assert loaded.nickname == "legacy"
    assert len(loaded.nostr_device_seed_hex) == 64
    assert len(loaded.nostr_private_key_hex) == 64
    assert loaded.nostr_relays != []
    assert loaded.network_proxy_mode == "disabled"
    assert loaded.network_proxy_policy == "prefer"
    assert loaded.watched_geohashes == []
    assert reloaded.nostr_device_seed_hex == loaded.nostr_device_seed_hex
    assert reloaded.nostr_private_key_hex == loaded.nostr_private_key_hex
