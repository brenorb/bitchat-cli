"""Replay two local command replies with a controlled socket."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from bitchat_cli import daemon_client
from bitchat_cli.state import RuntimePaths


def read_reply(reply: bytes, *, identified: bool) -> str:
    with TemporaryDirectory() as directory:
        runtime = RuntimePaths(Path(directory))
        runtime.daemon_socket_path.touch()
        socket = Mock()
        socket.recv.side_effect = (reply, b"")
        client = daemon_client.DaemonClient(runtime)
        with patch.object(daemon_client.socket, "socket", return_value=socket):
            try:
                if identified:
                    client.send_dm(peer_id="fixture-peer", content="hello")
                else:
                    client.runtime_status()
            except RuntimeError as error:
                return str(error)
            finally:
                socket.close.assert_called_once()
    return "accepted"


parser = argparse.ArgumentParser()
parser.add_argument("--expect-legacy", action="store_true")
options = parser.parse_args()
for reply, identified, expected_error in (
    (b'{"ok":true,"runtime":{}}', False, "incomplete response"),
    (b'{"ok":true}\n', True, "missing message_id"),
):
    outcome = read_reply(reply, identified=identified)
    assert ("accepted" if options.expect_legacy else expected_error) in outcome, outcome
    print(outcome)
