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
