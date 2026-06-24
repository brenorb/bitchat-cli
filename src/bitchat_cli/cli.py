from __future__ import annotations

import argparse
import asyncio

from .app import (
    run_chat,
    run_daemon_foreground,
    run_daemon_start,
    run_daemon_status,
    run_daemon_stop,
    run_dm_history,
    run_dm_inbox,
    run_dm_send,
    run_dm_send_file,
    run_doctor,
    run_geo_inbox,
    run_geo_levels,
    run_geo_lookup,
    run_geo_decode,
    run_geo_encode,
    run_geo_send,
    run_geo_who,
    run_geo_watch_add,
    run_geo_watch_list,
    run_geo_watch_remove,
    run_inbox,
    run_peers,
    run_send,
    run_send_file,
    run_social_block,
    run_social_favorite,
    run_social_fingerprint,
    run_social_link,
    run_social_list,
    run_social_unblock,
    run_social_unfavorite,
    run_social_unlink,
    run_network_proxy_disable,
    run_network_proxy_set,
    run_network_proxy_status,
    run_network_proxy_test,
    run_selftest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bitchat", description="BitChat public BLE chat CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Show local configuration and current scope")
    doctor.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    doctor.set_defaults(handler=run_doctor)

    chat = subparsers.add_parser("chat", help="Run the interactive public chat client")
    chat.add_argument("--nickname", help="Override the persisted nickname for this session")
    chat.add_argument("--scan-interval", type=float, default=5.0, help="BLE scan interval in seconds")
    chat.set_defaults(async_handler=run_chat)

    daemon = subparsers.add_parser("daemon", help="Manage the background listener")
    daemon_subparsers = daemon.add_subparsers(dest="daemon_command", required=True)

    daemon_start = daemon_subparsers.add_parser("start", help="Start the background listener")
    daemon_start.add_argument("--nickname", help="Override the persisted nickname for the daemon")
    daemon_start.add_argument("--scan-interval", type=float, default=5.0, help="BLE scan interval in seconds")
    daemon_start.set_defaults(handler=run_daemon_start)

    daemon_stop = daemon_subparsers.add_parser("stop", help="Stop the background listener")
    daemon_stop.set_defaults(handler=run_daemon_stop)

    daemon_status = daemon_subparsers.add_parser("status", help="Show background listener status")
    daemon_status.set_defaults(handler=run_daemon_status)

    daemon_run = subparsers.add_parser("daemon-run", help=argparse.SUPPRESS)
    daemon_run.add_argument("--nickname")
    daemon_run.add_argument("--scan-interval", type=float, default=5.0)
    daemon_run.set_defaults(async_handler=run_daemon_foreground)

    inbox = subparsers.add_parser("inbox", help="Show recorded inbound messages")
    inbox.add_argument("--json", action="store_true", help="Output JSON")
    inbox.add_argument("--tail", type=int, help="Show only the last N inbound messages")
    inbox.set_defaults(handler=run_inbox)

    mesh = subparsers.add_parser("mesh", help="Work with the public #mesh conversation")
    mesh_subparsers = mesh.add_subparsers(dest="mesh_command", required=True)

    mesh_inbox = mesh_subparsers.add_parser("inbox", help="Show recorded #mesh messages")
    mesh_inbox.add_argument("--json", action="store_true", help="Output JSON")
    mesh_inbox.add_argument("--tail", type=int, help="Show only the last N inbound messages")
    mesh_inbox.set_defaults(handler=run_inbox)

    mesh_send = mesh_subparsers.add_parser("send", help="Send one public message to #mesh through the background daemon")
    mesh_send.add_argument("message", help="Message body")
    mesh_send.set_defaults(handler=run_send)

    mesh_send_file = mesh_subparsers.add_parser("send-file", help="Send one public file to #mesh through the background daemon")
    mesh_send_file.add_argument("path", help="Path to a local file")
    mesh_send_file.set_defaults(handler=run_send_file)

    send = subparsers.add_parser("send", help="Send one public message through the background daemon")
    send.add_argument("message", help="Message body")
    send.set_defaults(handler=run_send)

    send_file = subparsers.add_parser("send-file", help="Send one public file through the background daemon")
    send_file.add_argument("path", help="Path to a local file")
    send_file.set_defaults(handler=run_send_file)

    geo = subparsers.add_parser("geo", help="Work with location geohash channels over Nostr")
    geo_subparsers = geo.add_subparsers(dest="geo_command", required=True)

    geo_send = geo_subparsers.add_parser("send", help="Send one public message to a geohash channel")
    geo_send.add_argument("geohash", help="Target geohash, with or without leading #")
    geo_send.add_argument(
        "--pow",
        type=int,
        default=0,
        help="Optional NIP-13 proof-of-work difficulty in leading zero bits",
    )
    geo_send.add_argument(
        "--teleport",
        action="store_true",
        help="Mark the geohash message as teleported, matching the official app tag",
    )
    geo_send.add_argument("--force", action="store_true", help="Acknowledge high-precision geohash disclosure risk")
    geo_send.add_argument("message", help="Message body")
    geo_send.set_defaults(handler=run_geo_send)

    geo_inbox = geo_subparsers.add_parser("inbox", help="Show recorded inbound messages for one geohash channel")
    geo_inbox.add_argument("geohash", help="Target geohash, with or without leading #")
    geo_inbox.add_argument("--json", action="store_true", help="Output JSON")
    geo_inbox.add_argument("--tail", type=int, help="Show only the last N inbound messages")
    geo_inbox.set_defaults(handler=run_geo_inbox)

    geo_who = geo_subparsers.add_parser("who", help="Show recently seen participants for one geohash channel")
    geo_who.add_argument("geohash", help="Target geohash, with or without leading #")
    geo_who.add_argument("--json", action="store_true", help="Output JSON")
    geo_who.set_defaults(handler=run_geo_who)

    geo_levels = geo_subparsers.add_parser("levels", help="Show upstream-compatible geohash hierarchy prefixes")
    geo_levels.add_argument("geohash", help="Target geohash, with or without leading #")
    geo_levels.add_argument("--json", action="store_true", help="Output JSON")
    geo_levels.set_defaults(handler=run_geo_levels)

    geo_encode = geo_subparsers.add_parser("encode", help="Derive one or all geohashes from coordinates")
    geo_encode.add_argument("--lat", type=float, required=True, help="Latitude in decimal degrees")
    geo_encode.add_argument("--lon", type=float, required=True, help="Longitude in decimal degrees")
    geo_encode.add_argument("--level", choices=["region", "province", "city", "neighborhood", "block", "building"])
    geo_encode.add_argument("--all-levels", action="store_true", help="Print the full upstream-compatible level set")
    geo_encode.add_argument("--json", action="store_true", help="Output JSON")
    geo_encode.set_defaults(handler=run_geo_encode)

    geo_decode = geo_subparsers.add_parser("decode", help="Show the center and bounds for a geohash")
    geo_decode.add_argument("geohash", help="Target geohash, with or without leading #")
    geo_decode.add_argument("--json", action="store_true", help="Output JSON")
    geo_decode.set_defaults(handler=run_geo_decode)

    geo_lookup = geo_subparsers.add_parser("lookup", help="Resolve a place name to coordinates and geohashes")
    geo_lookup.add_argument("query", help="Free-form place query")
    geo_lookup.add_argument("--limit", type=int, default=5, help="Maximum number of matches to return")
    geo_lookup.add_argument("--json", action="store_true", help="Output JSON")
    geo_lookup.set_defaults(handler=run_geo_lookup)

    geo_watch = geo_subparsers.add_parser("watch", help="Manage background geohash subscriptions")
    geo_watch_subparsers = geo_watch.add_subparsers(dest="geo_watch_command", required=True)

    geo_watch_add = geo_watch_subparsers.add_parser("add", help="Subscribe the daemon to a geohash channel")
    geo_watch_add.add_argument("geohash", help="Target geohash, with or without leading #")
    geo_watch_add.add_argument("--force", action="store_true", help="Acknowledge high-precision geohash disclosure risk")
    geo_watch_add.set_defaults(handler=run_geo_watch_add)

    geo_watch_remove = geo_watch_subparsers.add_parser("remove", help="Unsubscribe the daemon from a geohash channel")
    geo_watch_remove.add_argument("geohash", help="Target geohash, with or without leading #")
    geo_watch_remove.set_defaults(handler=run_geo_watch_remove)

    geo_watch_list = geo_watch_subparsers.add_parser("list", help="Show configured geohash subscriptions")
    geo_watch_list.add_argument("--json", action="store_true", help="Output JSON")
    geo_watch_list.set_defaults(handler=run_geo_watch_list)

    peers = subparsers.add_parser("peers", help="Show known peers recorded by the CLI")
    peers.add_argument("--json", action="store_true", help="Output JSON")
    peers.set_defaults(handler=run_peers)

    social = subparsers.add_parser("social", help="Manage social trust and Nostr fallback mappings")
    social_subparsers = social.add_subparsers(dest="social_command", required=True)

    social_list = social_subparsers.add_parser("list", help="Show linked social records")
    social_list.add_argument("--json", action="store_true", help="Output JSON")
    social_list.set_defaults(handler=run_social_list)

    social_link = social_subparsers.add_parser("link", help="Link a BitChat peer to a Nostr npub")
    social_link.add_argument("target", help="Peer ID or @nickname")
    social_link.add_argument("npub", help="Nostr npub to use for fallback")
    social_link.set_defaults(handler=run_social_link)

    social_unlink = social_subparsers.add_parser("unlink", help="Remove a Nostr link for a peer")
    social_unlink.add_argument("target", help="Peer ID or @nickname")
    social_unlink.set_defaults(handler=run_social_unlink)

    social_favorite = social_subparsers.add_parser("favorite", help="Authorize Nostr fallback for a linked peer")
    social_favorite.add_argument("target", help="Peer ID or @nickname")
    social_favorite.set_defaults(handler=run_social_favorite)

    social_unfavorite = social_subparsers.add_parser("unfavorite", help="Disable Nostr fallback for a linked peer")
    social_unfavorite.add_argument("target", help="Peer ID or @nickname")
    social_unfavorite.set_defaults(handler=run_social_unfavorite)

    social_block = social_subparsers.add_parser("block", help="Block a linked peer from Nostr fallback")
    social_block.add_argument("target", help="Peer ID or @nickname")
    social_block.set_defaults(handler=run_social_block)

    social_unblock = social_subparsers.add_parser("unblock", help="Unblock a linked peer from Nostr fallback")
    social_unblock.add_argument("target", help="Peer ID or @nickname")
    social_unblock.set_defaults(handler=run_social_unblock)

    social_fingerprint = social_subparsers.add_parser("fingerprint", help="Show local trust material for a peer")
    social_fingerprint.add_argument("target", help="Peer ID or @nickname")
    social_fingerprint.set_defaults(handler=run_social_fingerprint)

    network = subparsers.add_parser("network", help="Manage privacy policy and network proxy behavior")
    network_subparsers = network.add_subparsers(dest="network_command", required=True)

    network_proxy = network_subparsers.add_parser("proxy", help="Manage outbound network proxy settings")
    network_proxy_subparsers = network_proxy.add_subparsers(dest="network_proxy_command", required=True)

    network_proxy_status = network_proxy_subparsers.add_parser("status", help="Show the current proxy policy")
    network_proxy_status.add_argument("--json", action="store_true", help="Output JSON")
    network_proxy_status.set_defaults(handler=run_network_proxy_status)

    network_proxy_set = network_proxy_subparsers.add_parser("set", help="Set proxy mode to tor, system, or a proxy URL")
    network_proxy_set.add_argument("target", help="tor, system, or a proxy URL such as socks5h://127.0.0.1:9050")
    network_proxy_set.add_argument("--policy", choices=["prefer", "require"], default="prefer")
    network_proxy_set.set_defaults(handler=run_network_proxy_set)

    network_proxy_disable = network_proxy_subparsers.add_parser("disable", help="Disable proxy usage")
    network_proxy_disable.set_defaults(handler=run_network_proxy_disable)

    network_proxy_test = network_proxy_subparsers.add_parser("test", help="Test local proxy reachability")
    network_proxy_test.add_argument("--json", action="store_true", help="Output JSON")
    network_proxy_test.set_defaults(handler=run_network_proxy_test)

    dm = subparsers.add_parser("dm", help="Inspect direct-message history recorded by the CLI")
    dm_subparsers = dm.add_subparsers(dest="dm_command", required=True)

    dm_inbox = dm_subparsers.add_parser("inbox", help="List recorded DM conversations")
    dm_inbox.add_argument("--json", action="store_true", help="Output JSON")
    dm_inbox.add_argument("--tail", type=int, help="Show only the last N conversations")
    dm_inbox.set_defaults(handler=run_dm_inbox)

    dm_history = dm_subparsers.add_parser("history", help="Show DM history for one peer")
    dm_history.add_argument("target", help="Peer ID or @nickname")
    dm_history.add_argument("--json", action="store_true", help="Output JSON")
    dm_history.add_argument("--tail", type=int, help="Show only the last N rows")
    dm_history.set_defaults(handler=run_dm_history)

    dm_send = dm_subparsers.add_parser("send", help="Send one DM through the background daemon")
    dm_send.add_argument("target", help="Peer ID or @nickname")
    dm_send.add_argument("message", help="Message body")
    dm_send.set_defaults(handler=run_dm_send)

    dm_send_file = dm_subparsers.add_parser("send-file", help="Send one file over DM through the background daemon")
    dm_send_file.add_argument("target", help="Peer ID or @nickname")
    dm_send_file.add_argument("path", help="Path to a local file")
    dm_send_file.set_defaults(handler=run_dm_send_file)

    selftest = subparsers.add_parser("selftest", help="Run offline protocol checks")
    selftest.set_defaults(handler=run_selftest)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "async_handler"):
        return asyncio.run(args.async_handler(args))
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
