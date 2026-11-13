"""Check when the history command asks the daemon to resend a read receipt."""
import argparse
import contextlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from bitchat_cli import app
from bitchat_cli.state import MessageStore, RuntimePaths


with TemporaryDirectory() as directory:
    runtime = RuntimePaths(Path(directory))
    client = Mock()
    stamp = datetime.now(timezone.utc)
    store = MessageStore(runtime.inbox_path)
    record = dict(peer_id="fixture", conversation_id="fixture", scope="dm", message_id="one", sender="fixture")
    with (
        patch.object(app, "RuntimePaths", return_value=runtime),
        patch.object(app, "DaemonClient", return_value=client),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        store.append(**record, direction="inbound", content="message", timestamp=stamp, message_kind="private_message")
        full = argparse.Namespace(target="fixture", json=False, tail=None)
        # Both readers observe the same snapshot before the fake daemon records a result.
        assert app.run_dm_history(full) == 0
        assert app.run_dm_history(full) == 0
        assert client.mark_dm_read.call_count == 2
        for call in client.mark_dm_read.call_args_list:
            assert call.kwargs["message_ids"] == ["one"]
        client.reset_mock()

        store.append(**record, direction="outbound", content="read_receipt",
                     timestamp=stamp - timedelta(seconds=1), message_kind="receipt")
        assert app.run_dm_history(full) == 0
        client.mark_dm_read.assert_not_called()
        assert app.run_dm_history(argparse.Namespace(target="fixture", json=False, tail=1)) == 0
        client.mark_dm_read.assert_called_once_with(peer_id="fixture", message_ids=["one"])
print("A stale view or an omitted prior receipt can queue the same read receipt again.")
