from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from bleak import BleakClient, BleakScanner

from .constants import CHARACTERISTIC_UUID, MAX_PACKET_SIZE, SERVICE_UUID, MessageType
from .crypto import Identity, peer_id_from_noise_public_key
from .dedupe import ExpiringSet, packet_cache_key
from .fragments import FragmentAssemblyBuffer, fragment_packet_sequence
from .noise import NoiseError, NoiseSession
from .protocol import (
    Announcement,
    NoisePayload,
    NoisePayloadType,
    Packet,
    PrivateMessagePacket,
    make_announce_packet,
    make_delivered_payload,
    make_leave_packet,
    make_noise_encrypted_packet,
    make_noise_handshake_packet,
    make_public_message_packet,
    make_read_receipt_payload,
)


SystemLogger = Callable[[str], None]
MessageLogger = Callable[[str, str, datetime, str | None, str | None], None]
PeerLogger = Callable[["PeerRecord"], None]
PrivateMessageLogger = Callable[[str, str, datetime, str, str], None]
ReceiptLogger = Callable[[str, str, str, datetime], None]

_MAX_PENDING_NOISE_PAYLOADS_PER_PEER = 32
_MAX_NOTIFICATION_QUEUE_SIZE = 256
_NOTIFICATION_WORKER_COUNT = 1


@dataclass(slots=True)
class PeerRecord:
    peer_id: str
    nickname: str
    noise_public_key: bytes
    signing_public_key: bytes
    addresses: set[str] = field(default_factory=set)
    active_address: str | None = None
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BLEChatTransport:
    def __init__(
        self,
        *,
        identity: Identity,
        nickname: str,
        system_log: SystemLogger,
        message_log: MessageLogger,
        peer_log: PeerLogger | None = None,
        private_message_log: PrivateMessageLogger | None = None,
        receipt_log: ReceiptLogger | None = None,
        scan_interval: float = 5.0,
        background_mode: bool = False,
    ) -> None:
        self.identity = identity
        self.nickname = nickname
        self.system_log = system_log
        self.message_log = message_log
        self.peer_log = peer_log
        self.private_message_log = private_message_log
        self.receipt_log = receipt_log
        self.scan_interval = scan_interval
        self.background_mode = background_mode
        self.clients: dict[str, BleakClient] = {}
        self.connecting: set[str] = set()
        self.known_peers: dict[str, PeerRecord] = {}
        self._noise_sessions: dict[str, NoiseSession] = {}
        self._pending_noise_payloads: dict[str, deque[bytes]] = {}
        self._recent_own_signatures: deque[str] = deque(maxlen=128)
        self._recent_seen_packets = ExpiringSet(ttl_seconds=300.0, max_entries=4000)
        self._recent_inbound_messages = ExpiringSet(ttl_seconds=300.0, max_entries=1000)
        self._fragment_buffer = FragmentAssemblyBuffer()
        self._reconnect_ready_at: dict[str, float] = {}
        self._scan_task: asyncio.Task[None] | None = None
        self._notification_queue: asyncio.Queue[tuple[str, bytes]] | None = None
        self._notification_workers: list[asyncio.Task[None]] = []
        self._scanner: BleakScanner | None = None
        self._scanner_running = False
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._stop_event.clear()
        self._notification_queue = asyncio.Queue(maxsize=_MAX_NOTIFICATION_QUEUE_SIZE)
        self._notification_workers = [
            asyncio.create_task(self._notification_worker())
            for _ in range(_NOTIFICATION_WORKER_COUNT)
        ]
        self._scanner = BleakScanner(
            detection_callback=self._handle_discovered_device,
            service_uuids=[SERVICE_UUID],
        )
        self._scan_task = asyncio.create_task(self._scan_loop())
        if self.background_mode:
            self.system_log("Scanning for BitChat peers over Bluetooth LE in background duty-cycle mode.")
        else:
            self.system_log("Scanning for BitChat peers over Bluetooth LE.")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._scan_task is not None:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        notification_workers = list(self._notification_workers)
        self._notification_workers.clear()
        for worker in notification_workers:
            worker.cancel()
        for worker in notification_workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._notification_queue = None
        await self._set_scanning(False)
        try:
            await self._broadcast_packet(make_leave_packet(self.identity))
        except Exception:
            pass
        clients = list(self.clients.items())
        self.clients.clear()
        for _, client in clients:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass

    def list_peers(self) -> list[PeerRecord]:
        return sorted(self.known_peers.values(), key=lambda peer: (peer.nickname.lower(), peer.peer_id))

    def seed_known_peers(self, rows: list[dict[str, object]]) -> None:
        for row in rows:
            peer_id = str(row.get("peer_id") or "").strip()
            if not peer_id or peer_id in self.known_peers:
                continue
            try:
                noise_public_key = bytes.fromhex(str(row.get("noise_public_key") or ""))
                signing_public_key = bytes.fromhex(str(row.get("signing_public_key") or ""))
            except ValueError:
                continue
            if len(noise_public_key) != 32 or len(signing_public_key) != 32:
                continue
            last_seen_raw = str(row.get("last_seen") or "")
            try:
                last_seen = datetime.fromisoformat(last_seen_raw)
            except ValueError:
                last_seen = datetime.now(timezone.utc)
            self.known_peers[peer_id] = PeerRecord(
                peer_id=peer_id,
                nickname=str(row.get("nickname") or peer_id[:8]),
                noise_public_key=noise_public_key,
                signing_public_key=signing_public_key,
                addresses={str(item) for item in row.get("addresses", []) if str(item)},
                last_seen=last_seen,
            )

    async def set_nickname(self, nickname: str) -> None:
        self.nickname = nickname
        await self._broadcast_packet(make_announce_packet(self.identity, nickname))

    async def send_public_message(self, content: str) -> None:
        packet = make_public_message_packet(self.identity, content)
        await self._broadcast_packet(packet)

    async def send_private_message(
        self,
        peer_id: str,
        content: str,
        *,
        message_id: str | None = None,
    ) -> str:
        if peer_id not in self.known_peers:
            raise ValueError(f"unknown peer_id: {peer_id}")
        resolved_message_id = message_id or str(uuid4())
        typed_payload = NoisePayload(
            payload_type=NoisePayloadType.PRIVATE_MESSAGE,
            payload=PrivateMessagePacket(message_id=resolved_message_id, content=content).encode(),
        ).encode()
        await self._send_noise_payload(peer_id, typed_payload)
        return resolved_message_id

    async def send_read_receipt(self, peer_id: str, message_id: str) -> None:
        if peer_id not in self.known_peers:
            raise ValueError(f"unknown peer_id: {peer_id}")
        await self._send_noise_payload(peer_id, make_read_receipt_payload(message_id))

    async def _scan_loop(self) -> None:
        try:
            if not self.background_mode:
                await self._set_scanning(True)
                await self._stop_event.wait()
                return

            scan_on_duration = max(1.0, self.scan_interval)
            scan_off_duration = max(scan_on_duration * 2.0, 10.0)
            while not self._stop_event.is_set():
                await self._set_scanning(True)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=scan_on_duration)
                    break
                except asyncio.TimeoutError:
                    pass

                await self._set_scanning(False)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=scan_off_duration)
                    break
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.system_log(f"Scan loop failed: {exc}")

    async def _set_scanning(self, enabled: bool) -> None:
        if self._scanner is None:
            return
        if enabled:
            if self._scanner_running:
                return
            try:
                await self._scanner.start()
                self._scanner_running = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.system_log(f"Failed to start BLE scan: {exc}")
        else:
            if not self._scanner_running:
                return
            try:
                await self._scanner.stop()
            except Exception:
                pass
            finally:
                self._scanner_running = False

    def _handle_discovered_device(self, device, _advertisement_data) -> None:
        address = getattr(device, "address", None)
        if not address or address in self.clients or address in self.connecting:
            return
        if not self._can_attempt_reconnect(address):
            return
        self.connecting.add(address)
        asyncio.create_task(self._connect_device(device))

    async def _connect_device(self, device) -> None:
        address = device.address
        client = BleakClient(device, disconnected_callback=lambda _: self._on_disconnect(address))
        try:
            await client.connect()
            self._reconnect_ready_at.pop(address, None)
            characteristic = client.services.get_characteristic(CHARACTERISTIC_UUID)
            if characteristic is None:
                raise RuntimeError("peer missing BitChat characteristic")
            await client.start_notify(
                CHARACTERISTIC_UUID,
                lambda _sender, data, addr=address: self._enqueue_notification(addr, bytes(data)),
            )
            self.clients[address] = client
            self.system_log(f"Connected to BitChat peer at {address}.")
            await self._write_packet(client, make_announce_packet(self.identity, self.nickname))
        except Exception as exc:
            self._record_reconnect_backoff(address, multiplier=1.0)
            self.system_log(f"Failed to connect to {address}: {exc}")
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass
        finally:
            self.connecting.discard(address)

    def _on_disconnect(self, address: str) -> None:
        self._record_reconnect_backoff(address, multiplier=1.0 if self.background_mode else 0.4)
        if self.clients.pop(address, None) is not None:
            self.system_log(f"Disconnected from {address}.")

    async def _handle_notification(self, address: str, data: bytes) -> None:
        try:
            packet = Packet.decode(data)
        except Exception:
            return

        await self._handle_decoded_packet(address, packet)

    def _enqueue_notification(self, address: str, data: bytes) -> None:
        if self._notification_queue is None:
            asyncio.create_task(self._handle_notification(address, data))
            return
        try:
            self._notification_queue.put_nowait((address, data))
        except asyncio.QueueFull:
            self.system_log(f"Dropped inbound packet from {address}: notification queue is full.")

    async def _notification_worker(self) -> None:
        if self._notification_queue is None:
            return
        while True:
            address, data = await self._notification_queue.get()
            try:
                await self._handle_notification(address, data)
            finally:
                self._notification_queue.task_done()

    async def _handle_decoded_packet(self, address: str, packet: Packet) -> None:
        packet_key = packet_cache_key(packet)
        if self._recent_seen_packets.mark_seen(packet_key):
            return

        if packet.packet_type == MessageType.FRAGMENT:
            try:
                reassembled = self._fragment_buffer.append(packet)
            except Exception as exc:
                self.system_log(f"Ignored invalid fragment stream from {address}: {exc}")
                return
            if reassembled is not None:
                await self._handle_decoded_packet(address, reassembled)
            return

        signature_hex = packet.signature.hex() if packet.signature else None
        if packet.sender_id.hex() == self.identity.peer_id or (
            signature_hex is not None and signature_hex in self._recent_own_signatures
        ):
            return

        await self._relay_if_needed(address, packet)

        if packet.packet_type == MessageType.ANNOUNCE:
            await self._handle_announce(address, packet)
            return
        if packet.packet_type == MessageType.MESSAGE:
            await self._handle_message(packet)
            return
        if packet.packet_type == MessageType.NOISE_HANDSHAKE:
            await self._handle_noise_handshake(packet)
            return
        if packet.packet_type == MessageType.NOISE_ENCRYPTED:
            await self._handle_noise_encrypted(packet)
            return
        if packet.packet_type == MessageType.LEAVE:
            self._handle_leave(packet)
            return

    async def _relay_if_needed(self, ingress_address: str, packet: Packet) -> None:
        if packet.ttl <= 1:
            return
        recipient_is_self = packet.recipient_id is not None and packet.recipient_id.hex() == self.identity.peer_id
        if recipient_is_self:
            return
        if packet.packet_type == MessageType.REQUEST_SYNC:
            return

        relayable_types = {
            MessageType.ANNOUNCE,
            MessageType.MESSAGE,
            MessageType.LEAVE,
            MessageType.NOISE_HANDSHAKE,
            MessageType.NOISE_ENCRYPTED,
        }
        if packet.packet_type not in relayable_types:
            return

        relayed_packet = Packet(
            packet_type=packet.packet_type,
            sender_id=packet.sender_id,
            recipient_id=packet.recipient_id,
            timestamp_ms=packet.timestamp_ms,
            payload=packet.payload,
            signature=packet.signature,
            ttl=packet.ttl - 1,
            version=packet.version,
        )
        await self._broadcast_packet(relayed_packet, exclude_addresses={ingress_address})

    async def _handle_announce(self, address: str, packet: Packet) -> None:
        try:
            announcement = Announcement.decode(packet.payload)
        except Exception:
            return
        derived_peer_id = peer_id_from_noise_public_key(announcement.noise_public_key)
        claimed_peer_id = packet.sender_id.hex()
        if derived_peer_id != claimed_peer_id:
            self.system_log(f"Ignored announce from {address}: peer ID did not match the noise key.")
            return
        if not packet.verify_signature(announcement.signing_public_key):
            self.system_log(f"Ignored announce from {address}: invalid packet signature.")
            return

        existing = self.known_peers.get(claimed_peer_id)
        is_new = existing is None
        peer = existing or PeerRecord(
            peer_id=claimed_peer_id,
            nickname=announcement.nickname,
            noise_public_key=announcement.noise_public_key,
            signing_public_key=announcement.signing_public_key,
        )
        peer.nickname = announcement.nickname
        peer.noise_public_key = announcement.noise_public_key
        peer.signing_public_key = announcement.signing_public_key
        peer.last_seen = datetime.fromtimestamp(packet.timestamp_ms / 1000, tz=timezone.utc)
        peer.addresses.add(address)
        peer.active_address = address
        self.known_peers[claimed_peer_id] = peer
        if self.peer_log is not None:
            self.peer_log(peer)
        if is_new:
            self.system_log(f"Peer online: {peer.nickname} ({peer.peer_id}).")
        if claimed_peer_id in self._pending_noise_payloads:
            await self._initiate_noise_handshake(claimed_peer_id)

    async def _handle_message(self, packet: Packet) -> None:
        peer_id = packet.sender_id.hex()
        peer = self.known_peers.get(peer_id)
        if peer is None:
            return
        if not packet.verify_signature(peer.signing_public_key):
            self.system_log(f"Ignored message from {peer.nickname}: invalid signature.")
            return
        message_id = packet_cache_key(packet)
        if self._recent_inbound_messages.mark_seen(message_id):
            return
        try:
            content = packet.payload.decode("utf-8")
        except UnicodeDecodeError:
            content = packet.payload.decode("utf-8", errors="replace")
        peer.last_seen = datetime.fromtimestamp(packet.timestamp_ms / 1000, tz=timezone.utc)
        self.message_log(peer.nickname, content, peer.last_seen, peer.peer_id, message_id)

    async def _handle_noise_handshake(self, packet: Packet) -> None:
        if packet.recipient_id is None or packet.recipient_id.hex() != self.identity.peer_id:
            return
        peer_id = packet.sender_id.hex()
        session = self._noise_sessions.get(peer_id)
        if session is None or session.is_established():
            session = NoiseSession(
                peer_id=peer_id,
                role="responder",
                local_static_private_key=self.identity.noise_private_key,
            )
            self._noise_sessions[peer_id] = session

        try:
            response = session.process_handshake_message(packet.payload)
        except NoiseError as exc:
            self.system_log(f"Ignored noise handshake from {peer_id}: {exc}")
            return

        if response:
            await self._send_packet_to_peer(
                peer_id,
                make_noise_handshake_packet(self.identity, peer_id, response),
            )
        if session.is_established():
            await self._flush_pending_noise_payloads(peer_id)

    async def _handle_noise_encrypted(self, packet: Packet) -> None:
        if packet.recipient_id is None or packet.recipient_id.hex() != self.identity.peer_id:
            return
        peer_id = packet.sender_id.hex()
        session = self._noise_sessions.get(peer_id)
        if session is None or not session.is_established():
            await self._initiate_noise_handshake(peer_id)
            return

        try:
            decrypted = session.decrypt(packet.payload)
        except NoiseError as exc:
            self.system_log(f"Failed to decrypt private payload from {peer_id}: {exc}")
            session.reset()
            self._noise_sessions.pop(peer_id, None)
            await self._initiate_noise_handshake(peer_id)
            return

        peer = self.known_peers.get(peer_id)
        timestamp = datetime.fromtimestamp(packet.timestamp_ms / 1000, tz=timezone.utc)
        if peer is not None:
            peer.last_seen = timestamp
            if self.peer_log is not None:
                self.peer_log(peer)

        try:
            noise_payload = NoisePayload.decode(decrypted)
        except ValueError as exc:
            self.system_log(f"Ignored invalid private payload from {peer_id}: {exc}")
            return

        if noise_payload.payload_type == NoisePayloadType.PRIVATE_MESSAGE:
            await self._handle_private_message_payload(peer_id, noise_payload.payload, timestamp)
            return
        if noise_payload.payload_type == NoisePayloadType.DELIVERED:
            self._handle_receipt_payload(peer_id, "delivered", noise_payload.payload, timestamp)
            return
        if noise_payload.payload_type == NoisePayloadType.READ_RECEIPT:
            self._handle_receipt_payload(peer_id, "read_receipt", noise_payload.payload, timestamp)
            return

    async def _handle_private_message_payload(
        self,
        peer_id: str,
        payload: bytes,
        timestamp: datetime,
    ) -> None:
        try:
            private_message = PrivateMessagePacket.decode(payload)
        except ValueError as exc:
            self.system_log(f"Ignored invalid private message from {peer_id}: {exc}")
            return
        dedupe_key = f"dm:{peer_id}:{private_message.message_id}"
        if self._recent_inbound_messages.mark_seen(dedupe_key):
            return

        peer = self.known_peers.get(peer_id)
        sender = peer.nickname if peer is not None else peer_id[:8]
        if self.private_message_log is not None:
            self.private_message_log(
                sender,
                private_message.content,
                timestamp,
                peer_id,
                private_message.message_id,
            )
        else:
            self.system_log(f"[dm] <{sender}> {private_message.content}")

        await self._send_noise_payload(peer_id, make_delivered_payload(private_message.message_id))

    def _handle_receipt_payload(
        self,
        peer_id: str,
        receipt_type: str,
        payload: bytes,
        timestamp: datetime,
    ) -> None:
        try:
            message_id = payload.decode("utf-8")
        except UnicodeDecodeError:
            self.system_log(f"Ignored invalid {receipt_type} payload from {peer_id}.")
            return
        dedupe_key = f"receipt:{receipt_type}:{peer_id}:{message_id}"
        if self._recent_inbound_messages.mark_seen(dedupe_key):
            return
        if self.receipt_log is not None:
            self.receipt_log(peer_id, receipt_type, message_id, timestamp)

    def _handle_leave(self, packet: Packet) -> None:
        peer_id = packet.sender_id.hex()
        peer = self.known_peers.get(peer_id)
        if peer is not None:
            self.system_log(f"Peer left: {peer.nickname} ({peer.peer_id}).")

    async def _send_noise_payload(self, peer_id: str, typed_payload: bytes) -> None:
        session = self._noise_sessions.get(peer_id)
        if session is not None and session.is_established():
            try:
                await self._send_encrypted_payload_now(peer_id, typed_payload)
                return
            except NoiseError:
                session.reset()
                self._noise_sessions.pop(peer_id, None)

        self._queue_noise_payload(peer_id, typed_payload)
        await self._initiate_noise_handshake(peer_id)

    def _queue_noise_payload(self, peer_id: str, typed_payload: bytes) -> None:
        queue = self._pending_noise_payloads.setdefault(
            peer_id,
            deque(maxlen=_MAX_PENDING_NOISE_PAYLOADS_PER_PEER),
        )
        if len(queue) == queue.maxlen:
            queue.popleft()
            self.system_log(f"Dropped oldest pending private payload for {peer_id} due to queue limit.")
        queue.append(typed_payload)

    async def _flush_pending_noise_payloads(self, peer_id: str) -> None:
        queue = self._pending_noise_payloads.get(peer_id)
        if not queue:
            return
        while queue:
            typed_payload = queue.popleft()
            try:
                await self._send_encrypted_payload_now(peer_id, typed_payload)
            except NoiseError as exc:
                queue.appendleft(typed_payload)
                self.system_log(f"Paused pending private payload flush for {peer_id}: {exc}")
                break
        if not queue:
            self._pending_noise_payloads.pop(peer_id, None)

    async def _send_encrypted_payload_now(self, peer_id: str, typed_payload: bytes) -> None:
        session = self._noise_sessions.get(peer_id)
        if session is None or not session.is_established():
            raise NoiseError("session is not established")
        encrypted = session.encrypt(typed_payload)
        sent = await self._send_packet_to_peer(
            peer_id,
            make_noise_encrypted_packet(self.identity, peer_id, encrypted),
        )
        if sent is False:
            raise NoiseError("no connected link to peer")

    async def _initiate_noise_handshake(self, peer_id: str) -> None:
        session = self._noise_sessions.get(peer_id)
        if session is None:
            session = NoiseSession(
                peer_id=peer_id,
                role="initiator",
                local_static_private_key=self.identity.noise_private_key,
            )
            self._noise_sessions[peer_id] = session
        if session.is_established() or session.is_handshaking():
            return
        handshake_payload = session.start_handshake()
        if handshake_payload:
            sent = await self._send_packet_to_peer(
                peer_id,
                make_noise_handshake_packet(self.identity, peer_id, handshake_payload),
            )
            if sent is False:
                session.reset()
                self._noise_sessions.pop(peer_id, None)

    async def _send_packet_to_peer(self, peer_id: str, packet: Packet) -> bool:
        peer = self.known_peers.get(peer_id)
        if peer is None:
            self.system_log(f"Unknown peer_id for direct packet: {peer_id}")
            return False
        target_address = None
        if peer.active_address is not None and peer.active_address in self.clients:
            target_address = peer.active_address
        else:
            for address in sorted(peer.addresses):
                if address in self.clients:
                    target_address = address
                    break
        if target_address is None:
            self.system_log(f"No connected BitChat link to {peer.nickname} ({peer.peer_id}).")
            return False
        await self._write_packet(self.clients[target_address], packet)
        return True

    async def _broadcast_packet(self, packet: Packet, *, exclude_addresses: set[str] | None = None) -> None:
        excluded = exclude_addresses or set()
        clients = [client for address, client in self.clients.items() if address not in excluded]
        if not clients:
            if not excluded:
                self.system_log("No connected peers yet.")
            return
        await asyncio.gather(*(self._write_packet(client, packet) for client in clients), return_exceptions=True)

    async def _write_packet(self, client: BleakClient, packet: Packet) -> None:
        data = packet.encode(padding=False)
        if packet.signature is not None:
            self._recent_own_signatures.append(packet.signature.hex())
        if len(data) <= MAX_PACKET_SIZE:
            await client.write_gatt_char(CHARACTERISTIC_UUID, data, response=False)
            return

        fragments = fragment_packet_sequence(packet)
        for index, fragment in enumerate(fragments):
            await client.write_gatt_char(CHARACTERISTIC_UUID, fragment.encode(padding=False), response=False)
            if index + 1 < len(fragments):
                await asyncio.sleep(0.03)

    def _can_attempt_reconnect(self, address: str) -> bool:
        return time.monotonic() >= self._reconnect_ready_at.get(address, 0.0)

    def _record_reconnect_backoff(self, address: str, *, multiplier: float) -> None:
        base_delay = max(self.scan_interval, 2.0)
        delay = max(base_delay * multiplier, 2.0)
        if self.background_mode:
            delay = max(delay, 10.0)
        self._reconnect_ready_at[address] = time.monotonic() + delay
