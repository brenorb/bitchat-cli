"""Replay matching and mismatched signed proofs with ephemeral local keys."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bitchat_cli import app
from bitchat_cli.crypto import Identity
from bitchat_cli.social import SocialStore
from bitchat_cli.state import PeerStore, RuntimePaths
from bitchat_cli.verification import build_verification_payload

with TemporaryDirectory() as temporary:
    runtime = RuntimePaths(Path(temporary))
    selected = Identity.generate()
    other = Identity.generate()
    PeerStore(runtime.peers_path).upsert(
        peer_id=selected.peer_id, nickname="fixture", noise_public_key=selected.noise_public_key,
        signing_public_key=selected.signing_public_key, addresses=set(),
        last_seen=datetime.now(timezone.utc),
    )
    social = SocialStore(runtime.social_path)
    with patch.object(app, "RuntimePaths", return_value=runtime):
        proof = build_verification_payload(identity=other, nickname="other", npub=None).to_url()
        assert app.run_social_verify(argparse.Namespace(target=selected.peer_id, payload=proof)) == 1
        assert social.get(selected.peer_id) is None
        proof = build_verification_payload(identity=selected, nickname="fixture", npub=None).to_url()
        assert app.run_social_verify(argparse.Namespace(target=selected.peer_id, payload=proof)) == 0
        assert social.get(selected.peer_id)["verified_at"]
        assert app.run_social_unverify(argparse.Namespace(target=selected.peer_id)) == 0
        assert social.get(selected.peer_id)["verified_at"] is None
