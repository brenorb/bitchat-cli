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
    run_doctor,
    run_inbox,
    run_peers,
    run_send,
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

    send = subparsers.add_parser("send", help="Send one public message through the background daemon")
    send.add_argument("message", help="Message body")
    send.set_defaults(handler=run_send)

    peers = subparsers.add_parser("peers", help="Show known peers recorded by the CLI")
    peers.add_argument("--json", action="store_true", help="Output JSON")
    peers.set_defaults(handler=run_peers)

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
