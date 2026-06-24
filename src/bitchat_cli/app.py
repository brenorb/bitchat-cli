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
from .files import load_outgoing_file
from .geochat import (
    GeoChatDaemon,
    decode_bounds,
    encode_geohash,
    encode_geohash_levels,
    derive_nostr_identity,
    event_committed_pow_difficulty,
    event_display_name,
    event_geohash,
    event_is_teleported,
    event_pow_difficulty,
    geohash_levels,
    geohash_precision_for_level,
    lookup_place,
    normalize_geohash,
)
from .network import (
    NetworkConfig,
    first_live_tor_proxy,
    proxy_status_payload,
    resolve_proxy_target,
    test_proxy_url,
)
from .nostr import NostrDMClient, NostrIdentity, decode_npub
from .peripheral import create_peripheral_endpoint, peripheral_supported
from .queries import (
    attachment_summary as _attachment_summary,
    geohash_participants as _geohash_participants,
    pending_read_receipt_ids as _pending_read_receipt_ids,
    read_dm_records as _read_dm_records,
    read_public_records as _read_public_records,
    resolve_peer_or_social_target as _resolve_peer_or_social_target,
    resolve_peer_record as _resolve_peer_record,
    resolve_social_record as _resolve_social_record,
    social_store as _social_store,
)
from .social import SocialStore
from .state import ActiveSessionLease, MessageStore, PeerStore, RuntimePaths, StateStore, pid_is_running
from .transport import BLEChatTransport

_PERIPHERAL_SUBSCRIBE_ANNOUNCE_DELAY_SECONDS = 0.4
_MESH_CONVERSATION_ID = "mesh"
_HIGH_PRECISION_GEOHASH_WARNING_LENGTH = 7
_MEDIUM_PRECISION_GEOHASH_WARNING_LENGTH = 6


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


def _network_config_from_state(store: StateStore) -> NetworkConfig:
    return NetworkConfig.from_state(store.load())


def _geohash_privacy_warning(geohash: str) -> str | None:
    if len(geohash) >= _HIGH_PRECISION_GEOHASH_WARNING_LENGTH:
        return "high-precision geohash disclosure requires --force at block/building precision"
    if len(geohash) >= _MEDIUM_PRECISION_GEOHASH_WARNING_LENGTH:
        return "warning: neighborhood precision geohashes are more identifying than city/province channels"
    return None


def _resolve_nostr_identity(state) -> NostrIdentity:
    return NostrIdentity.from_private_key_hex(state.nostr_private_key_hex)


def _peer_has_live_ble_link(transport: BLEChatTransport, peer_id: str) -> bool:
    peer = transport.known_peers.get(peer_id)
    if peer is None:
        return False
    if peer.active_address is not None and (
        peer.active_address in transport.clients or peer.active_address.startswith("peripheral:")
    ):
        return True
    return any(address in transport.clients or address.startswith("peripheral:") for address in peer.addresses)


def _eligible_nostr_fallback_record(
    social_store: SocialStore,
    peer_id: str,
) -> dict[str, object] | None:
    record = social_store.get(peer_id)
    if record is None:
        return None
    if not bool(record.get("favorite")) or bool(record.get("blocked")):
        return None
    npub = str(record.get("nostr_npub") or "")
    if not npub:
        return None
    return record


def _record_geohash_event(
    event: dict[str, object],
    *,
    message_store: MessageStore,
    log_handle,
) -> None:
    geohash = event_geohash(event)
    if geohash is None:
        return
    raw_ts = int(event.get("created_at") or 0)
    timestamp = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
    sender_name = event_display_name(event)
    content = str(event.get("content") or "").strip()
    kind = int(event.get("kind") or 0)
    message_kind = "geohash_presence" if kind == 20001 else "geohash_message"
    message_store.append(
        direction="inbound",
        sender=sender_name,
        content=content,
        timestamp=timestamp,
        peer_id=str(event.get("pubkey") or ""),
        message_id=str(event.get("id") or ""),
        scope="public",
        conversation_id=geohash,
        message_kind=message_kind,
        pow_difficulty=(
            event_pow_difficulty(str(event.get("id") or ""))
            if event_committed_pow_difficulty(event) is not None and str(event.get("id") or "")
            else None
        ),
        teleported=event_is_teleported(event),
    )
    if kind == 20001:
        return
    print(
        f"{timestamp.astimezone(timezone.utc).isoformat()} #{geohash} <{sender_name}> {content}",
        file=log_handle,
        flush=True,
    )


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


def doctor_payload(store: StateStore) -> dict[str, object]:
    state = store.load()
    paths = RuntimePaths()
    network_payload = proxy_status_payload(NetworkConfig.from_state(state))
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
        "nostr_npub": _resolve_nostr_identity(state).npub,
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
            "file_transfer": True,
            "mesh_relay": True,
            "local_sync": True,
            "ble_peripheral": peripheral_supported(),
            "nostr_geochat": True,
            "nostr_dm_fallback": True,
            "proxy_policy": True,
            "fragment_reassembly": True,
        },
        "network": network_payload,
        "watched_geohashes": list(state.watched_geohashes),
    }


def run_doctor(args: argparse.Namespace) -> int:
    store = StateStore()
    payload = doctor_payload(store)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("bitchat doctor")
    print(f"nickname:           {payload['nickname']}")
    print(f"peer_id:            {payload['peer_id']}")
    print(f"nostr_npub:         {payload['nostr_npub']}")
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
    watched_geohashes = payload.get("watched_geohashes") or []
    print(f"watched_geohashes:  {', '.join('#' + item for item in watched_geohashes) or '-'}")
    print(f"proxy_mode:         {payload['network']['mode']}")
    print(f"proxy_policy:       {payload['network']['policy']}")
    print(f"proxy_target:       {payload['network']['target']}")
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
    public_file_log=None,
    private_file_log=None,
    background_mode: bool,
    file_runtime_paths: RuntimePaths | None = None,
) -> BLEChatTransport:
    transport = BLEChatTransport(
        identity=identity,
        nickname=nickname,
        system_log=system_log,
        message_log=message_log,
        peer_log=peer_log,
        private_message_log=private_message_log,
        receipt_log=receipt_log,
        public_file_log=public_file_log,
        private_file_log=private_file_log,
        scan_interval=scan_interval,
        background_mode=background_mode,
        file_runtime_paths=file_runtime_paths,
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
            conversation_id=_MESH_CONVERSATION_ID,
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

    def public_file_log(
        sender: str,
        saved_path: str,
        file_name: str,
        mime_type: str,
        file_size: int,
        timestamp: datetime,
        peer_id: str | None,
        transfer_id: str,
    ) -> None:
        ts = timestamp.astimezone(timezone.utc).strftime("%H:%M:%S")
        record = {
            "content": saved_path,
            "attachment_name": file_name,
            "attachment_mime": mime_type,
            "attachment_size_bytes": file_size,
        }
        message_store.append(
            direction="inbound",
            sender=sender,
            content=saved_path,
            timestamp=timestamp,
            peer_id=peer_id,
            message_id=transfer_id,
            conversation_id=_MESH_CONVERSATION_ID,
            message_kind="public_file",
            attachment_name=file_name,
            attachment_mime=mime_type,
            attachment_size_bytes=file_size,
        )
        print(f"\n[{ts}] <{sender}> {_attachment_summary(record)}", flush=True)

    def private_file_log(
        sender: str,
        saved_path: str,
        file_name: str,
        mime_type: str,
        file_size: int,
        timestamp: datetime,
        peer_id: str,
        transfer_id: str,
    ) -> None:
        ts = timestamp.astimezone(timezone.utc).strftime("%H:%M:%S")
        record = {
            "content": saved_path,
            "attachment_name": file_name,
            "attachment_mime": mime_type,
            "attachment_size_bytes": file_size,
        }
        message_store.append(
            direction="inbound",
            sender=sender,
            content=saved_path,
            timestamp=timestamp,
            peer_id=peer_id,
            message_id=transfer_id,
            scope="dm",
            conversation_id=peer_id,
            message_kind="private_file",
            attachment_name=file_name,
            attachment_mime=mime_type,
            attachment_size_bytes=file_size,
        )
        print(f"\n[{ts}] [dm] <{sender}> {_attachment_summary(record)}", flush=True)

    try:
        transport = _build_transport(
            identity=state.identity,
            nickname=state.nickname,
            system_log=system_log,
            message_log=message_log,
            peer_log=peer_log,
            private_message_log=private_message_log,
            receipt_log=receipt_log,
            public_file_log=public_file_log,
            private_file_log=private_file_log,
            scan_interval=args.scan_interval,
            background_mode=False,
            file_runtime_paths=runtime,
        )
        transport_ref["value"] = transport
        transport.seed_known_peers(peer_store.read_all())
        await transport.start()
        reader, stdin_transport = await _open_stdin_reader()

        print(f"welcome to bitchat; nickname={state.nickname} peer_id={state.identity.peer_id}")
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
        FilePacket,
        NoisePayload,
        NoisePayloadType,
        Packet,
        PrivateMessagePacket,
        make_announce_packet,
        make_delivered_payload,
        make_file_transfer_packet,
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

    file_packet = FilePacket(
        file_name="note.txt",
        file_size=5,
        mime_type="text/plain",
        content=b"hello",
    )
    decoded_file_packet = FilePacket.decode(file_packet.encode())
    assert decoded_file_packet == file_packet
    decoded_file_transfer = Packet.decode(
        make_file_transfer_packet(identity, file_packet, timestamp_ms=1_700_000_000_000).encode(padding=False)
    )
    assert decoded_file_transfer.version == 2
    assert FilePacket.decode(decoded_file_transfer.payload) == file_packet

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
    social_store = _social_store(runtime)
    state = store.load()
    network_config = NetworkConfig.from_state(state)
    if args.nickname:
        state.nickname = args.nickname
        store.save(state)
        network_config = NetworkConfig.from_state(state)
    transport_ref: dict[str, BLEChatTransport] = {}
    nostr_identity = _resolve_nostr_identity(state)

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
                conversation_id=_MESH_CONVERSATION_ID,
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

        def daemon_public_file_log(
            sender: str,
            saved_path: str,
            file_name: str,
            mime_type: str,
            file_size: int,
            timestamp: datetime,
            peer_id: str | None,
            transfer_id: str,
        ) -> None:
            record = {
                "content": saved_path,
                "attachment_name": file_name,
                "attachment_mime": mime_type,
                "attachment_size_bytes": file_size,
            }
            message_store.append(
                direction="inbound",
                sender=sender,
                content=saved_path,
                timestamp=timestamp,
                peer_id=peer_id,
                message_id=transfer_id,
                conversation_id=_MESH_CONVERSATION_ID,
                message_kind="public_file",
                attachment_name=file_name,
                attachment_mime=mime_type,
                attachment_size_bytes=file_size,
            )
            print(
                f"{timestamp.astimezone(timezone.utc).isoformat()} <{sender}> {_attachment_summary(record)}",
                file=log_handle,
                flush=True,
            )

        def daemon_private_file_log(
            sender: str,
            saved_path: str,
            file_name: str,
            mime_type: str,
            file_size: int,
            timestamp: datetime,
            peer_id: str,
            transfer_id: str,
        ) -> None:
            record = {
                "content": saved_path,
                "attachment_name": file_name,
                "attachment_mime": mime_type,
                "attachment_size_bytes": file_size,
            }
            message_store.append(
                direction="inbound",
                sender=sender,
                content=saved_path,
                timestamp=timestamp,
                peer_id=peer_id,
                message_id=transfer_id,
                scope="dm",
                conversation_id=peer_id,
                message_kind="private_file",
                attachment_name=file_name,
                attachment_mime=mime_type,
                attachment_size_bytes=file_size,
            )
            print(
                f"{timestamp.astimezone(timezone.utc).isoformat()} [dm] <{sender}> {_attachment_summary(record)}",
                file=log_handle,
                flush=True,
            )

        def daemon_geohash_message_log(event: dict[str, object]) -> None:
            _record_geohash_event(event, message_store=message_store, log_handle=log_handle)

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

        def daemon_nostr_message_log(message) -> None:
            social_record = social_store.get_by_pubkey(message.sender_pubkey_hex)
            if social_record is None or bool(social_record.get("blocked")):
                return
            peer_id = str(social_record.get("peer_id") or message.sender_peer_id)
            nickname = str(social_record.get("nickname") or peer_id[:8])
            timestamp = datetime.fromtimestamp(message.timestamp_ms / 1000, tz=timezone.utc)
            if message.message_kind == "private_message":
                message_store.append(
                    direction="inbound",
                    sender=nickname,
                    content=message.content,
                    timestamp=timestamp,
                    peer_id=peer_id,
                    message_id=message.message_id,
                    scope="dm",
                    conversation_id=peer_id,
                    message_kind="private_message",
                    delivery_state="nostr_fallback",
                )
                print(
                    f"{timestamp.astimezone(timezone.utc).isoformat()} [dm/nostr] <{nickname}> {message.content}",
                    file=log_handle,
                    flush=True,
                )
                npub = str(social_record.get("nostr_npub") or "")
                if npub and not bool(social_record.get("blocked")):
                    asyncio.create_task(
                        nostr_client.send_receipt(
                            sender_peer_id=state.identity.peer_id,
                            recipient_peer_id=peer_id,
                            recipient_npub=npub,
                            receipt_type="delivered",
                            message_id=message.message_id,
                        )
                    )
                return
            message_store.append(
                direction="system",
                sender=nickname,
                content=message.content,
                timestamp=timestamp,
                peer_id=peer_id,
                message_id=message.message_id,
                scope="dm",
                conversation_id=peer_id,
                message_kind="receipt",
                delivery_state=message.content,
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
                        social_record = _eligible_nostr_fallback_record(social_store, peer_id)
                        now = datetime.now(timezone.utc)
                        if _peer_has_live_ble_link(transport, peer_id):
                            message_id = await transport.send_private_message(peer_id, content)
                            delivery_state = "pending"
                            response = {"ok": True, "message_id": message_id, "transport": "ble"}
                        elif social_record is not None:
                            message_id = f"nostr-{int(time.time() * 1000)}"
                            await nostr_client.send_private_message(
                                sender_peer_id=state.identity.peer_id,
                                recipient_peer_id=peer_id,
                                recipient_npub=str(social_record["nostr_npub"]),
                                message_id=message_id,
                                content=content,
                            )
                            delivery_state = "nostr_fallback"
                            response = {"ok": True, "message_id": message_id, "transport": "nostr"}
                        else:
                            response = {"ok": False, "error": "no live BitChat link and no authorized Nostr fallback"}
                            message_id = ""
                            delivery_state = None
                        if not response.get("ok"):
                            pass
                        else:
                            message_store.append(
                                direction="outbound",
                                sender=state.nickname,
                                content=content,
                                timestamp=now,
                                peer_id=peer_id,
                                message_id=message_id,
                                scope="dm",
                                conversation_id=peer_id,
                                message_kind="private_message",
                                target_peer_id=peer_id,
                                delivery_state=delivery_state,
                            )
                elif command == "dm_send_file":
                    peer_id = str(payload.get("peer_id") or "")
                    raw_path = str(payload.get("path") or "")
                    if not peer_id or not raw_path:
                        response = {"ok": False, "error": "missing peer_id or path"}
                    else:
                        outgoing = load_outgoing_file(Path(raw_path))
                        transfer_id = await transport.send_private_file(peer_id, Path(raw_path))
                        message_store.append(
                            direction="outbound",
                            sender=state.nickname,
                            content=str(Path(raw_path).expanduser().resolve()),
                            timestamp=datetime.now(timezone.utc),
                            peer_id=peer_id,
                            message_id=transfer_id,
                            scope="dm",
                            conversation_id=peer_id,
                            message_kind="private_file",
                            target_peer_id=peer_id,
                            delivery_state="pending",
                            attachment_name=outgoing.file_name,
                            attachment_mime=outgoing.mime_type,
                            attachment_size_bytes=outgoing.file_size,
                        )
                        response = {"ok": True, "transfer_id": transfer_id}
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
                            conversation_id=_MESH_CONVERSATION_ID,
                        )
                        response = {"ok": True}
                elif command == "public_send_file":
                    raw_path = str(payload.get("path") or "")
                    if not raw_path:
                        response = {"ok": False, "error": "missing path"}
                    else:
                        outgoing = load_outgoing_file(Path(raw_path))
                        transfer_id = await transport.send_public_file(Path(raw_path))
                        message_store.append(
                            direction="outbound",
                            sender=state.nickname,
                            content=str(Path(raw_path).expanduser().resolve()),
                            timestamp=datetime.now(timezone.utc),
                            peer_id=state.identity.peer_id,
                            message_id=transfer_id,
                            conversation_id=_MESH_CONVERSATION_ID,
                            message_kind="public_file",
                            attachment_name=outgoing.file_name,
                            attachment_mime=outgoing.mime_type,
                            attachment_size_bytes=outgoing.file_size,
                        )
                        response = {"ok": True, "transfer_id": transfer_id}
                elif command == "geo_send":
                    geohash = str(payload.get("geohash") or "")
                    content = str(payload.get("content") or "")
                    teleported = bool(payload.get("teleported"))
                    try:
                        pow_difficulty = int(payload.get("pow_difficulty") or 0)
                    except (TypeError, ValueError):
                        pow_difficulty = -1
                    if not geohash or not content:
                        response = {"ok": False, "error": "missing geohash or content"}
                    elif pow_difficulty < 0:
                        response = {"ok": False, "error": "invalid pow_difficulty"}
                    else:
                        event = await geochat.publish(
                            geohash,
                            content,
                            pow_difficulty=pow_difficulty,
                            teleported=teleported,
                        )
                        now = datetime.fromtimestamp(int(event["created_at"]), tz=timezone.utc)
                        message_store.append(
                            direction="outbound",
                            sender=state.nickname,
                            content=content,
                            timestamp=now,
                            peer_id=str(event["pubkey"]),
                            message_id=str(event["id"]),
                            scope="public",
                            conversation_id=geohash,
                            message_kind="geohash_message",
                            pow_difficulty=(
                                event_pow_difficulty(str(event["id"]))
                                if event_committed_pow_difficulty(event) is not None
                                else None
                            ),
                            teleported=event_is_teleported(event),
                        )
                        response = {"ok": True, "message_id": event["id"]}
                elif command == "geo_watch_set":
                    raw_geohashes = payload.get("geohashes") or []
                    watched_geohashes = [str(item) for item in raw_geohashes if str(item)]
                    await geochat.set_watches(watched_geohashes)
                    response = {"ok": True, "geohashes": geochat.runtime_status()["watched_geohashes"]}
                elif command == "dm_mark_read":
                    peer_id = str(payload.get("peer_id") or "")
                    raw_ids = payload.get("message_ids") or []
                    message_ids = [str(item) for item in raw_ids if str(item)]
                    if not peer_id:
                        response = {"ok": False, "error": "missing peer_id"}
                    else:
                        social_record = _eligible_nostr_fallback_record(social_store, peer_id)
                        sent_count = 0
                        now = datetime.now(timezone.utc)
                        for message_id in message_ids:
                            if _peer_has_live_ble_link(transport, peer_id):
                                await transport.send_read_receipt(peer_id, message_id)
                            elif social_record is not None:
                                await nostr_client.send_receipt(
                                    sender_peer_id=state.identity.peer_id,
                                    recipient_peer_id=peer_id,
                                    recipient_npub=str(social_record["nostr_npub"]),
                                    receipt_type="read_receipt",
                                    message_id=message_id,
                                )
                            else:
                                continue
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
                            "watched_geohashes": geochat.runtime_status()["watched_geohashes"],
                            "geohash_relay_subscriptions": geochat.runtime_status()["relay_subscriptions"],
                            "nostr_relays": list(nostr_client.relay_urls),
                            "proxy_mode": network_config.proxy_mode,
                            "proxy_policy": network_config.proxy_policy,
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
            public_file_log=daemon_public_file_log,
            private_file_log=daemon_private_file_log,
            scan_interval=args.scan_interval,
            background_mode=True,
            file_runtime_paths=runtime,
        )
        transport_ref["value"] = transport
        transport.seed_known_peers(peer_store.read_all())
        geochat = GeoChatDaemon(
            relay_cache_path=runtime.georelays_cache_path,
            seed_hex=state.nostr_device_seed_hex,
            nickname=state.nickname,
            on_message=daemon_geohash_message_log,
            system_log=daemon_log,
            network_config=network_config,
        )
        nostr_client = NostrDMClient(
            identity=nostr_identity,
            relay_urls=state.nostr_relays,
            network_config=network_config,
            on_message=daemon_nostr_message_log,
            system_log=daemon_log,
        )
        daemon_server = await asyncio.start_unix_server(
            handle_daemon_client,
            path=str(runtime.daemon_socket_path),
        )

        stop_event = asyncio.Event()
        cleanup_signal_handlers = _install_stop_signal_handlers(stop_event)
        daemon_log(f"Daemon starting with nickname={state.nickname} peer_id={state.identity.peer_id}.")
        await transport.start()
        await geochat.start(state.watched_geohashes)
        await nostr_client.start()
        await stop_event.wait()
        daemon_log("Daemon stopping.")
    finally:
        if "cleanup_signal_handlers" in locals():
            cleanup_signal_handlers()
        if "transport" in locals():
            await transport.stop()
        if "geochat" in locals():
            await geochat.stop()
        if "nostr_client" in locals():
            await nostr_client.stop()
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
            print(f"geo_relays:      {runtime_status.get('geohash_relay_subscriptions')}")
            print(f"proxy_mode:      {runtime_status.get('proxy_mode')}")
            print(f"proxy_policy:    {runtime_status.get('proxy_policy')}")
            relays = runtime_status.get("nostr_relays")
            if isinstance(relays, list):
                print(f"nostr_relays:    {len(relays)} configured")
            addresses = runtime_status.get("peripheral_addresses")
            if isinstance(addresses, list):
                visible_addresses = ", ".join(str(item) for item in addresses if str(item))
                print(f"peripheral_addrs: {visible_addresses or '-'}")
            geohashes = runtime_status.get("watched_geohashes")
            if isinstance(geohashes, list):
                visible_geohashes = ", ".join(f"#{item}" for item in geohashes if str(item))
                print(f"geo_watches:     {visible_geohashes or '-'}")
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


def _normalize_geo_target(raw: str) -> str:
    try:
        return normalize_geohash(raw)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def run_inbox(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    records = [
        row
        for row in _read_public_records(runtime)
        if str(row.get("conversation_id") or _MESH_CONVERSATION_ID) == _MESH_CONVERSATION_ID
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
        content = (
            _attachment_summary(row)
            if str(row.get("message_kind") or "") in {"public_file", "private_file"}
            else row["content"]
        )
        print(f"{timestamp} #mesh <{sender}> {content}")
    return 0


def run_geo_inbox(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        geohash = _normalize_geo_target(args.geohash)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    records = [
        row
        for row in _read_public_records(runtime)
        if str(row.get("conversation_id") or "") == geohash
        and str(row.get("message_kind") or "") == "geohash_message"
    ]
    if args.tail is not None:
        records = records[-args.tail :]
    if args.json:
        print(json.dumps(records, indent=2))
        return 0
    if not records:
        print(f"no inbound messages recorded for #{geohash}")
        return 0
    for row in records:
        suffixes: list[str] = []
        if row.get("pow_difficulty") is not None:
            suffixes.append(f"[{int(row['pow_difficulty'])}b]")
        if row.get("teleported"):
            suffixes.append("[teleport]")
        suffix = f" {' '.join(suffixes)}" if suffixes else ""
        print(f"{row['timestamp']} #{geohash} <{row['sender']}> {row['content']}{suffix}")
    return 0


def run_geo_levels(args: argparse.Namespace) -> int:
    try:
        levels = geohash_levels(args.geohash)
    except ValueError as exc:
        print(str(exc))
        return 1
    if args.json:
        print(json.dumps(levels, indent=2))
        return 0
    for level, value in levels.items():
        print(f"{level:14} #{value}")
    return 0


def run_geo_encode(args: argparse.Namespace) -> int:
    try:
        if args.all_levels:
            payload = encode_geohash_levels(float(args.lat), float(args.lon))
        else:
            level = args.level or "city"
            payload = {level: encode_geohash(float(args.lat), float(args.lon), precision=geohash_precision_for_level(level))}
    except ValueError as exc:
        print(str(exc))
        return 1
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    for level, value in payload.items():
        print(f"{level:14} #{value}")
    return 0


def run_geo_decode(args: argparse.Namespace) -> int:
    try:
        geohash = _normalize_geo_target(args.geohash)
        lat_bounds, lon_bounds = decode_bounds(geohash)
        center_lat = (lat_bounds[0] + lat_bounds[1]) / 2
        center_lon = (lon_bounds[0] + lon_bounds[1]) / 2
    except (RuntimeError, ValueError) as exc:
        print(str(exc))
        return 1
    payload = {
        "geohash": geohash,
        "center": {"lat": center_lat, "lon": center_lon},
        "bounds": {
            "lat_min": lat_bounds[0],
            "lat_max": lat_bounds[1],
            "lon_min": lon_bounds[0],
            "lon_max": lon_bounds[1],
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"geohash:  #{geohash}")
    print(f"center:   {center_lat:.6f}, {center_lon:.6f}")
    print(f"lat:      {lat_bounds[0]:.6f} .. {lat_bounds[1]:.6f}")
    print(f"lon:      {lon_bounds[0]:.6f} .. {lon_bounds[1]:.6f}")
    return 0


def run_geo_lookup(args: argparse.Namespace) -> int:
    state = StateStore().load()
    try:
        results = lookup_place(
            args.query,
            network_config=NetworkConfig.from_state(state),
            limit=int(args.limit),
        )
    except Exception as exc:
        print(str(exc))
        return 1
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    if not results:
        print("no place matches found")
        return 0
    print("network lookup; no query or coordinates were persisted")
    for index, row in enumerate(results, start=1):
        print(f"[{index}] {row['label']}")
        print(f"    {row['lat']:.6f}, {row['lon']:.6f}")
        levels = row.get("levels", {})
        if isinstance(levels, dict):
            rendered = ", ".join(f"{level}=#{value}" for level, value in levels.items())
            print(f"    {rendered}")
    return 0


def run_geo_who(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        geohash = _normalize_geo_target(args.geohash)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    state = StateStore(runtime.state_path).load()
    participants = _geohash_participants(
        _read_public_records(runtime),
        geohash=geohash,
        self_pubkey=derive_nostr_identity(bytes.fromhex(state.nostr_device_seed_hex), geohash).public_key_hex,
        now=datetime.now(timezone.utc),
    )
    if args.json:
        print(json.dumps(participants, indent=2))
        return 0
    if not participants:
        print("no one else is around right now.")
        return 0
    print(f"#{geohash} participants")
    for row in participants:
        suffix = " teleport" if row.get("teleported") else ""
        print(f"{row['sender']:20} {row['last_seen']}{suffix}")
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


def run_social_list(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    rows = _social_store(runtime).read_all()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no social links recorded")
        return 0
    for row in rows:
        flags = []
        if row.get("favorite"):
            flags.append("favorite")
        if row.get("blocked"):
            flags.append("blocked")
        if row.get("verified_at"):
            flags.append("verified")
        flag_suffix = f" [{' '.join(flags)}]" if flags else ""
        print(f"{row['nickname']:16} {row['peer_id']}  {row.get('nostr_npub') or '-'}{flag_suffix}")
    return 0


def run_social_link(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    peer_rows = PeerStore(runtime.peers_path).read_all()
    try:
        peer = _resolve_peer_record(peer_rows, args.target)
        nostr_pubkey_hex = decode_npub(args.npub)
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    _social_store(runtime).upsert(
        peer_id=str(peer["peer_id"]),
        nickname=str(peer["nickname"]),
        nostr_npub=args.npub,
        nostr_pubkey_hex=nostr_pubkey_hex,
    )
    print(f"linked {peer['nickname']} ({peer['peer_id']}) to {args.npub}")
    return 0


def run_social_unlink(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        record = _resolve_peer_or_social_target(runtime, args.target)
    except ValueError as exc:
        print(str(exc))
        return 1
    _social_store(runtime).delete(str(record["peer_id"]))
    print(f"removed social link for {record['peer_id']}")
    return 0


def _toggle_social_flag(args: argparse.Namespace, *, favorite: bool | None = None, blocked: bool | None = None) -> int:
    runtime = RuntimePaths()
    try:
        record = _resolve_peer_or_social_target(runtime, args.target)
    except ValueError as exc:
        print(str(exc))
        return 1
    row = _social_store(runtime).upsert(
        peer_id=str(record["peer_id"]),
        nickname=str(record.get("nickname") or record["peer_id"]),
        favorite=favorite,
        blocked=blocked,
    )
    flags = []
    if row.get("favorite"):
        flags.append("favorite")
    if row.get("blocked"):
        flags.append("blocked")
    print(f"{row['peer_id']} -> {', '.join(flags) or 'neutral'}")
    return 0


def run_social_favorite(args: argparse.Namespace) -> int:
    return _toggle_social_flag(args, favorite=True)


def run_social_unfavorite(args: argparse.Namespace) -> int:
    return _toggle_social_flag(args, favorite=False)


def run_social_block(args: argparse.Namespace) -> int:
    return _toggle_social_flag(args, blocked=True)


def run_social_unblock(args: argparse.Namespace) -> int:
    return _toggle_social_flag(args, blocked=False)


def run_social_fingerprint(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    peer_rows = PeerStore(runtime.peers_path).read_all()
    social_row = None
    try:
        peer = _resolve_peer_record(peer_rows, args.target)
    except ValueError:
        try:
            social_row = _resolve_social_record(_social_store(runtime).read_all(), args.target)
            peer = {"peer_id": social_row["peer_id"], "nickname": social_row.get("nickname") or social_row["peer_id"]}
        except ValueError as exc:
            print(str(exc))
            return 1
    social_row = social_row or _social_store(runtime).get(str(peer["peer_id"]))
    print(f"peer_id:           {peer['peer_id']}")
    print(f"nickname:          {peer.get('nickname') or '-'}")
    if "noise_public_key" in peer:
        print(f"noise_public_key:  {peer['noise_public_key']}")
    if "signing_public_key" in peer:
        print(f"signing_public_key:{peer['signing_public_key']}")
    print(f"nostr_npub:        {social_row.get('nostr_npub') if social_row else '-'}")
    print(f"favorite:          {bool(social_row.get('favorite')) if social_row else False}")
    print(f"blocked:           {bool(social_row.get('blocked')) if social_row else False}")
    print(f"verified_at:       {social_row.get('verified_at') if social_row else '-'}")
    return 0


def run_network_proxy_status(args: argparse.Namespace) -> int:
    payload = proxy_status_payload(_network_config_from_state(StateStore()))
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"mode:            {payload['mode']}")
    print(f"policy:          {payload['policy']}")
    print(f"target:          {payload['target']}")
    healthy = payload.get("healthy")
    print(f"healthy:         {healthy if healthy is not None else 'unknown'}")
    return 0


def run_network_proxy_set(args: argparse.Namespace) -> int:
    store = StateStore()
    state = store.load()
    try:
        mode, proxy_url = resolve_proxy_target(args.target)
    except ValueError as exc:
        print(str(exc))
        return 1
    state.network_proxy_mode = mode
    state.network_proxy_url = proxy_url
    state.network_proxy_policy = args.policy
    store.save(state)
    target = proxy_url or mode
    if args.target.strip().lower() == "tor":
        live = first_live_tor_proxy()
        if live is not None:
            target = live
    print(f"proxy set to {target} ({args.policy})")
    return 0


def run_network_proxy_disable(_args: argparse.Namespace) -> int:
    store = StateStore()
    state = store.load()
    state.network_proxy_mode = "disabled"
    state.network_proxy_url = None
    store.save(state)
    print("proxy disabled")
    return 0


def run_network_proxy_test(args: argparse.Namespace) -> int:
    config = _network_config_from_state(StateStore())
    payload = proxy_status_payload(config)
    if config.proxy_mode == "custom" and config.proxy_url:
        payload["healthy"] = test_proxy_url(config.proxy_url)
    elif config.proxy_mode == "system":
        payload["healthy"] = None
    else:
        payload["healthy"] = False
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"mode:    {payload['mode']}")
    print(f"policy:  {payload['policy']}")
    print(f"target:  {payload['target']}")
    healthy = payload.get("healthy")
    print(f"healthy: {healthy if healthy is not None else 'unknown'}")
    return 0 if healthy is not False else 1


def run_dm_send(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        peer = _resolve_peer_or_social_target(runtime, args.target)
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


def run_dm_send_file(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        peer = _resolve_peer_or_social_target(runtime, args.target)
    except ValueError as exc:
        print(str(exc))
        return 1
    try:
        response = _send_daemon_command(
            runtime,
            {
                "command": "dm_send_file",
                "peer_id": str(peer["peer_id"]),
                "path": args.path,
            },
        )
    except RuntimeError as exc:
        print(str(exc))
        return 1
    print(f"queued file {response['transfer_id']} to {peer['nickname']} ({peer['peer_id']})")
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


def run_send_file(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        response = _send_daemon_command(
            runtime,
            {
                "command": "public_send_file",
                "path": args.path,
            },
        )
    except RuntimeError as exc:
        print(str(exc))
        return 1
    print(f"queued public file {response['transfer_id']}")
    return 0


def run_geo_send(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        geohash = _normalize_geo_target(args.geohash)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    warning = _geohash_privacy_warning(geohash)
    force = bool(getattr(args, "force", False))
    if warning is not None and len(geohash) >= _HIGH_PRECISION_GEOHASH_WARNING_LENGTH and not force:
        print(warning)
        return 1
    if warning is not None and len(geohash) == _MEDIUM_PRECISION_GEOHASH_WARNING_LENGTH and not force:
        print(warning)
    if int(args.pow) < 0:
        print("pow difficulty must be non-negative")
        return 1
    try:
        _send_daemon_command(
            runtime,
            {
                "command": "geo_send",
                "geohash": geohash,
                "content": args.message,
                "pow_difficulty": int(args.pow),
                "teleported": bool(args.teleport),
            },
        )
    except RuntimeError as exc:
        print(str(exc))
        return 1
    print(f"queued geohash message to #{geohash}")
    return 0


def run_geo_watch_add(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        geohash = _normalize_geo_target(args.geohash)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    warning = _geohash_privacy_warning(geohash)
    force = bool(getattr(args, "force", False))
    if warning is not None and len(geohash) >= _HIGH_PRECISION_GEOHASH_WARNING_LENGTH and not force:
        print(warning)
        return 1
    if warning is not None and len(geohash) == _MEDIUM_PRECISION_GEOHASH_WARNING_LENGTH and not force:
        print(warning)
    store = StateStore(runtime.state_path)
    state = store.load()
    watched = sorted(set(state.watched_geohashes) | {geohash})
    state.watched_geohashes = watched
    store.save(state)
    try:
        _send_daemon_command(runtime, {"command": "geo_watch_set", "geohashes": watched})
    except RuntimeError:
        pass
    print(f"watching #{geohash}")
    return 0


def run_geo_watch_remove(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    try:
        geohash = _normalize_geo_target(args.geohash)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    store = StateStore(runtime.state_path)
    state = store.load()
    watched = [item for item in state.watched_geohashes if item != geohash]
    state.watched_geohashes = watched
    store.save(state)
    try:
        _send_daemon_command(runtime, {"command": "geo_watch_set", "geohashes": watched})
    except RuntimeError:
        pass
    print(f"stopped watching #{geohash}")
    return 0


def run_geo_watch_list(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    state = StateStore(runtime.state_path).load()
    if args.json:
        print(json.dumps(state.watched_geohashes, indent=2))
        return 0
    if not state.watched_geohashes:
        print("no geohash watches configured")
        return 0
    for geohash in state.watched_geohashes:
        print(f"#{geohash}")
    return 0


def run_dm_inbox(args: argparse.Namespace) -> int:
    runtime = RuntimePaths()
    peer_rows = PeerStore(runtime.peers_path).read_all()
    social_rows = _social_store(runtime).read_all()
    peer_index = {row["peer_id"]: row for row in peer_rows}
    for row in social_rows:
        peer_index.setdefault(str(row["peer_id"]), row)
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
            elif row.get("message_kind") in {"public_file", "private_file"}:
                summary["last_preview"] = _attachment_summary(row)
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
    try:
        conversation_id = str(_resolve_peer_or_social_target(runtime, args.target)["peer_id"])
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
        content = (
            _attachment_summary(row)
            if str(row.get("message_kind") or "") in {"public_file", "private_file"}
            else row["content"]
        )
        print(f"{row['timestamp']} <{row['sender']}> {content}")
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
