from __future__ import annotations

import argparse
import contextlib
import io

import pytest

from bitchat_cli import app, cli


def _parser_paths(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    paths = [prefix]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in sorted(action.choices.items()):
                paths.extend(_parser_paths(subparser, prefix + (name,)))
    return paths


def test_every_parser_path_supports_help() -> None:
    parser = cli.build_parser()

    for path in _parser_paths(parser):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with pytest.raises(SystemExit) as raised:
                parser.parse_args([*path, "--help"])

        assert raised.value.code == 0, "help failed for " + " ".join(path or ("<root>",))
        assert output.getvalue()


def test_mesh_send_parser_routes_to_public_send_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["mesh", "send", "hello mesh"])

    assert args.handler is app.run_send
    assert args.message == "hello mesh"


def test_mesh_send_file_parser_routes_to_public_send_file_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["mesh", "send-file", "/tmp/photo.jpg"])

    assert args.handler is app.run_send_file
    assert args.path == "/tmp/photo.jpg"


def test_mesh_inbox_parser_routes_to_public_inbox_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["mesh", "inbox", "--tail", "5"])

    assert args.handler is app.run_inbox
    assert args.tail == 5


def test_dm_send_file_parser_routes_to_dm_send_file_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["dm", "send-file", "@bubbles", "/tmp/photo.jpg"])

    assert args.handler is app.run_dm_send_file
    assert args.target == "@bubbles"
    assert args.path == "/tmp/photo.jpg"


def test_geo_send_parser_routes_to_geo_send_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "send", "#u4pruy", "--pow", "12", "--teleport", "hello geo"])

    assert args.handler is app.run_geo_send
    assert args.geohash == "#u4pruy"
    assert args.pow == 12
    assert args.teleport is True
    assert args.message == "hello geo"


def test_geo_watch_add_parser_routes_to_geo_watch_add_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "watch", "add", "u4pruy"])

    assert args.handler is app.run_geo_watch_add
    assert args.geohash == "u4pruy"


def test_geo_who_parser_routes_to_geo_who_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "who", "#u4pruy", "--json"])

    assert args.handler is app.run_geo_who
    assert args.geohash == "#u4pruy"
    assert args.json is True


def test_geo_encode_parser_routes_to_geo_encode_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "encode", "--lat", "37.7", "--lon", "-122.4", "--all-levels"])

    assert args.handler is app.run_geo_encode
    assert args.all_levels is True


def test_geo_lookup_parser_routes_to_geo_lookup_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "lookup", "Sao Paulo", "--limit", "3"])

    assert args.handler is app.run_geo_lookup
    assert args.query == "Sao Paulo"
    assert args.limit == 3


def test_geo_channel_set_parser_routes_to_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "channel", "set", "#u4pruy", "--name", "Home", "--bookmark"])

    assert args.handler is app.run_geo_channel_set
    assert args.geohash == "#u4pruy"
    assert args.name == "Home"
    assert args.bookmark is True


def test_geo_notes_list_parser_routes_to_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "notes", "list", "#u4pruydqq", "--limit", "5", "--json"])

    assert args.handler is app.run_geo_notes_list
    assert args.geohash == "#u4pruydqq"
    assert args.limit == 5
    assert args.json is True


def test_geo_dm_send_parser_routes_to_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "dm", "send", "#u4pruy", "@bubbles", "hello geo dm"])

    assert args.handler is app.run_geo_dm_send
    assert args.geohash == "#u4pruy"
    assert args.target == "@bubbles"
    assert args.message == "hello geo dm"


def test_geo_participant_block_parser_routes_to_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "participant", "block", "#u4pruy", "deadbeef" * 8])

    assert args.handler is app.run_geo_participant_block
    assert args.target == "deadbeef" * 8


def test_social_link_parser_routes_to_social_link_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["social", "link", "@bubbles", "npub1test"])

    assert args.handler is app.run_social_link
    assert args.target == "@bubbles"
    assert args.npub == "npub1test"


def test_social_verify_parser_routes_to_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["social", "verify", "@bubbles", "--payload", "bitchat://verify?v=1"])

    assert args.handler is app.run_social_verify
    assert args.target == "@bubbles"
    assert args.payload == "bitchat://verify?v=1"


def test_network_proxy_set_parser_routes_to_proxy_set_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["network", "proxy", "set", "tor", "--policy", "require"])

    assert args.handler is app.run_network_proxy_set
    assert args.target == "tor"
    assert args.policy == "require"


def test_topic_join_parser_routes_to_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["topic", "join", "btc", "--password", "secret", "--retain"])

    assert args.handler is app.run_topic_join
    assert args.channel == "btc"
    assert args.password == "secret"
    assert args.retain is True
