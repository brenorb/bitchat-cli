from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from bleak import BleakClient, BleakScanner

from .constants import CHARACTERISTIC_UUID, MAX_PACKET_SIZE, SERVICE_UUID, MessageType
from .crypto import Identity, peer_id_from_noise_public_key
from .dedupe import ExpiringSet, packet_cache_key
from .files import load_outgoing_file, save_incoming_file, transfer_id_for_bytes
from .fragments import FragmentAssemblyBuffer, fragment_packet_sequence
from .noise import NoiseError, NoiseSession
from .peripheral import NullPeripheralEndpoint, PeripheralEndpoint
from .protocol import (
    Announcement,
    FilePacket,
    NoisePayload,
    NoisePayloadType,
    Packet,
    PrivateMessagePacket,
    make_file_transfer_packet,
    make_announce_packet,
    make_delivered_payload,
    make_leave_packet,
    make_noise_encrypted_packet,
    make_noise_handshake_packet,
    make_public_message_packet,
    make_read_receipt_payload,
)
from .state import RuntimePaths
from .sync import GCSFilter, RequestSyncManager, RequestSyncPacket, SyncTypeFlags, packet_sync_id


SystemLogger = Callable[[str], None]
MessageLogger = Callable[[str, str, datetime, str | None, str | None], None]
PeerLogger = Callable[["PeerRecord"], None]
PrivateMessageLogger = Callable[[str, str, datetime, str, str], None]
ReceiptLogger = Callable[[str, str, str, datetime], None]
PublicFileLogger = Callable[[str, str, str, str, int, datetime, str | None, str], None]
PrivateFileLogger = Callable[[str, str, str, str, int, datetime, str, str], None]

_MAX_PENDING_NOISE_PAYLOADS_PER_PEER = 32
_MAX_NOTIFICATION_QUEUE_SIZE = 256
_NOTIFICATION_WORKER_COUNT = 1
_MAX_CONNECT_QUEUE_SIZE = 64
_CONNECT_WORKER_COUNT = 2
_RUNTIME_PRUNE_INTERVAL_SECONDS = 30.0
_ANNOUNCE_INTERVAL_SECONDS = 30.0
_STALE_PEER_RETENTION_SECONDS = 900.0
_STALE_RECONNECT_RETENTION_SECONDS = 300.0
_MAX_FUTURE_TIMESTAMP_SKEW_SECONDS = 120.0
_MAX_BROADCAST_PACKET_AGE_SECONDS = 900.0
_SYNC_PUBLIC_MESSAGE_CAPACITY = 256
_SYNC_FRAGMENT_CAPACITY = 512
_SYNC_FILE_TRANSFER_CAPACITY = 256
_SUPPORTED_SYNC_TYPES = SyncTypeFlags.from_message_types(
    [MessageType.ANNOUNCE, MessageType.MESSAGE, MessageType.FRAGMENT, MessageType.FILE_TRANSFER]
)
_DEFAULT_REQUEST_SYNC_TYPES = SyncTypeFlags.from_message_types(
    [MessageType.ANNOUNCE, MessageType.MESSAGE, MessageType.FRAGMENT, MessageType.FILE_TRANSFER]
)
_BACKGROUND_CONNECTED_SCAN_OFF_SECONDS = 30.0
_BACKGROUND_IDLE_SCAN_OFF_SECONDS = 60.0
_BACKGROUND_DISCOVERY_GRACE_SECONDS = 20.0
_BACKGROUND_IDLE_THRESHOLD_SECONDS = 60.0
_MAX_BACKGROUND_SCAN_OFF_SECONDS = 120.0
_MAX_RECONNECT_BACKOFF_SECONDS = 120.0
_STABLE_CONNECTION_RESET_SECONDS = 60.0


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
        public_file_log: PublicFileLogger | None = None,
        private_file_log: PrivateFileLogger | None = None,
        scan_interval: float = 5.0,
        background_mode: bool = False,
        peripheral_endpoint: PeripheralEndpoint | None = None,
        file_runtime_paths: RuntimePaths | None = None,
    ) -> None:
        self.identity = identity
        self.nickname = nickname
        self.system_log = system_log
        self.message_log = message_log
        self.peer_log = peer_log
        self.private_message_log = private_message_log
        self.receipt_log = receipt_log
        self.public_file_log = public_file_log
        self.private_file_log = private_file_log
        self.scan_interval = scan_interval
        self.background_mode = background_mode
        self.peripheral_endpoint = peripheral_endpoint or NullPeripheralEndpoint()
        self.file_runtime_paths = file_runtime_paths or RuntimePaths()
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
        self._sync_announcements_by_peer: dict[str, Packet] = {}
        self._sync_public_messages: dict[str, Packet] = {}
        self._sync_fragments: dict[str, Packet] = {}
        self._sync_public_file_transfers: dict[str, Packet] = {}
        self._request_sync_manager = RequestSyncManager()
        self._scan_task: asyncio.Task[None] | None = None
        self._announce_task: asyncio.Task[None] | None = None
        self._connect_queue: asyncio.Queue[object] | None = None
        self._connect_workers: list[asyncio.Task[None]] = []
        self._queued_connect_addresses: set[str] = set()
        self._notification_queue: asyncio.Queue[tuple[str, bytes]] | None = None
        self._notification_workers: list[asyncio.Task[None]] = []
        self._scanner: BleakScanner | None = None
        self._scanner_running = False
        self._stop_event = asyncio.Event()
        self._last_runtime_prune_at = 0.0
        self._last_mesh_activity_at = time.monotonic()
        self._reconnect_attempts: dict[str, int] = {}
        self._connected_at: dict[str, float] = {}

    async def start(self) -> None:
        self._stop_event.clear()
        self.peripheral_endpoint.start()
        self._connect_queue = asyncio.Queue(maxsize=_MAX_CONNECT_QUEUE_SIZE)
        self._connect_workers = [
            asyncio.create_task(self._connect_worker())
            for _ in range(_CONNECT_WORKER_COUNT)
        ]
        self._notification_queue = asyncio.Queue(maxsize=_MAX_NOTIFICATION_QUEUE_SIZE)
        self._notification_workers = [
            asyncio.create_task(self._notification_worker())
            for _ in range(_NOTIFICATION_WORKER_COUNT)
        ]
        self._announce_task = asyncio.create_task(self._announce_loop())
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
            self._scan_task = None
        if self._announce_task is not None:
            self._announce_task.cancel()
            try:
                await self._announce_task
            except asyncio.CancelledError:
                pass
            self._announce_task = None
        connect_workers = list(self._connect_workers)
        self._connect_workers.clear()
        for worker in connect_workers:
            worker.cancel()
        for worker in connect_workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._connect_queue = None
        self._queued_connect_addresses.clear()
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
        self.peripheral_endpoint.stop()

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
        packet = self._make_announce_packet(nickname=nickname)
        self._remember_sync_packet(packet)
        await self._broadcast_packet(packet)

    async def send_public_message(self, content: str) -> None:
        packet = make_public_message_packet(self.identity, content)
        self._remember_sync_packet(packet)
        await self._broadcast_packet(packet)

    async def send_public_file(self, path: Path) -> str:
        file_packet = load_outgoing_file(path)
        transfer_id = transfer_id_for_bytes(file_packet.encode())
        packet = make_file_transfer_packet(self.identity, file_packet)
        self._remember_sync_packet(packet)
        await self._broadcast_packet(packet)
        return transfer_id

    async def send_request_sync(
        self,
        peer_id: str,
        *,
        types: SyncTypeFlags = _DEFAULT_REQUEST_SYNC_TYPES,
    ) -> bool:
        if peer_id not in self.known_peers:
            raise ValueError(f"unknown peer_id: {peer_id}")
        supported_types = self._supported_sync_types(types)
        if supported_types.is_empty():
            return False
        ids = self._sync_candidate_ids(supported_types)
        p, m, data = GCSFilter.build_filter(ids=ids, max_bytes=128, target_fpr=0.01)
        request = RequestSyncPacket(p=p, m=m, data=data, types=supported_types)
        self._request_sync_manager.register_request(peer_id)
        packet = Packet(
            packet_type=MessageType.REQUEST_SYNC,
            sender_id=bytes.fromhex(self.identity.peer_id),
            recipient_id=bytes.fromhex(peer_id),
            payload=request.encode(),
            ttl=0,
        ).sign(self.identity)
        sent = await self._send_packet_to_peer(peer_id, packet)
        return bool(sent)

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

    async def send_private_file(self, peer_id: str, path: Path) -> str:
        if peer_id not in self.known_peers:
            raise ValueError(f"unknown peer_id: {peer_id}")
        file_packet = load_outgoing_file(path)
        encoded = file_packet.encode()
        transfer_id = transfer_id_for_bytes(encoded)
        typed_payload = NoisePayload(
            payload_type=NoisePayloadType.FILE_TRANSFER,
            payload=encoded,
        ).encode()
        await self._send_noise_payload(peer_id, typed_payload)
        return transfer_id

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

            while not self._stop_event.is_set():
                scan_on_duration, scan_off_duration = self._background_scan_durations()
                self._prune_runtime_state()
                await self._set_scanning(True)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=scan_on_duration)
                    break
                except asyncio.TimeoutError:
                    pass

                if scan_off_duration <= 0.0:
                    continue
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

    async def _announce_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=_ANNOUNCE_INTERVAL_SECONDS,
                    )
                    break
                except asyncio.TimeoutError:
                    if not self.clients and self.peripheral_endpoint.subscriber_count() == 0:
                        continue
                    packet = self._make_announce_packet()
                    self._remember_sync_packet(packet)
                    await self._broadcast_packet(packet)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.system_log(f"Announce loop failed: {exc}")

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
        self._prune_runtime_state()
        address = getattr(device, "address", None)
        if (
            not address
            or address in self.clients
            or address in self.connecting
            or address in self._queued_connect_addresses
        ):
            return
        if not self._can_attempt_reconnect(address):
            return
        self._enqueue_connect_candidate(device)

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
            self._connected_at[address] = time.monotonic()
            self._mark_mesh_activity()
            self.system_log(f"Connected to BitChat peer at {address}.")
            packet = self._make_announce_packet()
            self._remember_sync_packet(packet)
            await self._write_packet(client, packet)
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
        connected_at = self._connected_at.pop(address, None)
        if connected_at is not None and time.monotonic() - connected_at >= _STABLE_CONNECTION_RESET_SECONDS:
            self._reconnect_attempts.pop(address, None)
        self._record_reconnect_backoff(address, multiplier=1.0 if self.background_mode else 0.4)
        if self.clients.pop(address, None) is not None:
            self._remove_address_from_peers(address)
            self.system_log(f"Disconnected from {address}.")

    async def _handle_notification(self, address: str, data: bytes) -> None:
        self._prune_runtime_state()
        self._mark_mesh_activity()
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

    async def _handle_peripheral_subscribe(self, address: str) -> None:
        packet = self._make_announce_packet()
        self._remember_sync_packet(packet)
        self.peripheral_endpoint.send(packet.encode(padding=False), target_addresses={address})

    def _handle_peripheral_unsubscribe(self, address: str) -> None:
        self._remove_address_from_peers(address)

    def _enqueue_connect_candidate(self, device) -> None:
        address = getattr(device, "address", None)
        if not address:
            return
        if self._connect_queue is None:
            self.connecting.add(address)
            asyncio.create_task(self._connect_device(device))
            return
        try:
            self._connect_queue.put_nowait(device)
            self._queued_connect_addresses.add(address)
        except asyncio.QueueFull:
            self.system_log(f"Dropped connect candidate {address}: connect queue is full.")

    async def _connect_worker(self) -> None:
        if self._connect_queue is None:
            return
        while True:
            device = await self._connect_queue.get()
            address = getattr(device, "address", None)
            if address is not None:
                self._queued_connect_addresses.discard(address)
                self.connecting.add(address)
            try:
                await self._connect_device(device)
            finally:
                self._connect_queue.task_done()

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
        ingress_peer = self._peer_for_address(address)
        claimed_sender_id = packet.sender_id.hex()
        signature_hex = packet.signature.hex() if packet.signature else None
        allow_self_authored_rsr = False
        if packet.is_rsr:
            validation_peer_id = ingress_peer.peer_id if ingress_peer is not None else claimed_sender_id
            if not self._request_sync_manager.is_valid_response(validation_peer_id, is_rsr=True):
                self.system_log(f"Ignored unsolicited sync response from {validation_peer_id}.")
                return
            allow_self_authored_rsr = (
                packet.ttl == 0
                and ingress_peer is not None
                and claimed_sender_id == self.identity.peer_id
            )
        if (
            claimed_sender_id == self.identity.peer_id
            or (signature_hex is not None and signature_hex in self._recent_own_signatures)
        ) and not allow_self_authored_rsr:
            return

        if not packet.is_rsr:
            rejection_reason = self._ingress_time_rejection(packet)
            if rejection_reason is not None:
                self.system_log(rejection_reason)
                return
        elif ingress_peer is None and claimed_sender_id != self.identity.peer_id:
            self.system_log(f"Ignored sync response from unknown ingress address {address}.")
            return

        packet_key = packet_cache_key(packet)
        if self._recent_seen_packets.mark_seen(packet_key):
            return

        if packet.packet_type == MessageType.FRAGMENT:
            self._remember_sync_packet(packet)
            await self._relay_if_needed(address, packet)
            try:
                reassembled = self._fragment_buffer.append(packet)
            except Exception as exc:
                self.system_log(f"Ignored invalid fragment stream from {address}: {exc}")
                return
            if reassembled is not None:
                await self._handle_decoded_packet(address, self._local_only_packet(reassembled))
            return

        await self._relay_if_needed(address, packet)

        if packet.packet_type == MessageType.ANNOUNCE:
            await self._handle_announce(address, packet)
            return
        if packet.packet_type == MessageType.MESSAGE:
            await self._handle_message(packet)
            return
        if packet.packet_type == MessageType.FILE_TRANSFER:
            await self._handle_file_transfer(packet)
            return
        if packet.packet_type == MessageType.NOISE_HANDSHAKE:
            await self._handle_noise_handshake(packet)
            return
        if packet.packet_type == MessageType.NOISE_ENCRYPTED:
            await self._handle_noise_encrypted(packet)
            return
        if packet.packet_type == MessageType.REQUEST_SYNC:
            await self._handle_request_sync(packet)
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
            MessageType.FILE_TRANSFER,
            MessageType.LEAVE,
            MessageType.NOISE_HANDSHAKE,
            MessageType.NOISE_ENCRYPTED,
            MessageType.FRAGMENT,
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
            is_rsr=packet.is_rsr,
        )
        await self._broadcast_packet(relayed_packet, exclude_addresses={ingress_address})

    def _local_only_packet(self, packet: Packet) -> Packet:
        return Packet(
            packet_type=packet.packet_type,
            sender_id=packet.sender_id,
            recipient_id=packet.recipient_id,
            timestamp_ms=packet.timestamp_ms,
            payload=packet.payload,
            signature=packet.signature,
            ttl=0,
            version=packet.version,
            is_rsr=packet.is_rsr,
        )

    def _ingress_time_rejection(self, packet: Packet, *, now: datetime | None = None) -> str | None:
        current = now or datetime.now(timezone.utc)
        packet_ts = datetime.fromtimestamp(packet.timestamp_ms / 1000, tz=timezone.utc)
        future_skew_seconds = (packet_ts - current).total_seconds()
        if future_skew_seconds > _MAX_FUTURE_TIMESTAMP_SKEW_SECONDS:
            return (
                f"Ignored packet from {packet.sender_id.hex()}: timestamp skew "
                f"{int(future_skew_seconds * 1000)}ms exceeds {int(_MAX_FUTURE_TIMESTAMP_SKEW_SECONDS * 1000)}ms."
            )

        packet_age_seconds = (current - packet_ts).total_seconds()
        if packet.packet_type in {MessageType.ANNOUNCE, MessageType.MESSAGE, MessageType.FILE_TRANSFER} and (
            packet.recipient_id is None or packet.packet_type == MessageType.ANNOUNCE
        ):
            if packet_age_seconds > _MAX_BROADCAST_PACKET_AGE_SECONDS:
                return (
                    f"Ignored stale {MessageType(packet.packet_type).name.lower()} from {packet.sender_id.hex()}: "
                    f"age {int(packet_age_seconds)}s exceeds {int(_MAX_BROADCAST_PACKET_AGE_SECONDS)}s."
                )
        return None

    def _make_announce_packet(self, *, nickname: str | None = None) -> Packet:
        direct_neighbors = [
            bytes.fromhex(peer.peer_id)
            for peer in sorted(self.known_peers.values(), key=lambda item: item.peer_id)[:10]
            if peer.active_address is not None and (
                peer.active_address in self.clients or peer.active_address.startswith("peripheral:")
            )
        ]
        return make_announce_packet(
            self.identity,
            nickname or self.nickname,
            direct_neighbors=direct_neighbors,
        )

    def _supported_sync_types(self, types: SyncTypeFlags) -> SyncTypeFlags:
        return types.intersect(_SUPPORTED_SYNC_TYPES)

    def _peer_for_address(self, address: str) -> PeerRecord | None:
        for peer in self.known_peers.values():
            if peer.active_address == address:
                return peer
        for peer in self.known_peers.values():
            if address in peer.addresses:
                return peer
        return None

    async def _handle_announce(self, address: str, packet: Packet) -> None:
        if packet.sender_id.hex() == self.identity.peer_id and packet.is_rsr:
            if not packet.verify_signature(self.identity.signing_public_key):
                self.system_log(f"Ignored replayed self announce from {address}: invalid packet signature.")
                return
            self._remember_sync_packet(packet)
            return
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
        was_reconnected = existing is not None and existing.active_address is None
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
        self._remember_sync_packet(packet)
        if self.peer_log is not None:
            self.peer_log(peer)
        if is_new:
            self.system_log(f"Peer online: {peer.nickname} ({peer.peer_id}).")
        if is_new or was_reconnected:
            await self.send_request_sync(claimed_peer_id)
        if claimed_peer_id in self._pending_noise_payloads:
            await self._initiate_noise_handshake(claimed_peer_id)

    async def _handle_message(self, packet: Packet) -> None:
        peer_id = packet.sender_id.hex()
        peer = self.known_peers.get(peer_id)
        sender_nickname: str
        sender_key: bytes
        if peer_id == self.identity.peer_id:
            sender_nickname = self.nickname
            sender_key = self.identity.signing_public_key
        else:
            if peer is None:
                return
            sender_nickname = peer.nickname
            sender_key = peer.signing_public_key
        if not packet.verify_signature(sender_key):
            self.system_log(f"Ignored message from {sender_nickname}: invalid signature.")
            return
        message_id = packet_cache_key(packet)
        if self._recent_inbound_messages.mark_seen(message_id):
            return
        try:
            content = packet.payload.decode("utf-8")
        except UnicodeDecodeError:
            content = packet.payload.decode("utf-8", errors="replace")
        timestamp = datetime.fromtimestamp(packet.timestamp_ms / 1000, tz=timezone.utc)
        if peer is not None:
            peer.last_seen = timestamp
        self._remember_sync_packet(packet)
        self.message_log(sender_nickname, content, timestamp, peer_id, message_id)

    async def _handle_file_transfer(self, packet: Packet) -> None:
        peer_id = packet.sender_id.hex()
        peer = self.known_peers.get(peer_id)
        sender_nickname: str
        sender_key: bytes
        if peer_id == self.identity.peer_id:
            sender_nickname = self.nickname
            sender_key = self.identity.signing_public_key
        else:
            if peer is None:
                return
            sender_nickname = peer.nickname
            sender_key = peer.signing_public_key
        if not packet.verify_signature(sender_key):
            self.system_log(f"Ignored file transfer from {sender_nickname}: invalid signature.")
            return
        transfer_id = transfer_id_for_bytes(packet.payload)
        dedupe_key = f"file:{peer_id}:{transfer_id}"
        if self._recent_inbound_messages.mark_seen(dedupe_key):
            return
        try:
            file_packet = FilePacket.decode(packet.payload)
        except ValueError as exc:
            self.system_log(f"Ignored invalid file transfer from {sender_nickname}: {exc}")
            return
        saved_path = save_incoming_file(self.file_runtime_paths, file_packet)
        timestamp = datetime.fromtimestamp(packet.timestamp_ms / 1000, tz=timezone.utc)
        if peer is not None:
            peer.last_seen = timestamp
        self._remember_sync_packet(packet)
        if self.public_file_log is not None:
            self.public_file_log(
                sender_nickname,
                str(saved_path),
                file_packet.file_name,
                file_packet.mime_type,
                file_packet.file_size,
                timestamp,
                peer_id,
                transfer_id,
            )
            return
        self.system_log(
            f"[file] <{sender_nickname}> {file_packet.file_name} ({file_packet.file_size} bytes) -> {saved_path}"
        )

    async def _handle_request_sync(self, packet: Packet) -> None:
        if packet.recipient_id is not None and packet.recipient_id.hex() != self.identity.peer_id:
            return
        peer_id = packet.sender_id.hex()
        peer = self.known_peers.get(peer_id)
        if peer is None:
            return
        if not packet.verify_signature(peer.signing_public_key):
            self.system_log(f"Ignored request sync from {peer.nickname}: invalid signature.")
            return
        request = RequestSyncPacket.decode(packet.payload)
        if request is None:
            self.system_log(f"Ignored invalid request sync from {peer.nickname}.")
            return

        sorted_values = GCSFilter.decode_to_sorted_set(p=request.p, m=request.m, data=request.data)
        requested_types = self._supported_sync_types(request.types or SyncTypeFlags.PUBLIC_MESSAGES)
        if requested_types.is_empty():
            return
        responses = self._sync_response_candidates(requested_types)
        for cached_packet in responses:
            packet_id = packet_sync_id(cached_packet)
            candidate = GCSFilter.bucket(packet_id, modulus=request.m)
            if GCSFilter.contains(sorted_values=sorted_values, candidate=candidate):
                continue
            response = Packet(
                packet_type=cached_packet.packet_type,
                sender_id=cached_packet.sender_id,
                recipient_id=cached_packet.recipient_id,
                timestamp_ms=cached_packet.timestamp_ms,
                payload=cached_packet.payload,
                signature=cached_packet.signature,
                ttl=0,
                version=cached_packet.version,
                is_rsr=True,
            )
            await self._send_packet_to_peer(peer_id, response)

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
        if noise_payload.payload_type == NoisePayloadType.FILE_TRANSFER:
            await self._handle_private_file_payload(peer_id, noise_payload.payload, timestamp)
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

    async def _handle_private_file_payload(
        self,
        peer_id: str,
        payload: bytes,
        timestamp: datetime,
    ) -> None:
        try:
            file_packet = FilePacket.decode(payload)
        except ValueError as exc:
            self.system_log(f"Ignored invalid private file from {peer_id}: {exc}")
            return
        transfer_id = transfer_id_for_bytes(payload)
        dedupe_key = f"dm-file:{peer_id}:{transfer_id}"
        if self._recent_inbound_messages.mark_seen(dedupe_key):
            return

        peer = self.known_peers.get(peer_id)
        sender = peer.nickname if peer is not None else peer_id[:8]
        saved_path = save_incoming_file(self.file_runtime_paths, file_packet)
        if self.private_file_log is not None:
            self.private_file_log(
                sender,
                str(saved_path),
                file_packet.file_name,
                file_packet.mime_type,
                file_packet.file_size,
                timestamp,
                peer_id,
                transfer_id,
            )
        else:
            self.system_log(
                f"[dm-file] <{sender}> {file_packet.file_name} ({file_packet.file_size} bytes) -> {saved_path}"
            )

        await self._send_noise_payload(peer_id, make_delivered_payload(transfer_id))

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

    def _remove_address_from_peers(self, address: str) -> None:
        for peer in self.known_peers.values():
            if address in peer.addresses:
                peer.addresses.discard(address)
                if peer.active_address == address:
                    peer.active_address = None

    def _remember_sync_packet(self, packet: Packet) -> None:
        if packet.packet_type == MessageType.ANNOUNCE:
            self._sync_announcements_by_peer[packet.sender_id.hex()] = packet
            return
        if packet.packet_type == MessageType.FRAGMENT:
            packet_id_hex = packet_sync_id(packet).hex()
            self._sync_fragments[packet_id_hex] = packet
            while len(self._sync_fragments) > _SYNC_FRAGMENT_CAPACITY:
                oldest_key = next(iter(self._sync_fragments))
                self._sync_fragments.pop(oldest_key, None)
            return
        if packet.packet_type == MessageType.FILE_TRANSFER:
            packet_id_hex = packet_sync_id(packet).hex()
            self._sync_public_file_transfers[packet_id_hex] = packet
            while len(self._sync_public_file_transfers) > _SYNC_FILE_TRANSFER_CAPACITY:
                oldest_key = next(iter(self._sync_public_file_transfers))
                self._sync_public_file_transfers.pop(oldest_key, None)
            return
        if packet.packet_type != MessageType.MESSAGE:
            return
        packet_id_hex = packet_sync_id(packet).hex()
        self._sync_public_messages[packet_id_hex] = packet
        while len(self._sync_public_messages) > _SYNC_PUBLIC_MESSAGE_CAPACITY:
            oldest_key = next(iter(self._sync_public_messages))
            self._sync_public_messages.pop(oldest_key, None)

    def _sync_candidate_ids(self, types: SyncTypeFlags) -> list[bytes]:
        ids: list[bytes] = []
        if types.contains(MessageType.ANNOUNCE):
            ids.extend(packet_sync_id(packet) for packet in self._sync_announcements_by_peer.values())
        if types.contains(MessageType.MESSAGE):
            ids.extend(packet_sync_id(packet) for packet in self._sync_public_messages.values())
        if types.contains(MessageType.FRAGMENT):
            ids.extend(packet_sync_id(packet) for packet in self._sync_fragments.values())
        if types.contains(MessageType.FILE_TRANSFER):
            ids.extend(packet_sync_id(packet) for packet in self._sync_public_file_transfers.values())
        return ids

    def _sync_response_candidates(self, types: SyncTypeFlags) -> list[Packet]:
        packets: list[Packet] = []
        if types.contains(MessageType.ANNOUNCE):
            packets.extend(self._sync_announcements_by_peer.values())
        if types.contains(MessageType.MESSAGE):
            packets.extend(self._sync_public_messages.values())
        if types.contains(MessageType.FRAGMENT):
            packets.extend(self._sync_fragments.values())
        if types.contains(MessageType.FILE_TRANSFER):
            packets.extend(self._sync_public_file_transfers.values())
        return packets

    def _prune_runtime_state(
        self,
        *,
        now_monotonic: float | None = None,
        now_wall: datetime | None = None,
        force: bool = False,
    ) -> None:
        monotonic_now = now_monotonic if now_monotonic is not None else time.monotonic()
        if not force and monotonic_now - self._last_runtime_prune_at < _RUNTIME_PRUNE_INTERVAL_SECONDS:
            return
        self._last_runtime_prune_at = monotonic_now
        wall_now = now_wall or datetime.now(timezone.utc)
        self._request_sync_manager.cleanup()

        reconnect_cutoff = monotonic_now - _STALE_RECONNECT_RETENTION_SECONDS
        self._reconnect_ready_at = {
            address: ready_at
            for address, ready_at in self._reconnect_ready_at.items()
            if ready_at >= reconnect_cutoff
        }
        self._reconnect_attempts = {
            address: attempts
            for address, attempts in self._reconnect_attempts.items()
            if address in self._reconnect_ready_at or address in self.clients
        }
        self._connected_at = {
            address: connected_at
            for address, connected_at in self._connected_at.items()
            if address in self.clients
        }

        stale_cutoff = wall_now.timestamp() - _STALE_PEER_RETENTION_SECONDS
        stale_peer_ids: list[str] = []
        live_addresses = set(self.clients)
        for peer_id, peer in self.known_peers.items():
            peer.addresses.intersection_update(live_addresses)
            if peer.active_address not in live_addresses:
                peer.active_address = None
            if peer.last_seen.timestamp() < stale_cutoff and not peer.addresses:
                stale_peer_ids.append(peer_id)

        for peer_id in stale_peer_ids:
            self.known_peers.pop(peer_id, None)
            self._noise_sessions.pop(peer_id, None)
            self._pending_noise_payloads.pop(peer_id, None)
            self._sync_announcements_by_peer.pop(peer_id, None)

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
        data = packet.encode(padding=False)
        target_address = None
        if peer.active_address is not None and peer.active_address in self.clients:
            target_address = peer.active_address
        else:
            for address in sorted(peer.addresses):
                if address in self.clients:
                    target_address = address
                    break
        if target_address is None:
            peripheral_targets = {
                address
                for address in peer.addresses
                if address.startswith("peripheral:")
            }
            if peer.active_address is not None and peer.active_address.startswith("peripheral:"):
                peripheral_targets.add(peer.active_address)
            if peripheral_targets:
                self.peripheral_endpoint.send(data, target_addresses=peripheral_targets)
                self._mark_mesh_activity()
                if packet.signature is not None:
                    self._recent_own_signatures.append(packet.signature.hex())
                return True
        if target_address is None:
            self.system_log(f"No connected BitChat link to {peer.nickname} ({peer.peer_id}).")
            return False
        await self._write_packet(self.clients[target_address], packet)
        return True

    async def _broadcast_packet(self, packet: Packet, *, exclude_addresses: set[str] | None = None) -> None:
        excluded = exclude_addresses or set()
        data = packet.encode(padding=False)
        self.peripheral_endpoint.send(
            data,
            exclude_addresses={address for address in excluded if address.startswith("peripheral:")},
        )
        clients = [client for address, client in self.clients.items() if address not in excluded]
        if not clients:
            if not excluded:
                self.system_log("No connected peers yet.")
            return
        await asyncio.gather(*(self._write_packet(client, packet) for client in clients), return_exceptions=True)

    async def _write_packet(self, client: BleakClient, packet: Packet) -> None:
        data = packet.encode(padding=False)
        self._mark_mesh_activity()
        if packet.signature is not None:
            self._recent_own_signatures.append(packet.signature.hex())
        if len(data) <= MAX_PACKET_SIZE:
            await client.write_gatt_char(CHARACTERISTIC_UUID, data, response=False)
            return

        fragments = fragment_packet_sequence(packet)
        for index, fragment in enumerate(fragments):
            self._remember_sync_packet(fragment)
            await client.write_gatt_char(CHARACTERISTIC_UUID, fragment.encode(padding=False), response=False)
            if index + 1 < len(fragments):
                await asyncio.sleep(0.03)

    def _can_attempt_reconnect(self, address: str) -> bool:
        return time.monotonic() >= self._reconnect_ready_at.get(address, 0.0)

    def _record_reconnect_backoff(
        self,
        address: str,
        *,
        multiplier: float,
        now_monotonic: float | None = None,
    ) -> None:
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        attempts = self._reconnect_attempts.get(address, 0) + 1
        self._reconnect_attempts[address] = attempts
        base_delay = max(max(self.scan_interval, 2.0) * multiplier, 2.0)
        if self.background_mode:
            base_delay = max(base_delay, 10.0)
        delay = base_delay * (2 ** (attempts - 1))
        delay = min(delay, _MAX_RECONNECT_BACKOFF_SECONDS)
        self._reconnect_ready_at[address] = now + delay

    def _background_scan_durations(self, *, now_monotonic: float | None = None) -> tuple[float, float]:
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        scan_on_duration = max(1.0, self.scan_interval)
        scan_off_duration = max(scan_on_duration * 2.0, 10.0)
        idle_for = max(0.0, now - self._last_mesh_activity_at)
        live_link_count = len(self.clients) + self.peripheral_endpoint.subscriber_count()
        if live_link_count:
            scan_off_duration = max(scan_off_duration, _BACKGROUND_CONNECTED_SCAN_OFF_SECONDS)
        elif idle_for < _BACKGROUND_DISCOVERY_GRACE_SECONDS:
            scan_off_duration = 0.0
        elif idle_for >= _BACKGROUND_IDLE_THRESHOLD_SECONDS:
            scan_off_duration = max(scan_off_duration, _BACKGROUND_IDLE_SCAN_OFF_SECONDS)
        return scan_on_duration, min(scan_off_duration, _MAX_BACKGROUND_SCAN_OFF_SECONDS)

    def _mark_mesh_activity(self, *, now_monotonic: float | None = None) -> None:
        self._last_mesh_activity_at = now_monotonic if now_monotonic is not None else time.monotonic()
