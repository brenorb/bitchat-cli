from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from bitchat_cli import app
from bitchat_cli.crypto import Identity
from bitchat_cli.state import MessageStore, PeerStore, RuntimePaths


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
