from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable

from .protocol import Packet


class SyncMessageType(IntEnum):
    ANNOUNCE = 0x01
    MESSAGE = 0x02
    LEAVE = 0x03
    NOISE_HANDSHAKE = 0x10
    NOISE_ENCRYPTED = 0x11
    FRAGMENT = 0x20
    REQUEST_SYNC = 0x21
    FILE_TRANSFER = 0x22


class SyncTypeFlags:
    _TYPE_TO_BIT = {
        SyncMessageType.ANNOUNCE: 0,
        SyncMessageType.MESSAGE: 1,
        SyncMessageType.LEAVE: 2,
        SyncMessageType.NOISE_HANDSHAKE: 3,
        SyncMessageType.NOISE_ENCRYPTED: 4,
        SyncMessageType.FRAGMENT: 5,
        SyncMessageType.REQUEST_SYNC: 6,
        SyncMessageType.FILE_TRANSFER: 7,
    }

    def __init__(self, raw_value: int) -> None:
        self.raw_value = raw_value & 0x00FF_FFFF_FFFF_FFFF

    @classmethod
    def from_message_types(cls, message_types: list[int]) -> "SyncTypeFlags":
        raw_value = 0
        for message_type in message_types:
            bit = cls._TYPE_TO_BIT.get(SyncMessageType(message_type))
            if bit is not None:
                raw_value |= 1 << bit
        return cls(raw_value)

    def contains(self, message_type: int) -> bool:
        bit = self._TYPE_TO_BIT.get(SyncMessageType(message_type))
        return bit is not None and bool(self.raw_value & (1 << bit))

    def intersect(self, other: "SyncTypeFlags") -> "SyncTypeFlags":
        return SyncTypeFlags(self.raw_value & other.raw_value)

    def is_empty(self) -> bool:
        return self.raw_value == 0

    def to_bytes(self) -> bytes | None:
        if self.raw_value == 0:
            return None
        value = self.raw_value
        out = bytearray()
        while value > 0 and len(out) < 8:
            out.append(value & 0xFF)
            value >>= 8
        while out and out[-1] == 0:
            out.pop()
        return bytes(out) if out else None

    @classmethod
    def decode(cls, data: bytes) -> "SyncTypeFlags | None":
        if not 1 <= len(data) <= 8:
            return None
        raw = 0
        for index, byte in enumerate(data):
            raw |= byte << (index * 8)
        return cls(raw)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SyncTypeFlags) and self.raw_value == other.raw_value


SyncTypeFlags.PUBLIC_MESSAGES = SyncTypeFlags.from_message_types(
    [SyncMessageType.ANNOUNCE, SyncMessageType.MESSAGE]
)


@dataclass(slots=True)
class RequestSyncPacket:
    p: int
    m: int
    data: bytes
    types: SyncTypeFlags | None = None
    since_timestamp: int | None = None
    fragment_id_filter: str | None = None

    def encode(self) -> bytes:
        out = bytearray()

        def put_tlv(tag: int, value: bytes) -> None:
            out.append(tag)
            out.extend(len(value).to_bytes(2, "big"))
            out.extend(value)

        put_tlv(0x01, bytes([self.p & 0xFF]))
        put_tlv(0x02, int(self.m).to_bytes(4, "big"))
        put_tlv(0x03, self.data)
        if self.types is not None and (types_bytes := self.types.to_bytes()) is not None:
            put_tlv(0x04, types_bytes)
        if self.since_timestamp is not None:
            put_tlv(0x05, int(self.since_timestamp).to_bytes(8, "big"))
        if self.fragment_id_filter is not None:
            put_tlv(0x06, self.fragment_id_filter.encode("utf-8"))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes, *, max_accept_bytes: int = 1024) -> "RequestSyncPacket | None":
        offset = 0
        p: int | None = None
        m: int | None = None
        payload: bytes | None = None
        types: SyncTypeFlags | None = None
        since_timestamp: int | None = None
        fragment_id_filter: str | None = None

        while offset + 3 <= len(data):
            tag = data[offset]
            offset += 1
            length = int.from_bytes(data[offset : offset + 2], "big")
            offset += 2
            if offset + length > len(data):
                return None
            value = data[offset : offset + length]
            offset += length
            if tag == 0x01 and len(value) == 1:
                p = value[0]
            elif tag == 0x02 and len(value) == 4:
                m = int.from_bytes(value, "big")
            elif tag == 0x03:
                if len(value) > max_accept_bytes:
                    return None
                payload = bytes(value)
            elif tag == 0x04:
                types = SyncTypeFlags.decode(bytes(value))
            elif tag == 0x05 and len(value) == 8:
                since_timestamp = int.from_bytes(value, "big")
            elif tag == 0x06:
                try:
                    fragment_id_filter = bytes(value).decode("utf-8")
                except UnicodeDecodeError:
                    return None

        if p is None or m is None or payload is None or p < 1 or p > GCSFilter.MAX_P or m <= 0:
            return None
        return cls(
            p=p,
            m=m,
            data=payload,
            types=types,
            since_timestamp=since_timestamp,
            fragment_id_filter=fragment_id_filter,
        )


class RequestSyncManager:
    def __init__(
        self,
        *,
        response_window_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.response_window_seconds = response_window_seconds
        self.clock = clock or time.monotonic
        self._pending_requests: dict[str, float] = {}

    def register_request(self, peer_id: str) -> None:
        self._pending_requests[peer_id] = self.clock()

    def is_valid_response(self, peer_id: str, *, is_rsr: bool) -> bool:
        if not is_rsr:
            return False
        self.cleanup()
        request_time = self._pending_requests.get(peer_id)
        if request_time is None:
            return False
        return self.clock() - request_time <= self.response_window_seconds

    def cleanup(self) -> None:
        now = self.clock()
        cutoff = now - self.response_window_seconds
        self._pending_requests = {
            peer_id: request_time
            for peer_id, request_time in self._pending_requests.items()
            if request_time >= cutoff
        }


def packet_sync_id(packet: Packet) -> bytes:
    digest = hashlib.sha256()
    digest.update(bytes([int(packet.packet_type)]))
    digest.update(packet.sender_id)
    digest.update(int(packet.timestamp_ms).to_bytes(8, "big"))
    digest.update(packet.payload)
    return digest.digest()[:16]


class GCSFilter:
    MAX_P = 32

    @staticmethod
    def derive_p(*, target_fpr: float) -> int:
        bounded = max(0.000001, min(0.25, target_fpr))
        raw = 0
        while (1 / (2 ** raw)) > bounded:
            raw += 1
        return max(1, raw)

    @staticmethod
    def estimate_max_elements(*, size_bytes: int, p: int) -> int:
        bits = max(8, size_bytes * 8)
        per = max(3, p + 2)
        return max(1, bits // per)

    @classmethod
    def build_filter(cls, *, ids: list[bytes], max_bytes: int, target_fpr: float) -> tuple[int, int, bytes]:
        p = cls.derive_p(target_fpr=target_fpr)
        if not ids:
            return p, 1, b""

        cap = cls.estimate_max_elements(size_bytes=max_bytes, p=p)
        selected = ids[:cap]
        m = min(max(1, len(selected) * (1 << p)), 0xFFFFFFFF)
        mapped = cls._normalize_mapped_values(
            sorted(cls.bucket(packet_id, modulus=m) for packet_id in selected),
            modulus=m,
        )
        encoded = cls._encode(mapped, p)
        trimmed = mapped

        while len(encoded) > max_bytes and len(trimmed) > 1:
            trimmed = trimmed[: max(1, (len(trimmed) * 9) // 10)]
            encoded = cls._encode(trimmed, p)

        if len(encoded) > max_bytes:
            return p, 1, b""
        return p, m, encoded

    @classmethod
    def decode_to_sorted_set(cls, *, p: int, m: int, data: bytes) -> list[int]:
        if p < 1 or p > cls.MAX_P or m <= 1:
            return []
        reader = _BitReader(data)
        values: list[int] = []
        acc = 0
        while True:
            q = reader.read_unary()
            if q is None:
                break
            r = reader.read_bits(p)
            if r is None:
                break
            x = (q << p) + r + 1
            acc += x
            if acc >= m:
                break
            values.append(acc)
        return values

    @classmethod
    def contains(cls, *, sorted_values: list[int], candidate: int) -> bool:
        low = 0
        high = len(sorted_values) - 1
        while low <= high:
            mid = (low + high) >> 1
            value = sorted_values[mid]
            if value == candidate:
                return True
            if value < candidate:
                low = mid + 1
            else:
                high = mid - 1
        return False

    @classmethod
    def bucket(cls, packet_id: bytes, *, modulus: int) -> int:
        if modulus <= 1:
            return 0
        hashed = hashlib.sha256(packet_id).digest()[:8]
        h64 = int.from_bytes(hashed, "big") & 0x7FFF_FFFF_FFFF_FFFF
        value = h64 % modulus
        return 1 if value == 0 else value

    @staticmethod
    def _normalize_mapped_values(values: list[int], *, modulus: int) -> list[int]:
        if modulus <= 1 or not values:
            return []
        out: list[int] = []
        last = 0
        for value in values:
            normalized = min(value, modulus - 1)
            if normalized > last:
                out.append(normalized)
                last = normalized
        return out

    @staticmethod
    def _encode(sorted_values: list[int], p: int) -> bytes:
        writer = _BitWriter()
        prev = 0
        mask = (1 << p) - 1
        for value in sorted_values:
            delta = value - prev
            prev = value
            q = (delta - 1) >> p
            r = (delta - 1) & mask
            writer.write_ones(q)
            writer.write_bit(0)
            writer.write_bits(r, p)
        return writer.to_bytes()


class _BitWriter:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._cur = 0
        self._nbits = 0

    def write_bit(self, bit: int) -> None:
        self._cur = ((self._cur << 1) | (bit & 1)) & 0xFF
        self._nbits += 1
        if self._nbits == 8:
            self._buf.append(self._cur)
            self._cur = 0
            self._nbits = 0

    def write_ones(self, count: int) -> None:
        for _ in range(max(0, count)):
            self.write_bit(1)

    def write_bits(self, value: int, count: int) -> None:
        for index in range(count - 1, -1, -1):
            self.write_bit((value >> index) & 1)

    def to_bytes(self) -> bytes:
        if self._nbits > 0:
            self._buf.append((self._cur << (8 - self._nbits)) & 0xFF)
            self._cur = 0
            self._nbits = 0
        return bytes(self._buf)


class _BitReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._index = 0
        self._current = data[0] if data else 0
        self._left = 8 if data else 0

    def read_bit(self) -> int | None:
        if self._index >= len(self._data):
            return None
        bit = (self._current >> 7) & 1
        self._current = (self._current << 1) & 0xFF
        self._left -= 1
        if self._left == 0:
            self._index += 1
            if self._index < len(self._data):
                self._current = self._data[self._index]
                self._left = 8
        return bit

    def read_unary(self) -> int | None:
        count = 0
        while True:
            bit = self.read_bit()
            if bit is None:
                return None
            if bit == 1:
                count += 1
            else:
                return count

    def read_bits(self, count: int) -> int | None:
        value = 0
        for _ in range(count):
            bit = self.read_bit()
            if bit is None:
                return None
            value = (value << 1) | bit
        return value
