"""Run from the repository root with PYTHONPATH=src."""
import argparse
import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bitchat_cli import app
from bitchat_cli.state import RuntimePaths


def startup(*, cached: bool) -> tuple[int, str]:
    with TemporaryDirectory() as directory:
        runtime = RuntimePaths(Path(directory))
        if cached:
            runtime.daemon_pid_path.write_text("43210\n", encoding="utf-8")
        runtime.daemon_socket_path.touch()
        child = Mock(pid=43210)
        child.poll.return_value = None
        clock = SimpleNamespace(monotonic=iter((0.0, 0.0, 0.0, 11.0)).__next__, sleep=lambda _: None)
        client = Mock()
        client.runtime_status.return_value = {"ready": False}
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(app, "RuntimePaths", return_value=runtime),
            patch.object(app, "DaemonClient", return_value=client),
            patch.object(app, "pid_is_running", return_value=True),
            patch.object(app, "time", clock),
            patch.object(app.subprocess, "Popen", return_value=child),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = app.run_daemon_start(SimpleNamespace(nickname=None, scan_interval=5.0))
        return code, stdout.getvalue() + stderr.getvalue()


parser = argparse.ArgumentParser()
parser.add_argument("--expect-legacy", action="store_true")
options = parser.parse_args()
for cached in (False, True):
    status, output = startup(cached=cached)
    expected = 0 if options.expect_legacy else 1
    assert status == expected, (cached, status, output)
    if not options.expect_legacy:
        assert ("not ready" if cached else "failed to start") in output, output
    print(f"{'repeat' if cached else 'initial'} start with ready=false returned {status}")
