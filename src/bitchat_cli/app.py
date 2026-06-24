from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .constants import CHARACTERISTIC_UUID, SERVICE_UUID
from .peripheral import create_peripheral_endpoint, peripheral_supported
from .state import ActiveSessionLease, MessageStore, PeerStore, RuntimePaths, StateStore, pid_is_running
from .transport import BLEChatTransport

_PERIPHERAL_SUBSCRIBE_ANNOUNCE_DELAY_SECONDS = 0.4


def _clear_screen() -> None:
    print("\033[2J\033[H", end="", flush=True)


async def _open_stdin_reader() -> tuple[asyncio.StreamReader, asyncio.Transport]:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    return reader, transport


def _resolve_peer_target(transport: BLEChatTransport, raw_target: str):
    target = raw_target.strip()
    if target.startswith("@"):
        target = target[1:]
    peers = transport.list_peers()
    direct_match = [peer for peer in peers if peer.peer_id == target]
    if direct_match:
        return direct_match[0]
    nickname_match = [peer for peer in peers if peer.nickname == target]
    if len(nickname_match) == 1:
        return nickname_match[0]
    if len(nickname_match) > 1:
        raise ValueError(f"ambiguous nickname: {raw_target}")
    raise ValueError(f"unknown peer: {raw_target}")


def _resolve_peer_record(records: list[dict[str, object]], raw_target: str) -> dict[str, object]:
    target = raw_target.strip()
    if target.startswith("@"):
        target = target[1:]
    direct_match = [record for record in records if record.get("peer_id") == target]
    if direct_match:
        return direct_match[0]
    nickname_match = [record for record in records if record.get("nickname") == target]
    if len(nickname_match) == 1:
        return nickname_match[0]
    if len(nickname_match) > 1:
        raise ValueError(f"ambiguous nickname: {raw_target}")
    raise ValueError(f"unknown peer: {raw_target}")


def _read_dm_records(runtime: RuntimePaths) -> list[dict[str, object]]:
    rows = MessageStore(runtime.inbox_path).read_all(deduplicate=True)
    return [row for row in rows if row.get("scope") == "dm"]


def _send_daemon_command(runtime: RuntimePaths, payload: dict[str, object]) -> dict[str, object]:
    if not runtime.daemon_socket_path.exists():
        raise RuntimeError("daemon socket not found; start `bitchat daemon start` first")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(runtime.daemon_socket_path))
        client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        response = b""
        while not response.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk:
                break
            response += chunk
    except OSError as exc:
        raise RuntimeError(f"failed to talk to daemon: {exc}") from exc
    finally:
        client.close()
    if not response:
        raise RuntimeError("daemon returned no response")
    decoded = json.loads(response.decode("utf-8"))
    if not decoded.get("ok", False):
        raise RuntimeError(str(decoded.get("error") or "daemon command failed"))
    return decoded


def _pending_read_receipt_ids(rows: list[dict[str, object]], peer_id: str) -> list[str]:
    sent = {
        str(row.get("message_id"))
        for row in rows
        if row.get("message_kind") == "receipt"
        and row.get("direction") == "outbound"
        and row.get("peer_id") == peer_id
        and row.get("content") == "read_receipt"
    }
    pending: list[str] = []
    for row in rows:
        if row.get("message_kind") != "private_message":
            continue
        if row.get("direction") != "inbound":
            continue
        if row.get("peer_id") != peer_id:
            continue
        message_id = str(row.get("message_id") or "")
        if not message_id or message_id in sent:
            continue
        pending.append(message_id)
    return pending


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
            "socket_path": str(paths.daemon_socket_path),
            "inbox_path": str(paths.inbox_path),
            "peers_path": str(paths.peers_path),
        },
        "scope": {
            "public_chat": True,
            "private_noise_chat": True,
            "mesh_relay": True,
            "local_sync": True,
            "ble_peripheral": peripheral_supported(),
            "nostr_geochat": False,
            "fragment_reassembly": True,
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
    print(f"daemon_socket:      {payload['daemon']['socket_path']}")
    print(f"inbox_path:         {payload['daemon']['inbox_path']}")
    print(f"peers_path:         {payload['daemon']['peers_path']}")
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
    peer_log,
    private_message_log,
    receipt_log,
    background_mode: bool,
) -> BLEChatTransport:
    transport = BLEChatTransport(
        identity=identity,
        nickname=nickname,
        system_log=system_log,
        message_log=message_log,
        peer_log=peer_log,
        private_message_log=private_message_log,
        receipt_log=receipt_log,
        scan_interval=scan_interval,
        background_mode=background_mode,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    pending_subscribe_announces: dict[str, object] = {}

    def flush_subscribe_announce(address: str) -> None:
        pending_subscribe_announces.pop(address, None)
        asyncio.create_task(transport._handle_peripheral_subscribe(address))

    def handle_subscribe(address: str) -> None:
        existing = pending_subscribe_announces.pop(address, None)
        if existing is not None:
            existing.cancel()
        if loop is None:
            flush_subscribe_announce(address)
            return
        pending_subscribe_announces[address] = loop.call_later(
            _PERIPHERAL_SUBSCRIBE_ANNOUNCE_DELAY_SECONDS,
            flush_subscribe_announce,
            address,
        )

    def handle_unsubscribe(address: str) -> None:
        existing = pending_subscribe_announces.pop(address, None)
        if existing is not None:
            existing.cancel()
        transport._handle_peripheral_unsubscribe(address)

    transport.peripheral_endpoint = create_peripheral_endpoint(
        service_uuid=SERVICE_UUID,
        characteristic_uuid=CHARACTERISTIC_UUID,
        system_log=system_log,
        on_write=transport._enqueue_notification,
        on_subscribe=handle_subscribe,
        on_unsubscribe=handle_unsubscribe,
        loop=loop,
    )
    return transport


async def run_chat(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    runtime.ensure()
    session = ActiveSessionLease(runtime.radio_pid_path)
    acquired, owner_pid = session.acquire()
    if not acquired:
        print(f"another bitchat BLE session is already running (pid {owner_pid})")
        return 1

    store = StateStore(runtime.state_path)
    message_store = MessageStore(runtime.inbox_path)
    peer_store = PeerStore(runtime.peers_path)
    state = store.load()
    if args.nickname:
        state.nickname = args.nickname
        store.save(state)
    transport_ref: dict[str, BLEChatTransport] = {}

    def system_log(message: str) -> None:
        print(f"[system] {message}", flush=True)

    def message_log(
        sender: str,
        content: str,
        timestamp: datetime,
        peer_id: str | None,
        message_id: str | None,
    ) -> None:
        ts = timestamp.astimezone(timezone.utc).strftime("%H:%M:%S")
        message_store.append(
            direction="inbound",
            sender=sender,
            content=content,
            timestamp=timestamp,
            peer_id=peer_id,
            message_id=message_id,
        )
        print(f"\n[{ts}] <{sender}> {content}", flush=True)

    def peer_log(peer) -> None:
        peer_store.upsert(
            peer_id=peer.peer_id,
            nickname=peer.nickname,
            noise_public_key=peer.noise_public_key,
            signing_public_key=peer.signing_public_key,
            addresses=peer.addresses,
            last_seen=peer.last_seen,
        )

    def private_message_log(
        sender: str,
        content: str,
        timestamp: datetime,
        peer_id: str,
        message_id: str,
    ) -> None:
        ts = timestamp.astimezone(timezone.utc).strftime("%H:%M:%S")
        message_store.append(
            direction="inbound",
            sender=sender,
            content=content,
            timestamp=timestamp,
            peer_id=peer_id,
            message_id=message_id,
            scope="dm",
            conversation_id=peer_id,
            message_kind="private_message",
        )
        print(f"\n[{ts}] [dm] <{sender}> {content}", flush=True)

    def receipt_log(peer_id: str, receipt_type: str, message_id: str, timestamp: datetime) -> None:
        active_transport = transport_ref.get("value")
        peer = (
            next((item for item in active_transport.list_peers() if item.peer_id == peer_id), None)
            if active_transport is not None
            else None
        )
        message_store.append(
            direction="system",
            sender=peer.nickname if peer is not None else peer_id,
            content=receipt_type,
            timestamp=timestamp,
            peer_id=peer_id,
            message_id=message_id,
            scope="dm",
            conversation_id=peer_id,
            message_kind="receipt",
            delivery_state=receipt_type,
        )

    try:
        transport = _build_transport(
            identity=state.identity,
            nickname=state.nickname,
            system_log=system_log,
            message_log=message_log,
            peer_log=peer_log,
            private_message_log=private_message_log,
            receipt_log=receipt_log,
            scan_interval=args.scan_interval,
            background_mode=False,
        )
        transport_ref["value"] = transport
        transport.seed_known_peers(peer_store.read_all())
        await transport.start()
        reader, stdin_transport = await _open_stdin_reader()

        print(f"welcome to bitchat-cli; nickname={state.nickname} peer_id={state.identity.peer_id}")
        print("type /help for commands")
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
                    print("/msg <@nickname|peer_id> <message>")
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
                if command == "/msg":
                    target, _, body = rest.strip().partition(" ")
                    if not target or not body.strip():
                        print("usage: /msg <@nickname|peer_id> <message>")
                        continue
                    try:
                        peer = _resolve_peer_target(transport, target)
                    except ValueError as exc:
                        print(str(exc))
                        continue
                    timestamp = datetime.now(timezone.utc)
                    message_id = await transport.send_private_message(peer.peer_id, body.strip())
                    message_store.append(
                        direction="outbound",
                        sender=state.nickname,
                        content=body.strip(),
                        timestamp=timestamp,
                        peer_id=peer.peer_id,
                        message_id=message_id,
                        scope="dm",
                        conversation_id=peer.peer_id,
                        message_kind="private_message",
                        target_peer_id=peer.peer_id,
                        delivery_state="pending",
                    )
                    print(f"[{timestamp.strftime('%H:%M:%S')}] [dm->{peer.nickname}] {body.strip()}")
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
        if "stdin_transport" in locals():
            stdin_transport.close()
        if "transport" in locals():
            await transport.stop()
        session.release()
    return 0


def run_selftest(_args: argparse.Namespace) -> int:
    from .crypto import Identity, peer_id_from_noise_public_key
    from .noise import XXHandshake
    from .protocol import (
        Announcement,
        NoisePayload,
        NoisePayloadType,
        Packet,
        PrivateMessagePacket,
        make_announce_packet,
        make_delivered_payload,
        make_public_message_packet,
        make_read_receipt_payload,
    )

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

    private_message = PrivateMessagePacket(message_id="m-1", content="hello")
    decoded_private_message = PrivateMessagePacket.decode(private_message.encode())
    assert decoded_private_message == private_message

    private_payload = NoisePayload(
        payload_type=NoisePayloadType.PRIVATE_MESSAGE,
        payload=private_message.encode(),
    )
    decoded_private_payload = NoisePayload.decode(private_payload.encode())
    assert decoded_private_payload == private_payload
    assert NoisePayload.decode(make_delivered_payload("m-1")).payload_type == NoisePayloadType.DELIVERED
    assert NoisePayload.decode(make_read_receipt_payload("m-1")).payload_type == NoisePayloadType.READ_RECEIPT

    initiator = XXHandshake(role="initiator", local_static_private_key=identity.noise_private_key)
    responder_identity = Identity.generate()
    responder = XXHandshake(role="responder", local_static_private_key=responder_identity.noise_private_key)
    responder.read_message(initiator.write_message())
    initiator.read_message(responder.write_message())
    responder.read_message(initiator.write_message())
    initiator_send, initiator_receive = initiator.transport_ciphers(use_extracted_nonce=True)
    responder_send, responder_receive = responder.transport_ciphers(use_extracted_nonce=True)
    encrypted_ping = initiator_send.encrypt(b"ping")
    encrypted_pong = responder_send.encrypt(b"pong")
    assert responder_receive.decrypt(encrypted_ping) == b"ping"
    assert initiator_receive.decrypt(encrypted_pong) == b"pong"

    print("selftest ok")
    return 0


async def run_daemon_foreground(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    runtime.ensure()
    session = ActiveSessionLease(runtime.radio_pid_path)
    acquired, owner_pid = session.acquire()
    if not acquired:
        print(f"another bitchat BLE session is already running (pid {owner_pid})")
        return 1

    store = StateStore(runtime.state_path)
    message_store = MessageStore(runtime.inbox_path)
    peer_store = PeerStore(runtime.peers_path)
    state = store.load()
    if args.nickname:
        state.nickname = args.nickname
        store.save(state)
    transport_ref: dict[str, BLEChatTransport] = {}

    log_handle = runtime.daemon_log_path.open("a", encoding="utf-8")
    try:
        def daemon_log(message: str) -> None:
            line = f"{datetime.now(timezone.utc).isoformat()} [system] {message}"
            print(line, file=log_handle, flush=True)

        def daemon_message_log(
            sender: str,
            content: str,
            timestamp: datetime,
            peer_id: str | None,
            message_id: str | None,
        ) -> None:
            message_store.append(
                direction="inbound",
                sender=sender,
                content=content,
                timestamp=timestamp,
                peer_id=peer_id,
                message_id=message_id,
            )
            line = f"{timestamp.astimezone(timezone.utc).isoformat()} <{sender}> {content}"
            print(line, file=log_handle, flush=True)

        def daemon_peer_log(peer) -> None:
            peer_store.upsert(
                peer_id=peer.peer_id,
                nickname=peer.nickname,
                noise_public_key=peer.noise_public_key,
                signing_public_key=peer.signing_public_key,
                addresses=peer.addresses,
                last_seen=peer.last_seen,
            )

        def daemon_private_message_log(
            sender: str,
            content: str,
            timestamp: datetime,
            peer_id: str,
            message_id: str,
        ) -> None:
            message_store.append(
                direction="inbound",
                sender=sender,
                content=content,
                timestamp=timestamp,
                peer_id=peer_id,
                message_id=message_id,
                scope="dm",
                conversation_id=peer_id,
                message_kind="private_message",
            )
            print(
                f"{timestamp.astimezone(timezone.utc).isoformat()} [dm] <{sender}> {content}",
                file=log_handle,
                flush=True,
            )

        def daemon_receipt_log(peer_id: str, receipt_type: str, message_id: str, timestamp: datetime) -> None:
            active_transport = transport_ref.get("value")
            peer = (
                next((item for item in active_transport.list_peers() if item.peer_id == peer_id), None)
                if active_transport is not None
                else None
            )
            message_store.append(
                direction="system",
                sender=peer.nickname if peer is not None else peer_id,
                content=receipt_type,
                timestamp=timestamp,
                peer_id=peer_id,
                message_id=message_id,
                scope="dm",
                conversation_id=peer_id,
                message_kind="receipt",
                delivery_state=receipt_type,
            )

        async def handle_daemon_client(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                raw = await reader.readline()
                if not raw:
                    return
                payload = json.loads(raw.decode("utf-8"))
                command = payload.get("command")
                if command == "dm_send":
                    peer_id = str(payload.get("peer_id") or "")
                    content = str(payload.get("content") or "")
                    if not peer_id or not content:
                        response = {"ok": False, "error": "missing peer_id or content"}
                    else:
                        message_id = await transport.send_private_message(peer_id, content)
                        message_store.append(
                            direction="outbound",
                            sender=state.nickname,
                            content=content,
                            timestamp=datetime.now(timezone.utc),
                            peer_id=peer_id,
                            message_id=message_id,
                            scope="dm",
                            conversation_id=peer_id,
                            message_kind="private_message",
                            target_peer_id=peer_id,
                            delivery_state="pending",
                        )
                        response = {"ok": True, "message_id": message_id}
                elif command == "public_send":
                    content = str(payload.get("content") or "")
                    if not content:
                        response = {"ok": False, "error": "missing content"}
                    else:
                        now = datetime.now(timezone.utc)
                        await transport.send_public_message(content)
                        message_store.append(
                            direction="outbound",
                            sender=state.nickname,
                            content=content,
                            timestamp=now,
                            peer_id=state.identity.peer_id,
                        )
                        response = {"ok": True}
                elif command == "dm_mark_read":
                    peer_id = str(payload.get("peer_id") or "")
                    raw_ids = payload.get("message_ids") or []
                    message_ids = [str(item) for item in raw_ids if str(item)]
                    if not peer_id:
                        response = {"ok": False, "error": "missing peer_id"}
                    else:
                        sent_count = 0
                        now = datetime.now(timezone.utc)
                        for message_id in message_ids:
                            await transport.send_read_receipt(peer_id, message_id)
                            message_store.append(
                                direction="outbound",
                                sender=state.nickname,
                                content="read_receipt",
                                timestamp=now,
                                peer_id=peer_id,
                                message_id=message_id,
                                scope="dm",
                                conversation_id=peer_id,
                                message_kind="receipt",
                                target_peer_id=peer_id,
                            )
                            sent_count += 1
                        response = {"ok": True, "count": sent_count}
                elif command == "runtime_status":
                    response = {
                        "ok": True,
                        "runtime": {
                            "connected_links": len(transport.clients),
                            "known_peers": len(transport.known_peers),
                            "peripheral_advertising": transport.peripheral_endpoint.is_advertising(),
                            "peripheral_subscribers": transport.peripheral_endpoint.subscriber_count(),
                            "peripheral_addresses": list(transport.peripheral_endpoint.subscriber_addresses()),
                            "peripheral_recent_events": list(transport.peripheral_endpoint.recent_events()),
                        },
                    }
                else:
                    response = {"ok": False, "error": "unknown daemon command"}
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                await writer.drain()
            except Exception as exc:
                writer.write((json.dumps({"ok": False, "error": str(exc)}) + "\n").encode("utf-8"))
                await writer.drain()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        runtime.daemon_pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        runtime.daemon_socket_path.unlink(missing_ok=True)
        transport = _build_transport(
            identity=state.identity,
            nickname=state.nickname,
            system_log=daemon_log,
            message_log=daemon_message_log,
            peer_log=daemon_peer_log,
            private_message_log=daemon_private_message_log,
            receipt_log=daemon_receipt_log,
            scan_interval=args.scan_interval,
            background_mode=True,
        )
        transport_ref["value"] = transport
        transport.seed_known_peers(peer_store.read_all())
        daemon_server = await asyncio.start_unix_server(
            handle_daemon_client,
            path=str(runtime.daemon_socket_path),
        )

        stop_event = asyncio.Event()
        cleanup_signal_handlers = _install_stop_signal_handlers(stop_event)
        daemon_log(f"Daemon starting with nickname={state.nickname} peer_id={state.identity.peer_id}.")
        await transport.start()
        await stop_event.wait()
        daemon_log("Daemon stopping.")
    finally:
        if "cleanup_signal_handlers" in locals():
            cleanup_signal_handlers()
        if "transport" in locals():
            await transport.stop()
        if "daemon_server" in locals():
            daemon_server.close()
            await daemon_server.wait_closed()
        runtime.daemon_socket_path.unlink(missing_ok=True)
        try:
            if runtime.daemon_pid_path.exists():
                runtime.daemon_pid_path.unlink()
        finally:
            session.release()
            log_handle.close()
    return 0


def run_daemon_start(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    runtime.ensure()
    session = ActiveSessionLease(runtime.radio_pid_path)
    owner_pid = session.read_owner_pid()
    if owner_pid is not None and pid_is_running(owner_pid):
        print(f"another bitchat BLE session is already running (pid {owner_pid})")
        return 1
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
    env = os.environ.copy()
    repo_src = str(Path(__file__).resolve().parents[1])
    pythonpath_parts = [repo_src]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    log_handle = runtime.daemon_log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=str(Path.cwd()),
        env=env,
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
    print(f"socket: {runtime.daemon_socket_path}")
    print(f"inbox: {runtime.inbox_path}")
    print(f"peers: {runtime.peers_path}")
    return 0


def _clear_daemon_runtime_files(runtime: RuntimePaths, pid: int) -> None:
    runtime.daemon_pid_path.unlink(missing_ok=True)
    lease = ActiveSessionLease(runtime.radio_pid_path)
    owner_pid = lease.read_owner_pid()
    if owner_pid == pid:
        runtime.radio_pid_path.unlink(missing_ok=True)


def _wait_for_daemon_exit(
    runtime: RuntimePaths,
    pid: int,
    *,
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.1,
    is_running=None,
) -> bool:
    check_running = is_running or pid_is_running
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not check_running(pid):
            _clear_daemon_runtime_files(runtime, pid)
            return True
        time.sleep(poll_interval_seconds)
    if not check_running(pid):
        _clear_daemon_runtime_files(runtime, pid)
        return True
    return False


def _install_stop_signal_handlers(stop_event: asyncio.Event):
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, stop_event.set)
        loop.add_signal_handler(signal.SIGTERM, stop_event.set)

        def cleanup() -> None:
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)

        return cleanup
    except (NotImplementedError, RuntimeError):
        def _request_stop(_signum, _frame) -> None:
            stop_event.set()

        old_sigint = signal.signal(signal.SIGINT, _request_stop)
        old_sigterm = signal.signal(signal.SIGTERM, _request_stop)

        def cleanup() -> None:
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)

        return cleanup


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
    if _wait_for_daemon_exit(runtime, pid):
        print(f"stopped daemon (pid {pid})")
        return 0
    print(f"sent SIGTERM to daemon (pid {pid}); still shutting down")
    return 1


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
        print(f"socket: {runtime.daemon_socket_path}")
        print(f"inbox: {runtime.inbox_path}")
        print(f"peers: {runtime.peers_path}")
        try:
            response = _send_daemon_command(runtime, {"command": "runtime_status"})
        except RuntimeError:
            response = {}
        runtime_status = response.get("runtime") if isinstance(response, dict) else None
        if isinstance(runtime_status, dict):
            print(f"connected_links: {runtime_status.get('connected_links')}")
            print(f"known_peers:     {runtime_status.get('known_peers')}")
            print(f"peripheral_adv:  {runtime_status.get('peripheral_advertising')}")
            print(f"peripheral_subs: {runtime_status.get('peripheral_subscribers')}")
            addresses = runtime_status.get("peripheral_addresses")
            if isinstance(addresses, list):
                visible_addresses = ", ".join(str(item) for item in addresses if str(item))
                print(f"peripheral_addrs: {visible_addresses or '-'}")
            events = runtime_status.get("peripheral_recent_events")
            if isinstance(events, list):
                for event in events:
                    if isinstance(event, dict):
                        print(f"peripheral_recent: {_format_peripheral_event(event)}")
        return 0
    print(f"daemon pid file exists but process {pid} is not running")
    return 1


def _format_peripheral_event(event: dict[str, object]) -> str:
    action = str(event.get("event") or "event")
    address = str(event.get("address") or "?")
    parts = [action, address]
    byte_count = event.get("bytes")
    if isinstance(byte_count, int):
        parts.append(f"bytes={byte_count}")
    total_bytes = event.get("total_bytes")
    if isinstance(total_bytes, int):
        parts.append(f"total_bytes={total_bytes}")
    return " ".join(parts)


def run_inbox(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    records = MessageStore(runtime.inbox_path).read_all(deduplicate=True)
    records = [
        row
        for row in records
        if row.get("direction") == "inbound" and row.get("scope", "public") == "public"
    ]
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


def run_peers(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    records = PeerStore(runtime.peers_path).read_all()
    if args.json:
        print(json.dumps(records, indent=2))
        return 0
    if not records:
        print("no known peers recorded")
        return 0
    for row in records:
        addresses = ", ".join(row.get("addresses", [])) or "-"
        print(f"{row['nickname']:16} {row['peer_id']}  {row['last_seen']}  {addresses}")
    return 0


def run_dm_send(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    peer_rows = PeerStore(runtime.peers_path).read_all()
    try:
        peer = _resolve_peer_record(peer_rows, args.target)
    except ValueError as exc:
        print(str(exc))
        return 1
    try:
        response = _send_daemon_command(
            runtime,
            {
                "command": "dm_send",
                "peer_id": str(peer["peer_id"]),
                "content": args.message,
            },
        )
    except RuntimeError as exc:
        print(str(exc))
        return 1
    print(f"queued dm {response['message_id']} to {peer['nickname']} ({peer['peer_id']})")
    return 0


def run_send(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        _send_daemon_command(
            runtime,
            {
                "command": "public_send",
                "content": args.message,
            },
        )
    except RuntimeError as exc:
        print(str(exc))
        return 1
    print("queued public message")
    return 0


def run_dm_inbox(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    peer_rows = PeerStore(runtime.peers_path).read_all()
    peer_index = {row["peer_id"]: row for row in peer_rows}
    conversations: dict[str, dict[str, object]] = {}
    for row in _read_dm_records(runtime):
        conversation_id = row.get("conversation_id") or row.get("peer_id") or row.get("target_peer_id")
        if not conversation_id:
            continue
        summary = conversations.setdefault(
            str(conversation_id),
            {
                "conversation_id": str(conversation_id),
                "peer_id": str(conversation_id),
                "nickname": peer_index.get(str(conversation_id), {}).get("nickname", str(conversation_id)[:8]),
                "last_timestamp": "",
                "last_preview": "",
            },
        )
        timestamp = str(row.get("timestamp") or "")
        if timestamp >= str(summary["last_timestamp"]):
            summary["last_timestamp"] = timestamp
            if row.get("message_kind") == "private_message":
                summary["last_preview"] = str(row.get("content") or "")
            else:
                summary["last_preview"] = str(row.get("content") or row.get("message_kind") or "")

    rows = sorted(
        conversations.values(),
        key=lambda row: str(row.get("last_timestamp") or ""),
    )
    if args.tail is not None:
        rows = rows[-args.tail :]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no direct-message conversations recorded")
        return 0
    for row in rows:
        print(
            f"{row['nickname']:16} {row['peer_id']}  {row['last_timestamp']}  {row['last_preview']}"
        )
    return 0


def run_dm_history(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    peer_rows = PeerStore(runtime.peers_path).read_all()
    try:
        conversation_id = str(_resolve_peer_record(peer_rows, args.target)["peer_id"])
    except ValueError:
        conversation_id = args.target[1:] if args.target.startswith("@") else args.target

    rows = []
    for row in _read_dm_records(runtime):
        row_conversation_id = row.get("conversation_id") or row.get("peer_id") or row.get("target_peer_id")
        if str(row_conversation_id) != conversation_id:
            continue
        rows.append(row)
    rows.sort(key=lambda row: str(row.get("timestamp") or ""))
    if args.tail is not None:
        rows = rows[-args.tail :]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no direct messages recorded for that peer")
        return 0
    for row in rows:
        if row.get("message_kind") == "receipt":
            print(
                f"{row['timestamp']} [receipt] {row['content']} {row['message_id']}"
            )
            continue
        print(f"{row['timestamp']} <{row['sender']}> {row['content']}")
    pending_read_receipts = _pending_read_receipt_ids(rows, conversation_id)
    if pending_read_receipts:
        try:
            _send_daemon_command(
                runtime,
                {
                    "command": "dm_mark_read",
                    "peer_id": conversation_id,
                    "message_ids": pending_read_receipts,
                },
            )
        except RuntimeError:
            pass
    return 0
