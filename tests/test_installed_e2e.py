"""Real installed command/daemon/socket tests with isolated local transports.

BITCHAT_E2E_BIN may point to a fresh wheel installation's `bitchat` executable.
The default uses the current environment's installed console entry point.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import pytest
from bitchat_cli.protocol import FilePacket

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='daemon uses UNIX sockets')
SUPPORT = Path(__file__).parent / 'support' / 'installed_transport'

@pytest.fixture
def installed():
    with tempfile.TemporaryDirectory(prefix='bc-e2e-', dir='/tmp') as directory:
        root = Path(directory)
        env = dict(os.environ, XDG_CONFIG_HOME=directory, PYTHONPATH=str(SUPPORT), BITCHAT_E2E_CONTROLLED='1')
        binary = os.environ.get('BITCHAT_E2E_BIN', str(Path(sys.executable).parent / 'bitchat'))
        def invoke(*args, input=None):
            return subprocess.run([binary, *args], env=env, cwd=directory, input=input, text=True, capture_output=True, timeout=12)
        yield invoke, root / 'bitchat', env, binary
        # Stop only this test's independently created daemon.
        invoke('daemon', 'stop')


def socket_command(path, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(str(path))
        client.sendall(json.dumps(command).encode() + b'\n')
        response = b''
        while not response.endswith(b'\n'):
            chunk = client.recv(4096)
            if not chunk: break
            response += chunk
        return json.loads(response)


def wait_ready(invoke, root):
    deadline = time.monotonic() + 5
    last = None
    while time.monotonic() < deadline:
        if (root / 'daemon.sock').exists():
            last = invoke('daemon', 'status')
            if last.returncode == 0 and 'known_peers:     1' in last.stdout:
                return
        time.sleep(.05)
    pytest.fail(f'daemon never became ready: {last}; {(root / "daemon.log").read_text()}')


def test_installed_daemon_lifecycle_socket_and_signed_local_messages(installed):
    invoke, root, _env, _binary = installed
    initial = json.loads(invoke('doctor', '--json').stdout)
    identity = json.loads((root / 'state.json').read_text())
    assert (root / 'state.json').stat().st_mode & 0o777 == 0o600
    started = invoke('daemon', 'start', '--nickname', 'installed-e2e')
    assert started.returncode == 0, started.stderr
    wait_ready(invoke, root)
    status = socket_command(root / 'daemon.sock', {'command': 'runtime_status'})
    assert status['ok'] is True
    assert status['runtime']['known_peers'] == 1
    assert status['runtime']['nostr_relays'] == []
    repeated = invoke('daemon', 'start')
    assert repeated.returncode == 0, repeated.stderr
    assert 'already running' in repeated.stdout
    sent = invoke('mesh', 'send', '-', input='controlled local message\n')
    assert sent.returncode == 0, sent.stderr
    assert sent.stderr == ''
    file = root / 'sample.txt'
    file.write_text('controlled local file')
    assert invoke('mesh', 'send-file', str(file)).returncode == 0
    rows = [json.loads(line) for line in (root / 'messages.jsonl').read_text().splitlines()]
    assert any(row['content'] == 'controlled local message' and row['direction'] == 'outbound' for row in rows)
    inbox = invoke('inbox', '--json')
    assert inbox.returncode == 0
    assert any(row['content'] == 'local fixture reply' for row in json.loads(inbox.stdout))
    wires = [json.loads(line) for line in (root / 'controlled-wire.jsonl').read_text().splitlines()]
    assert len(wires) == 2 and all(row['signature_valid'] for row in wires)
    assert bytes.fromhex(wires[0]['payload_hex']) == b'controlled local message'
    attachment = FilePacket.decode(bytes.fromhex(wires[1]['payload_hex']))
    assert attachment.file_name == 'sample.txt'
    assert attachment.content == b'controlled local file'
    unknown = socket_command(root / 'daemon.sock', {'command': 'no-such-command'})
    assert unknown == {'ok': False, 'error': 'unknown daemon command'}
    stopped = invoke('daemon', 'stop')
    assert stopped.returncode == 0, stopped.stderr
    assert invoke('daemon', 'status').stdout.strip() == 'daemon not running'
    assert not any((root / name).exists() for name in ('daemon.pid', 'daemon.sock', 'radio.pid'))
    assert json.loads(invoke('doctor', '--json').stdout)['peer_id'] == initial['peer_id']
    assert json.loads((root / 'state.json').read_text())['signing_private_key'] == identity['signing_private_key']


def test_installed_secret_stdin_errors_and_closed_pipe(installed):
    invoke, root, env, binary = installed
    assert invoke('--help').returncode == 0
    assert not root.exists()
    secret = 'only-for-local-e2e'
    result = invoke('topic', 'password', '#local', '--password-stdin', input=secret + '\n')
    assert result.returncode == 0
    assert secret not in result.stdout + result.stderr
    rows = json.loads(invoke('topic', 'list', '--json').stdout)
    assert rows[0]['password_set'] is True and 'password' not in rows[0]
    assert json.loads((root / 'state.json').read_text())['topic_channels']['#local']['password'] == secret
    invalid = invoke('mesh', 'send', '-', input=' \n')
    assert invalid.returncode == 1 and invalid.stdout == '' and 'must not be empty' in invalid.stderr
    syntax = invoke('daemon', 'start', '--scan-interval', 'nan')
    assert syntax.returncode == 2 and syntax.stdout == ''
    reader, writer = os.pipe()
    os.close(reader)
    try:
        closed = subprocess.run([binary, '--help'], env=env, stdout=writer, stderr=subprocess.PIPE, text=True, timeout=5)
    finally:
        os.close(writer)
    assert closed.returncode == 0 and closed.stderr == ''


def test_installed_daemon_reports_startup_failure(installed):
    invoke, root, env, _binary = installed
    env['BITCHAT_E2E_FAIL_START'] = '1'
    failed = invoke('daemon', 'start')
    assert failed.returncode == 1
    assert failed.stdout == ''
    assert 'failed to start' in failed.stderr
    assert not any((root / name).exists() for name in ('daemon.pid', 'daemon.sock', 'radio.pid'))
