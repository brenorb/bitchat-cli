from __future__ import annotations

import secrets
import time
from collections import deque
from dataclasses import dataclass

from .constants import MAX_PACKET_SIZE, MessageType
from .protocol import Packet


DEFAULT_FRAGMENT_CHUNK_SIZE = 469
FRAGMENT_HEADER_SIZE = 13


@dataclass(slots=True)
class FragmentHeader:
    sender_id: bytes
    fragment_id: bytes
    index: int
    total: int
    original_type: int
    chunk: bytes

    @classmethod
    def from_packet(cls, packet: Packet) -> "FragmentHeader | None":
        if packet.packet_type != MessageType.FRAGMENT:
            return None
        if len(packet.payload) < FRAGMENT_HEADER_SIZE:
            return None

        fragment_id = packet.payload[:8]
        index = int.from_bytes(packet.payload[8:10], "big")
        total = int.from_bytes(packet.payload[10:12], "big")
        original_type = packet.payload[12]
        if total <= 0 or total > 10_000 or index < 0 or index >= total:
            return None
        return cls(
            sender_id=packet.sender_id,
            fragment_id=fragment_id,
            index=index,
            total=total,
            original_type=original_type,
            chunk=packet.payload[FRAGMENT_HEADER_SIZE:],
        )


def fragment_packet_sequence(
    packet: Packet,
    *,
    chunk_size: int = DEFAULT_FRAGMENT_CHUNK_SIZE,
    fragment_id: bytes | None = None,
) -> list[Packet]:
    encoded = packet.encode(padding=False)
    if len(encoded) <= MAX_PACKET_SIZE:
        return [packet]

    fragment_id = fragment_id or secrets.token_bytes(8)
    if len(fragment_id) != 8:
        raise ValueError("fragment_id must be 8 bytes")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    chunks = [encoded[offset : offset + chunk_size] for offset in range(0, len(encoded), chunk_size)]
    packets: list[Packet] = []
    for index, chunk in enumerate(chunks):
        fragment = Packet(
            packet_type=MessageType.FRAGMENT,
            sender_id=packet.sender_id,
            recipient_id=packet.recipient_id,
            timestamp_ms=packet.timestamp_ms,
            payload=(
                fragment_id
                + index.to_bytes(2, "big")
                + len(chunks).to_bytes(2, "big")
                + bytes([int(packet.packet_type)])
                + chunk
            ),
            signature=None,
            ttl=packet.ttl,
            version=packet.version,
        )
        if len(fragment.encode(padding=False)) > MAX_PACKET_SIZE:
            raise ValueError("fragment exceeds BLE packet size limit")
        packets.append(fragment)
    return packets


class FragmentAssemblyBuffer:
    def __init__(
        self,
        *,
        max_in_flight: int = 128,
        expiry_seconds: float = 30.0,
    ) -> None:
        self.max_in_flight = max_in_flight
        self.expiry_seconds = expiry_seconds
        self._chunks: dict[tuple[bytes, bytes], dict[int, bytes]] = {}
        self._meta: dict[tuple[bytes, bytes], tuple[int, int, float]] = {}
        self._order: deque[tuple[bytes, bytes]] = deque()

    def append(self, packet: Packet) -> Packet | None:
        header = FragmentHeader.from_packet(packet)
        if header is None:
            return None

        now = time.monotonic()
        self.remove_expired(now=now)
        key = (header.sender_id, header.fragment_id)
        if key not in self._chunks:
            self._ensure_capacity()
            self._chunks[key] = {}
            self._meta[key] = (header.total, header.original_type, now)
            self._order.append(key)

        total, original_type, _created_at = self._meta[key]
        if total != header.total or original_type != header.original_type:
            self._discard(key)
            return None

        self._chunks[key][header.index] = header.chunk
        if len(self._chunks[key]) != total:
            return None

        encoded = b"".join(self._chunks[key][index] for index in range(total))
        self._discard(key)
        decoded = Packet.decode(encoded)
        if decoded.packet_type != original_type:
            raise ValueError("reassembled packet type mismatch")
        return decoded

    def remove_expired(self, *, now: float | None = None) -> None:
        now = now or time.monotonic()
        for key, (_total, _original_type, created_at) in list(self._meta.items()):
            if now - created_at > self.expiry_seconds:
                self._discard(key)

    def _ensure_capacity(self) -> None:
        while len(self._chunks) >= self.max_in_flight and self._order:
            self._discard(self._order[0])

    def _discard(self, key: tuple[bytes, bytes]) -> None:
        self._chunks.pop(key, None)
        self._meta.pop(key, None)
        try:
            self._order.remove(key)
        except ValueError:
            pass
