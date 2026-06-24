from __future__ import annotations

from bitchat_cli import app, cli


def test_mesh_send_parser_routes_to_public_send_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["mesh", "send", "hello mesh"])

    assert args.handler is app.run_send
    assert args.message == "hello mesh"


def test_mesh_inbox_parser_routes_to_public_inbox_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["mesh", "inbox", "--tail", "5"])

    assert args.handler is app.run_inbox
    assert args.tail == 5


def test_geo_send_parser_routes_to_geo_send_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "send", "#u4pruy", "--pow", "12", "hello geo"])

    assert args.handler is app.run_geo_send
    assert args.geohash == "#u4pruy"
    assert args.pow == 12
    assert args.message == "hello geo"


def test_geo_watch_add_parser_routes_to_geo_watch_add_handler() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["geo", "watch", "add", "u4pruy"])

    assert args.handler is app.run_geo_watch_add
    assert args.geohash == "u4pruy"
