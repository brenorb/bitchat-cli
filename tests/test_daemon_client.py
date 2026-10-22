from __future__ import annotations

import socket

import pytest

from bitchat_cli.daemon_client import DaemonClient
from bitchat_cli.state import RuntimePaths


class FakeSocket:
    def __init__(self, response=b'{"ok": true}\n'):
        self.response = response
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def connect(self, path):
        pass

    def sendall(self, payload):
        pass

    def recv(self, size):
        if isinstance(self.response, Exception):
            raise self.response
        response, self.response = self.response, b''
        return response

    def close(self):
        self.closed = True


def client_with_socket(tmp_path, monkeypatch, response=b'{"ok": true}\n'):
    runtime = RuntimePaths(tmp_path)
    runtime.daemon_socket_path.touch()
    fake = FakeSocket(response)
    monkeypatch.setattr(socket, 'socket', lambda *_args: fake)
    return DaemonClient(runtime), fake


def test_daemon_commands_have_a_bounded_wait(tmp_path, monkeypatch):
    client, fake = client_with_socket(tmp_path, monkeypatch)
    assert client.send_command({'command': 'runtime_status'}) == {'ok': True}
    assert fake.timeout == 30.0
    assert fake.closed


def test_daemon_timeout_explains_recovery_and_closes_socket(tmp_path, monkeypatch):
    client, fake = client_with_socket(tmp_path, monkeypatch, TimeoutError('timed out'))
    with pytest.raises(RuntimeError, match='timed out.*daemon status'):
        client.send_command({'command': 'runtime_status'})
    assert fake.closed


@pytest.mark.parametrize('response', [b'not json\n', b'[]\n', b'\xff\n', b'{"ok":true}'])
def test_invalid_or_incomplete_daemon_response_is_actionable(tmp_path, monkeypatch, response):
    client, fake = client_with_socket(tmp_path, monkeypatch, response)
    with pytest.raises(RuntimeError, match='invalid.*daemon|incomplete.*daemon'):
        client.send_command({'command': 'runtime_status'})
    assert fake.closed
