"""Check independent local flags and delete semantics with temporary data."""
from pathlib import Path
from tempfile import TemporaryDirectory

from bitchat_cli.social import SocialStore

with TemporaryDirectory() as directory:
    path = Path(directory) / "social.json"
    store = SocialStore(path)
    store.upsert(peer_id="peer", nickname="fixture", favorite=True, verified=True)
    row = store.upsert(peer_id="peer", nickname="", blocked=True)
    assert row["nickname"] == "fixture" and row["favorite"] and row["verified_at"]
    assert row["blocked"]
    row = store.upsert(peer_id="peer", nickname="", verified=False)
    assert row["favorite"] and row["blocked"] and row["verified_at"] is None
    store.delete("peer")
    assert SocialStore(path).get("peer") is None
print("Updating one social flag preserved the others.")
