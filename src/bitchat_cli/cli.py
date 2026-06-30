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
    run_geo_dm_history,
    run_geo_dm_send,
    run_geo_encode,
    run_geo_send,
    run_geo_channel_list,
    run_geo_channel_set,
    run_geo_channel_show,
    run_geo_notes_add,
    run_geo_notes_list,
    run_geo_participant_block,
    run_geo_participant_show,
    run_geo_participant_unblock,
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
    run_social_unverify,
    run_social_unblock,
    run_social_unfavorite,
    run_social_unlink,
    run_social_verify,
    run_network_proxy_disable,
    run_network_proxy_set,
    run_network_proxy_status,
    run_network_proxy_test,
    run_selftest,
    run_topic_join,
    run_topic_leave,
    run_topic_list,
    run_topic_password_set,
    run_topic_retain,
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

    geo_channel = geo_subparsers.add_parser("channel", help="Manage saved geohash channel metadata")
    geo_channel_subparsers = geo_channel.add_subparsers(dest="geo_channel_command", required=True)

    geo_channel_set = geo_channel_subparsers.add_parser("set", help="Save local metadata for a geohash")
    geo_channel_set.add_argument("geohash", help="Target geohash, with or without leading #")
    geo_channel_set.add_argument("--name", help="Friendly display name")
    geo_channel_set.add_argument("--note", help="Private local note for this geohash")
    geo_channel_set.add_argument("--bookmark", action="store_true", help="Bookmark the geohash")
    geo_channel_set.add_argument("--unbookmark", action="store_true", help="Remove bookmark state")
    geo_channel_set.add_argument("--select", action="store_true", help="Mark this as the selected geohash context")
    geo_channel_set.add_argument("--unselect", action="store_true", help="Clear selected state")
    geo_channel_set.add_argument("--teleport", action="store_true", help="Mark the saved context as teleported")
    geo_channel_set.add_argument("--no-teleport", action="store_true", help="Clear saved teleport state")
    geo_channel_set.set_defaults(handler=run_geo_channel_set)

    geo_channel_show = geo_channel_subparsers.add_parser("show", help="Show saved metadata and recent activity for one geohash")
    geo_channel_show.add_argument("geohash", help="Target geohash, with or without leading #")
    geo_channel_show.add_argument("--json", action="store_true", help="Output JSON")
    geo_channel_show.set_defaults(handler=run_geo_channel_show)

    geo_channel_list = geo_channel_subparsers.add_parser("list", help="List saved geohash channel metadata")
    geo_channel_list.add_argument("--bookmarked", action="store_true", help="Show only bookmarked geohashes")
    geo_channel_list.add_argument("--json", action="store_true", help="Output JSON")
    geo_channel_list.set_defaults(handler=run_geo_channel_list)

    geo_notes = geo_subparsers.add_parser("notes", help="Publish and inspect persistent location notes")
    geo_notes_subparsers = geo_notes.add_subparsers(dest="geo_notes_command", required=True)

    geo_notes_add = geo_notes_subparsers.add_parser("add", help="Publish a location note for one building geohash")
    geo_notes_add.add_argument("geohash", help="Building-level geohash, with or without leading #")
    geo_notes_add.add_argument("message", help="Note body")
    geo_notes_add.set_defaults(handler=run_geo_notes_add)

    geo_notes_list = geo_notes_subparsers.add_parser("list", help="Fetch persistent location notes for one building geohash")
    geo_notes_list.add_argument("geohash", help="Building-level geohash, with or without leading #")
    geo_notes_list.add_argument("--json", action="store_true", help="Output JSON")
    geo_notes_list.add_argument("--limit", type=int, default=50, help="Maximum notes to return")
    geo_notes_list.set_defaults(handler=run_geo_notes_list)

    geo_dm = geo_subparsers.add_parser("dm", help="Send and inspect geohash-scoped Nostr DMs")
    geo_dm_subparsers = geo_dm.add_subparsers(dest="geo_dm_command", required=True)

    geo_dm_send = geo_dm_subparsers.add_parser("send", help="Send a geohash-scoped Nostr DM")
    geo_dm_send.add_argument("geohash", help="Source geohash, with or without leading #")
    geo_dm_send.add_argument("target", help="Participant pubkey, npub, or @nickname")
    geo_dm_send.add_argument("message", help="Message body")
    geo_dm_send.set_defaults(handler=run_geo_dm_send)

    geo_dm_history = geo_dm_subparsers.add_parser("history", help="Show geohash-scoped DM history for one participant")
    geo_dm_history.add_argument("geohash", help="Source geohash, with or without leading #")
    geo_dm_history.add_argument("target", help="Participant pubkey, npub, or @nickname")
    geo_dm_history.add_argument("--json", action="store_true", help="Output JSON")
    geo_dm_history.add_argument("--tail", type=int, help="Show only the last N rows")
    geo_dm_history.set_defaults(handler=run_geo_dm_history)

    geo_participant = geo_subparsers.add_parser("participant", help="Inspect and moderate geohash participants")
    geo_participant_subparsers = geo_participant.add_subparsers(dest="geo_participant_command", required=True)

    geo_participant_show = geo_participant_subparsers.add_parser("show", help="Show saved trust state for one participant")
    geo_participant_show.add_argument("geohash", help="Observed geohash, with or without leading #")
    geo_participant_show.add_argument("target", help="Participant pubkey, npub, or @nickname")
    geo_participant_show.set_defaults(handler=run_geo_participant_show)

    geo_participant_block = geo_participant_subparsers.add_parser("block", help="Block a geohash participant by Nostr identity")
    geo_participant_block.add_argument("geohash", help="Observed geohash, with or without leading #")
    geo_participant_block.add_argument("target", help="Participant pubkey, npub, or @nickname")
    geo_participant_block.set_defaults(handler=run_geo_participant_block)

    geo_participant_unblock = geo_participant_subparsers.add_parser("unblock", help="Unblock a geohash participant by Nostr identity")
    geo_participant_unblock.add_argument("geohash", help="Observed geohash, with or without leading #")
    geo_participant_unblock.add_argument("target", help="Participant pubkey, npub, or @nickname")
    geo_participant_unblock.set_defaults(handler=run_geo_participant_unblock)

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

    social_verify = social_subparsers.add_parser("verify", help="Mark a peer as verified")
    social_verify.add_argument("target", help="Peer ID, pubkey, or @nickname")
    social_verify.add_argument("--payload", help="Verification QR payload to validate before marking verified")
    social_verify.set_defaults(handler=run_social_verify)

    social_unverify = social_subparsers.add_parser("unverify", help="Clear verified state for a peer")
    social_unverify.add_argument("target", help="Peer ID, pubkey, or @nickname")
    social_unverify.set_defaults(handler=run_social_unverify)

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

    topic = subparsers.add_parser("topic", help="Manage Android-style named topic channels locally")
    topic_subparsers = topic.add_subparsers(dest="topic_command", required=True)

    topic_join = topic_subparsers.add_parser("join", help="Join or create a local named topic channel")
    topic_join.add_argument("channel", help="Channel name, with or without leading #")
    topic_join.add_argument("--password", help="Optional password marker for the topic")
    topic_join.add_argument("--retain", action="store_true", help="Enable local retention for the topic")
    topic_join.set_defaults(handler=run_topic_join)

    topic_leave = topic_subparsers.add_parser("leave", help="Leave a local named topic channel")
    topic_leave.add_argument("channel", help="Channel name, with or without leading #")
    topic_leave.set_defaults(handler=run_topic_leave)

    topic_list = topic_subparsers.add_parser("list", help="List locally configured topic channels")
    topic_list.add_argument("--json", action="store_true", help="Output JSON")
    topic_list.set_defaults(handler=run_topic_list)

    topic_password = topic_subparsers.add_parser("password", help="Set or clear a stored topic password")
    topic_password.add_argument("channel", help="Channel name, with or without leading #")
    topic_password.add_argument("password", nargs="?", help="Password to store; omit to clear")
    topic_password.set_defaults(handler=run_topic_password_set)

    topic_retain = topic_subparsers.add_parser("retain", help="Enable or disable local retention for a topic")
    topic_retain.add_argument("channel", help="Channel name, with or without leading #")
    topic_retain.add_argument("enabled", choices=["on", "off"])
    topic_retain.set_defaults(handler=run_topic_retain)

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
