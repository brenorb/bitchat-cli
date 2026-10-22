import pytest

from bitchat_cli import cli


@pytest.mark.parametrize("command,handler", [("join", "run_topic_join"), ("password", "run_topic_password_set")])
def test_password_file_is_noninteractive(command, handler, monkeypatch, tmp_path):
    path = tmp_path / "password"
    path.write_bytes(b"private topic password\r\n")
    seen = []
    monkeypatch.setattr(cli, handler, lambda args: seen.append(args.password) or 0)
    monkeypatch.setattr(cli.sys.stdin, "read", lambda *_: pytest.fail("must not read stdin"))
    assert cli.main(["topic", command, "friends", "--password-file", str(path)]) == 0
    assert seen == ["private topic password"]


@pytest.mark.parametrize("contents", [b"", b"\n", b"PRIVATE-FIRST\nPRIVATE-SECOND\n", b"PRIVATE\xff"])
def test_invalid_password_files_fail_without_echo_or_dispatch(contents, tmp_path, monkeypatch, capsys):
    path = tmp_path / "password"
    path.write_bytes(contents)
    monkeypatch.setattr(cli, "run_topic_join", lambda _: pytest.fail("invalid password reached handler"))
    assert cli.main(["topic", "join", "friends", "--password-file", str(path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "--password-file" in output.err
    assert "PRIVATE" not in output.err
    assert "Traceback" not in output.err


def test_missing_password_file_is_actionable(tmp_path, capsys):
    assert cli.main(["topic", "join", "friends", "--password-file", str(tmp_path / "missing")]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "--password-file" in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("argv", [
    ["join", "friends", "--password-stdin", "--password-file", "missing"],
    ["join", "friends", "--password", "PRIVATE", "--password-file", "missing"],
    ["password", "friends", "PRIVATE", "--password-file", "missing"],
    ["password", "friends", "--password-stdin", "--password-file", "missing"],
])
def test_password_sources_cannot_be_combined(argv, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["topic", *argv])
    assert error.value.code == 2
    assert "PRIVATE" not in capsys.readouterr().err


def test_password_file_workflow_redacts_output_and_preserves_state_on_failure(tmp_path, monkeypatch, capsys):
    from bitchat_cli.state import RuntimePaths, StateStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "password"
    path.write_text("PRIVATE-TOPIC-PASSWORD\n")
    assert cli.main(["topic", "join", "friends", "--password-file", str(path)]) == 0
    assert cli.main(["topic", "list", "--json"]) == 0
    assert "PRIVATE" not in capsys.readouterr().out
    runtime = RuntimePaths()
    before = runtime.state_path.read_bytes()
    path.write_text("")
    assert cli.main(["topic", "password", "friends", "--password-file", str(path)]) == 1
    assert runtime.state_path.read_bytes() == before
    assert StateStore(runtime.state_path).load().topic_channels["#friends"]["password"] == "PRIVATE-TOPIC-PASSWORD"
