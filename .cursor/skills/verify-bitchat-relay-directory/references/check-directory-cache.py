"""Check temporary relay-directory cache and stub only the CSV download."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bitchat_cli import geochat

valid = "Relay URL,Latitude,Longitude\nrelay.invalid,1,2\n"
with TemporaryDirectory() as directory:
    cache = Path(directory) / "relays.csv"
    cache.write_text("invalid csv")
    with patch.object(geochat, "_download_relay_csv", return_value=valid) as download:
        entries = geochat.load_relay_entries(cache)
        assert len(entries) == 1 and entries[0].host == "relay.invalid"
        assert cache.read_text() == valid
        assert geochat.load_relay_entries(cache) == entries
        download.assert_called_once()
    cache.unlink()
    with patch.object(geochat, "_download_relay_csv", side_effect=["invalid csv", valid]) as download:
        assert geochat.load_relay_entries(cache) == entries
        assert download.call_count == 2
print("A malformed cache or first download triggered one replacement download.")
