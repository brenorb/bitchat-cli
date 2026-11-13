"""Compare how the two source versions handle a malformed optional key."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from bitchat_cli.state import StateStore

parser = argparse.ArgumentParser()
parser.add_argument("--legacy", action="store_true")
args = parser.parse_args()
with TemporaryDirectory() as temporary:
    path = Path(temporary) / "state.json"
    store = StateStore(path)
    original = store.load()
    invalid = original.to_json()
    invalid["nostr_private_key"] = "invalid"
    path.write_text(json.dumps(invalid))
    previous_bytes = path.read_bytes()
    if args.legacy:
        loaded = store.load()
        assert loaded.nostr_private_key_hex != original.nostr_private_key_hex
        assert len(loaded.nostr_private_key_hex) == 64
        assert loaded.identity.peer_id == original.identity.peer_id
        print("The release returned a replacement Nostr identity for a malformed key.")
    else:
        try:
            store.load()
        except ValueError as error:
            assert "invalid state file" in str(error)
        else:
            raise AssertionError("The malformed identity was accepted")
        print("The current loader rejected the malformed key.")
    assert path.read_bytes() == previous_bytes
