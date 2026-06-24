from __future__ import annotations

from bitchat_cli import app, cli


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
