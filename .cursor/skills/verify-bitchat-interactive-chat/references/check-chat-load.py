"""Check the real interactive startup path with a temporary invalid state file."""
import argparse
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bitchat_cli import app
from bitchat_cli.state import RuntimePaths

with TemporaryDirectory() as temporary:
    runtime = RuntimePaths(Path(temporary))
    runtime.state_path.write_text("invalid JSON")
    with patch.object(app, "RuntimePaths", return_value=runtime):
        try:
            asyncio.run(app.run_chat(argparse.Namespace(nickname=None, scan_interval=1)))
        except ValueError:
            pass
        else:
            raise AssertionError("The invalid state file was accepted")
    assert runtime.radio_pid_path.read_text().strip() == str(os.getpid())
print("Interactive startup failed before the cleanup block and left the lease file.")
