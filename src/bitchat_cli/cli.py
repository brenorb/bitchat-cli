from __future__ import annotations

import argparse
import asyncio

from .app import (
    run_chat,
    run_daemon_foreground,
    run_daemon_start,
    run_daemon_status,
    run_daemon_stop,
    run_doctor,
    run_inbox,
    run_peers,
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

    peers = subparsers.add_parser("peers", help="Show known peers recorded by the CLI")
    peers.add_argument("--json", action="store_true", help="Output JSON")
    peers.set_defaults(handler=run_peers)

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
