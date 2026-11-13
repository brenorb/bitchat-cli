"""Exercise CLI routing and channel selection with a temporary runtime."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from bitchat_cli import app
from bitchat_cli.state import RuntimePaths, StateStore


with TemporaryDirectory() as directory:
    runtime = RuntimePaths(Path(directory))
    client = Mock()
    with patch.object(app, "RuntimePaths", return_value=runtime), patch.object(app, "DaemonClient", return_value=client):
        send = argparse.Namespace(geohash="u4pruyd", message="local fixture", force=False, pow=0, teleport=False)
        assert app.run_geo_send(send) == 1
        client.send_geo.assert_not_called()
        send.geohash = "u4pruy"
        assert app.run_geo_send(send) == 0
        send.geohash = "u4pruyd"
        send.force = True
        assert app.run_geo_send(send) == 0
        assert client.send_geo.call_count == 2

        for geohash in ("u4pruy", "u4pruz"):
            options = argparse.Namespace(
                geohash=geohash, name=None, note=None, bookmark=False, unbookmark=False,
                teleport=False, no_teleport=False, select=True, unselect=False,
            )
            assert app.run_geo_channel_set(options) == 0
        channels = StateStore(runtime.state_path).load().geohash_channels
        assert [name for name, entry in channels.items() if entry["selected"]] == ["u4pruz"]
