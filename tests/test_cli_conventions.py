from __future__ import annotations

import pytest

from bitchat_cli import cli


@pytest.mark.parametrize('argv', [[], ['geo'], ['geo', 'channel'], ['dm']])
def test_bare_commands_show_help_without_running_handlers(argv, capsys):
    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    assert 'usage:' in captured.out
    assert captured.err == ''


@pytest.mark.parametrize('argv', [['--help'], ['-h'], ['help'], ['help', 'geo', 'encode'], ['geo', 'encode', '--lat', 'invalid', '--help']])
def test_help_is_successful_and_precedes_validation(argv, capsys):
    assert cli.main(argv) == 0
    assert 'usage:' in capsys.readouterr().out


def test_help_has_examples_documentation_and_support(capsys):
    assert cli.main(['--help']) == 0
    output = capsys.readouterr().out
    assert 'Examples:' in output
    assert 'https://github.com/brenorb/bitchat-cli' in output
    assert '/issues' in output
    assert 'daemon-run' not in output


def test_version_does_not_load_state(capsys):
    assert cli.main(['--version']) == 0
    assert capsys.readouterr().out.startswith('bitchat ')


@pytest.mark.parametrize('argv', [
    ['inbox', '--ta', '3'],
    ['inbox', '--tail', '0'],
    ['inbox', '--tail', '-1'],
    ['geo', 'lookup', 'Berlin', '--limit', '0'],
    ['geo', 'send', 'u4pru', '--pow', '-1', 'hello'],
    ['chat', '--scan-interval', 'nan'],
    ['daemon', 'start', '--scan-interval', 'inf'],
    ['chat', '--scan-interval', '-1'],
    ['geo', 'channel', 'set', 'u4pru', '--bookmark', '--unbookmark'],
    ['geo', 'encode', '--lat', '1', '--lon', '2', '--level', 'city', '--all-levels'],
])
def test_invalid_arguments_fail_before_dispatch(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'error:' in captured.err


@pytest.mark.parametrize('argv', [['geo', 'decode', '!bad', '--json'], ['geo', 'levels', '!bad', '--json'], ['geo', 'send', 'u4pruydqq', 'hello']])
def test_expected_failures_keep_stdout_clean(argv, capsys):
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err.strip()


@pytest.mark.parametrize('failure', [PermissionError('state is not writable'), ValueError('invalid saved state'), RuntimeError('network unavailable')])
def test_operational_errors_are_concise(monkeypatch, capsys, failure):
    def fail(_args):
        raise failure
    monkeypatch.setattr(cli, 'run_selftest', fail)
    assert cli.main(['selftest']) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert str(failure) in captured.err
    assert 'Traceback' not in captured.err


def test_keyboard_interrupt_returns_conventional_status(monkeypatch, capsys):
    def fail(_args):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, 'run_selftest', fail)
    assert cli.main(['selftest']) == 130
    assert 'interrupted' in capsys.readouterr().err


def test_chat_requires_a_terminal_before_starting_radio(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: False)
    assert cli.main(['chat']) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'terminal' in captured.err
    assert 'daemon' in captured.err


def test_broken_pipe_exits_without_traceback(monkeypatch):
    def fail(_args):
        raise BrokenPipeError
    monkeypatch.setattr(cli, 'run_selftest', fail)
    assert cli.main(['selftest']) == 0


def test_password_can_come_from_stdin(monkeypatch):
    import io
    seen = {}
    monkeypatch.setattr(cli.sys, 'stdin', io.StringIO('secret password\n'))
    monkeypatch.setattr(cli, 'run_topic_join', lambda args: seen.update(password=args.password) or 0)
    assert cli.main(['topic', 'join', 'friends', '--password-stdin']) == 0
    assert seen['password'] == 'secret password'


@pytest.mark.parametrize('secret_input', ['', 'one\ntwo\n'])
def test_password_stdin_refuses_empty_or_multiline_secrets(monkeypatch, capsys, secret_input):
    import io
    monkeypatch.setattr(cli.sys, 'stdin', io.StringIO(secret_input))
    assert cli.main(['topic', 'password', 'friends', '--password-stdin']) == 1
    assert capsys.readouterr().out == ''


def test_password_stdin_does_not_prompt_a_terminal(monkeypatch, capsys):
    import io
    stream = io.StringIO('secret\n')
    stream.isatty = lambda: True
    monkeypatch.setattr(cli.sys, 'stdin', stream)
    assert cli.main(['topic', 'join', 'friends', '--password-stdin']) == 1
    assert 'pipe' in capsys.readouterr().err


def test_explicit_message_stdin_is_composable(monkeypatch):
    import io
    seen = {}
    monkeypatch.setattr(cli.sys, 'stdin', io.StringIO('hello from pipeline\n'))
    monkeypatch.setattr(cli, 'run_send', lambda args: seen.update(message=args.message) or 0)
    assert cli.main(['mesh', 'send', '-']) == 0
    assert seen['message'] == 'hello from pipeline'


def test_help_also_handles_closed_stdout(monkeypatch):
    def closed(_parser):
        raise BrokenPipeError
    monkeypatch.setattr(cli.CommandParser, 'print_help', closed)
    assert cli.main(['--help']) == 0


def test_help_rejects_unknown_nested_command(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(['help', 'geo', 'typo'])
    assert exc.value.code == 2
    assert 'typo' in capsys.readouterr().err


def test_installed_cli_contract_with_isolated_config(tmp_path):
    import json
    import os
    import subprocess
    import sys
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path))
    def invoke(*argv):
        return subprocess.run([sys.executable, '-m', 'bitchat_cli.cli', *argv], env=env, text=True, capture_output=True)
    for argv in [(), ('--version',), ('--help',), ('help', 'geo', 'encode')]:
        result = invoke(*argv)
        assert result.returncode == 0
        assert result.stdout
        assert result.stderr == ''
    assert not (tmp_path / 'bitchat').exists()
    result = invoke('geo', 'encode', '--lat', '37.7', '--lon', '-122.4', '--json')
    assert result.returncode == 0
    assert json.loads(result.stdout)['city']
    assert result.stderr == ''
    result = invoke('geo', 'decode', '!', '--json')
    assert result.returncode == 1
    assert result.stdout == ''
    assert 'invalid geohash' in result.stderr


def test_real_closed_pipe_exits_cleanly():
    import os
    import subprocess
    import sys
    reader, writer = os.pipe()
    os.close(reader)
    try:
        result = subprocess.run([sys.executable, '-m', 'bitchat_cli.cli', '--help'], stdout=writer, stderr=subprocess.PIPE, text=True)
    finally:
        os.close(writer)
    assert result.returncode == 0
    assert result.stderr == ''
