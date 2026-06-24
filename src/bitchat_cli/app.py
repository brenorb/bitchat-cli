from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .constants import CHARACTERISTIC_UUID, SERVICE_UUID
from .state import MessageStore, RuntimePaths, StateStore, pid_is_running
from .transport import BLEChatTransport


def _clear_screen() -> None:
    print("\033[2J\033[H", end="", flush=True)


async def _open_stdin_reader() -> tuple[asyncio.StreamReader, asyncio.Transport]:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    return reader, transport


def doctor_payload(store: StateStore) -> dict[str, object]:
    state = store.load()
    paths = RuntimePaths()
    daemon_running = False
    daemon_pid = None
    if paths.daemon_pid_path.exists():
        try:
            daemon_pid = int(paths.daemon_pid_path.read_text().strip())
        except ValueError:
            daemon_pid = None
        if daemon_pid is not None:
            daemon_running = pid_is_running(daemon_pid)
    return {
        "nickname": state.nickname,
        "peer_id": state.identity.peer_id,
        "service_uuid": SERVICE_UUID,
        "characteristic_uuid": CHARACTERISTIC_UUID,
        "state_path": str(store.path),
        "daemon": {
            "running": daemon_running,
            "pid": daemon_pid,
            "log_path": str(paths.daemon_log_path),
            "inbox_path": str(paths.inbox_path),
        },
        "scope": {
            "public_chat": True,
            "private_noise_chat": False,
            "mesh_relay": False,
            "ble_peripheral": False,
            "nostr_geochat": False,
            "fragment_reassembly": False,
        },
    }


def run_doctor(args: argparse.Namespace) -> int:
    store = StateStore()
    payload = doctor_payload(store)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("bitchat-cli doctor")
    print(f"nickname:           {payload['nickname']}")
    print(f"peer_id:            {payload['peer_id']}")
    print(f"service_uuid:       {payload['service_uuid']}")
    print(f"characteristic_uuid:{payload['characteristic_uuid']}")
    print(f"state_path:         {payload['state_path']}")
    print(f"daemon_running:     {payload['daemon']['running']}")
    if payload["daemon"]["pid"] is not None:
        print(f"daemon_pid:         {payload['daemon']['pid']}")
    print(f"daemon_log:         {payload['daemon']['log_path']}")
    print(f"inbox_path:         {payload['daemon']['inbox_path']}")
    print("scope:")
    for key, value in payload["scope"].items():
        print(f"  {key}: {value}")
    return 0


def _build_transport(
    *,
    nickname: str,
    identity,
    scan_interval: float,
    system_log,
    message_log,
) -> BLEChatTransport:
    return BLEChatTransport(
        identity=identity,
        nickname=nickname,
        system_log=system_log,
        message_log=message_log,
        scan_interval=scan_interval,
    )


async def run_chat(args: argparse.Namespace) -> int:
    store = StateStore()
    message_store = MessageStore()
    state = store.load()
    if args.nickname:
        state.nickname = args.nickname
        store.save(state)

    def system_log(message: str) -> None:
        print(f"[system] {message}", flush=True)

    def message_log(sender: str, content: str, timestamp: datetime, peer_id: str | None) -> None:
        ts = timestamp.astimezone(timezone.utc).strftime("%H:%M:%S")
        message_store.append(
            direction="inbound",
            sender=sender,
            content=content,
            timestamp=timestamp,
            peer_id=peer_id,
        )
        print(f"\n[{ts}] <{sender}> {content}", flush=True)

    transport = _build_transport(
        identity=state.identity,
        nickname=state.nickname,
        system_log=system_log,
        message_log=message_log,
        scan_interval=args.scan_interval,
    )
    await transport.start()
    reader, stdin_transport = await _open_stdin_reader()

    print(f"welcome to bitchat-cli; nickname={state.nickname} peer_id={state.identity.peer_id}")
    print("type /help for commands")
    try:
        while True:
            print("> ", end="", flush=True)
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if text.startswith("/"):
                command, _, rest = text.partition(" ")
                if command == "/help":
                    print("/help")
                    print("/name <nickname>")
                    print("/who")
                    print("/clear")
                    print("/quit")
                    continue
                if command == "/name":
                    new_name = rest.strip()
                    if not new_name:
                        print("usage: /name <nickname>")
                        continue
                    state.nickname = new_name
                    store.save(state)
                    await transport.set_nickname(new_name)
                    print(f"nickname updated to {new_name}")
                    continue
                if command == "/who":
                    peers = transport.list_peers()
                    if not peers:
                        print("no known peers yet")
                        continue
                    for peer in peers:
                        addrs = ", ".join(sorted(peer.addresses)) or "-"
                        print(f"{peer.nickname:16} {peer.peer_id}  {addrs}")
                    continue
                if command == "/clear":
                    _clear_screen()
                    continue
                if command == "/quit":
                    break
                print(f"unknown command: {command}")
                continue

            await transport.send_public_message(text)
            now_dt = datetime.now(timezone.utc)
            now = now_dt.strftime("%H:%M:%S")
            message_store.append(
                direction="outbound",
                sender=state.nickname,
                content=text,
                timestamp=now_dt,
                peer_id=state.identity.peer_id,
            )
            print(f"[{now}] <{state.nickname}> {text}")
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        stdin_transport.close()
        await transport.stop()
    return 0


def run_selftest(_args: argparse.Namespace) -> int:
    from .crypto import Identity, peer_id_from_noise_public_key
    from .protocol import Announcement, Packet, make_announce_packet, make_public_message_packet

    identity = Identity.generate()

    announcement = Announcement(
        nickname="alice",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        direct_neighbors=[bytes.fromhex("0011223344556677")],
    )
    decoded_announcement = Announcement.decode(announcement.encode())
    assert decoded_announcement.nickname == "alice"
    assert decoded_announcement.noise_public_key == identity.noise_public_key
    assert decoded_announcement.signing_public_key == identity.signing_public_key

    packet = make_public_message_packet(identity, "hello world", timestamp_ms=1_700_000_000_000)
    decoded_packet = Packet.decode(packet.encode(padding=False))
    assert decoded_packet.payload == b"hello world"
    assert decoded_packet.verify_signature(identity.signing_public_key)

    relayed = Packet(
        packet_type=decoded_packet.packet_type,
        sender_id=decoded_packet.sender_id,
        recipient_id=decoded_packet.recipient_id,
        timestamp_ms=decoded_packet.timestamp_ms,
        payload=decoded_packet.payload,
        signature=decoded_packet.signature,
        ttl=3,
        version=decoded_packet.version,
    )
    assert relayed.verify_signature(identity.signing_public_key)

    announce_packet = make_announce_packet(identity, "alice", timestamp_ms=1_700_000_000_000)
    decoded_announce_packet = Packet.decode(announce_packet.encode(padding=False))
    decoded_announce_payload = Announcement.decode(decoded_announce_packet.payload)
    assert decoded_announce_packet.verify_signature(decoded_announce_payload.signing_public_key)
    assert peer_id_from_noise_public_key(decoded_announce_payload.noise_public_key) == identity.peer_id

    print("selftest ok")
    return 0


async def run_daemon_foreground(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    runtime.ensure()
    store = StateStore(runtime.state_path)
    message_store = MessageStore(runtime.inbox_path)
    state = store.load()
    if args.nickname:
        state.nickname = args.nickname
        store.save(state)

    log_handle = runtime.daemon_log_path.open("a", encoding="utf-8")

    def daemon_log(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat()} [system] {message}"
        print(line, file=log_handle, flush=True)

    def daemon_message_log(sender: str, content: str, timestamp: datetime, peer_id: str | None) -> None:
        message_store.append(
            direction="inbound",
            sender=sender,
            content=content,
            timestamp=timestamp,
            peer_id=peer_id,
        )
        line = f"{timestamp.astimezone(timezone.utc).isoformat()} <{sender}> {content}"
        print(line, file=log_handle, flush=True)

    runtime.daemon_pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    transport = _build_transport(
        identity=state.identity,
        nickname=state.nickname,
        system_log=daemon_log,
        message_log=daemon_message_log,
        scan_interval=args.scan_interval,
    )

    stop_event = asyncio.Event()

    def _request_stop(_signum, _frame) -> None:
        stop_event.set()

    old_sigint = signal.signal(signal.SIGINT, _request_stop)
    old_sigterm = signal.signal(signal.SIGTERM, _request_stop)
    try:
        daemon_log(f"Daemon starting with nickname={state.nickname} peer_id={state.identity.peer_id}.")
        await transport.start()
        await stop_event.wait()
        daemon_log("Daemon stopping.")
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)
        await transport.stop()
        try:
            if runtime.daemon_pid_path.exists():
                runtime.daemon_pid_path.unlink()
        finally:
            log_handle.close()
    return 0


def run_daemon_start(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    runtime.ensure()
    if runtime.daemon_pid_path.exists():
        try:
            existing_pid = int(runtime.daemon_pid_path.read_text().strip())
        except ValueError:
            existing_pid = None
        if existing_pid is not None and pid_is_running(existing_pid):
            print(f"daemon already running (pid {existing_pid})")
            return 0
        runtime.daemon_pid_path.unlink(missing_ok=True)

    command = [
        sys.executable,
        "-m",
        "bitchat_cli.cli",
        "daemon-run",
        "--scan-interval",
        str(args.scan_interval),
    ]
    if args.nickname:
        command.extend(["--nickname", args.nickname])

    log_handle = runtime.daemon_log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=str(Path.cwd()),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    log_handle.close()
    runtime.daemon_pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
    print(f"daemon started (pid {process.pid})")
    print(f"log: {runtime.daemon_log_path}")
    print(f"inbox: {runtime.inbox_path}")
    return 0


def run_daemon_stop(_args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    if not runtime.daemon_pid_path.exists():
        print("daemon not running")
        return 0
    try:
        pid = int(runtime.daemon_pid_path.read_text().strip())
    except ValueError:
        runtime.daemon_pid_path.unlink(missing_ok=True)
        print("daemon pid file was invalid; cleaned up")
        return 0

    if not pid_is_running(pid):
        runtime.daemon_pid_path.unlink(missing_ok=True)
        print("daemon was not running; cleaned up stale pid file")
        return 0

    os.kill(pid, signal.SIGTERM)
    print(f"sent SIGTERM to daemon (pid {pid})")
    return 0


def run_daemon_status(_args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    if not runtime.daemon_pid_path.exists():
        print("daemon not running")
        return 0
    try:
        pid = int(runtime.daemon_pid_path.read_text().strip())
    except ValueError:
        print("daemon pid file is invalid")
        return 1
    if pid_is_running(pid):
        print(f"daemon running (pid {pid})")
        print(f"log: {runtime.daemon_log_path}")
        print(f"inbox: {runtime.inbox_path}")
        return 0
    print(f"daemon pid file exists but process {pid} is not running")
    return 1


def run_inbox(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    records = MessageStore(runtime.inbox_path).read_all()
    records = [row for row in records if row.get("direction") == "inbound"]
    if args.tail is not None:
        records = records[-args.tail :]
    if args.json:
        print(json.dumps(records, indent=2))
        return 0
    if not records:
        print("no inbound messages recorded")
        return 0
    for row in records:
        timestamp = row["timestamp"]
        sender = row["sender"]
        content = row["content"]
        print(f"{timestamp} <{sender}> {content}")
    return 0
