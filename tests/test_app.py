from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from bitchat_cli import app
from bitchat_cli.crypto import Identity
from bitchat_cli.state import PeerStore, RuntimePaths


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
