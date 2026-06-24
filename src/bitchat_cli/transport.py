from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from bleak import BleakClient, BleakScanner

from .constants import CHARACTERISTIC_UUID, MAX_PACKET_SIZE, SERVICE_UUID, MessageType
from .crypto import Identity, peer_id_from_noise_public_key
from .protocol import Announcement, Packet, make_announce_packet, make_leave_packet, make_public_message_packet


SystemLogger = Callable[[str], None]
MessageLogger = Callable[[str, str, datetime, str | None], None]


@dataclass(slots=True)
class PeerRecord:
    peer_id: str
    nickname: str
    noise_public_key: bytes
    signing_public_key: bytes
    addresses: set[str] = field(default_factory=set)
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BLEChatTransport:
    def __init__(
        self,
        *,
        identity: Identity,
        nickname: str,
        system_log: SystemLogger,
        message_log: MessageLogger,
        scan_interval: float = 5.0,
    ) -> None:
        self.identity = identity
        self.nickname = nickname
        self.system_log = system_log
        self.message_log = message_log
        self.scan_interval = scan_interval
        self.clients: dict[str, BleakClient] = {}
        self.connecting: set[str] = set()
        self.known_peers: dict[str, PeerRecord] = {}
        self._recent_own_signatures: deque[str] = deque(maxlen=128)
        self._scan_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._stop_event.clear()
        self._scan_task = asyncio.create_task(self._scan_loop())
        self.system_log("Scanning for BitChat peers over Bluetooth LE.")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._scan_task is not None:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
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

    async def set_nickname(self, nickname: str) -> None:
        self.nickname = nickname
        await self._broadcast_packet(make_announce_packet(self.identity, nickname))

    async def send_public_message(self, content: str) -> None:
        packet = make_public_message_packet(self.identity, content)
        await self._broadcast_packet(packet)

    async def _scan_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                devices = await BleakScanner.discover(timeout=self.scan_interval, service_uuids=[SERVICE_UUID])
                for device in devices:
                    address = getattr(device, "address", None)
                    if not address or address in self.clients or address in self.connecting:
                        continue
                    self.connecting.add(address)
                    asyncio.create_task(self._connect_device(device))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.system_log(f"Scan failed: {exc}")
                await asyncio.sleep(2.0)

    async def _connect_device(self, device) -> None:
        address = device.address
        client = BleakClient(device, disconnected_callback=lambda _: self._on_disconnect(address))
        try:
            await client.connect()
            characteristic = client.services.get_characteristic(CHARACTERISTIC_UUID)
            if characteristic is None:
                raise RuntimeError("peer missing BitChat characteristic")
            await client.start_notify(
                CHARACTERISTIC_UUID,
                lambda _sender, data, addr=address: asyncio.create_task(self._handle_notification(addr, bytes(data))),
            )
            self.clients[address] = client
            self.system_log(f"Connected to BitChat peer at {address}.")
            await self._write_packet(client, make_announce_packet(self.identity, self.nickname))
        except Exception as exc:
            self.system_log(f"Failed to connect to {address}: {exc}")
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass
        finally:
            self.connecting.discard(address)

    def _on_disconnect(self, address: str) -> None:
        if self.clients.pop(address, None) is not None:
            self.system_log(f"Disconnected from {address}.")

    async def _handle_notification(self, address: str, data: bytes) -> None:
        try:
            packet = Packet.decode(data)
        except Exception:
            return

        signature_hex = packet.signature.hex() if packet.signature else None
        if packet.sender_id.hex() == self.identity.peer_id or (
            signature_hex is not None and signature_hex in self._recent_own_signatures
        ):
            return

        if packet.packet_type == MessageType.ANNOUNCE:
            await self._handle_announce(address, packet)
            return
        if packet.packet_type == MessageType.MESSAGE:
            await self._handle_message(packet)
            return
        if packet.packet_type == MessageType.LEAVE:
            self._handle_leave(packet)
            return
        if packet.packet_type == MessageType.FRAGMENT:
            self.system_log("Ignored fragmented payload; this CLI does not reassemble fragments yet.")

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
        self.known_peers[claimed_peer_id] = peer
        if is_new:
            self.system_log(f"Peer online: {peer.nickname} ({peer.peer_id}).")

    async def _handle_message(self, packet: Packet) -> None:
        peer_id = packet.sender_id.hex()
        peer = self.known_peers.get(peer_id)
        if peer is None:
            return
        if not packet.verify_signature(peer.signing_public_key):
            self.system_log(f"Ignored message from {peer.nickname}: invalid signature.")
            return
        try:
            content = packet.payload.decode("utf-8")
        except UnicodeDecodeError:
            content = packet.payload.decode("utf-8", errors="replace")
        peer.last_seen = datetime.fromtimestamp(packet.timestamp_ms / 1000, tz=timezone.utc)
        self.message_log(peer.nickname, content, peer.last_seen, peer.peer_id)

    def _handle_leave(self, packet: Packet) -> None:
        peer_id = packet.sender_id.hex()
        peer = self.known_peers.get(peer_id)
        if peer is not None:
            self.system_log(f"Peer left: {peer.nickname} ({peer.peer_id}).")

    async def _broadcast_packet(self, packet: Packet) -> None:
        clients = list(self.clients.values())
        if not clients:
            self.system_log("No connected peers yet.")
            return
        await asyncio.gather(*(self._write_packet(client, packet) for client in clients), return_exceptions=True)

    async def _write_packet(self, client: BleakClient, packet: Packet) -> None:
        data = packet.encode(padding=False)
        if len(data) > MAX_PACKET_SIZE:
            raise ValueError("packet exceeds the first-cut CLI limit; fragmentation is not implemented")
        if packet.signature is not None:
            self._recent_own_signatures.append(packet.signature.hex())
        await client.write_gatt_char(CHARACTERISTIC_UUID, data, response=False)
