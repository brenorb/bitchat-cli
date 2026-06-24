from __future__ import annotations

import time
import zlib
from dataclasses import dataclass, field
from enum import IntEnum

from .constants import (
    COMPRESSION_THRESHOLD,
    FLAG_HAS_RECIPIENT,
    FLAG_HAS_SIGNATURE,
    FLAG_IS_COMPRESSED,
    FLAG_IS_RSR,
    MESSAGE_TTL,
    PADDING_BLOCK_SIZES,
    MessageType,
)
from .crypto import Identity, verify_signature


class NoisePayloadType(IntEnum):
    PRIVATE_MESSAGE = 0x01
    READ_RECEIPT = 0x02
    DELIVERED = 0x03


def optimal_block_size(data_size: int) -> int:
    total_size = data_size + 16
    for block_size in PADDING_BLOCK_SIZES:
        if total_size <= block_size:
            return block_size
    return data_size


def pad_message(data: bytes, target_size: int) -> bytes:
    if len(data) >= target_size:
        return data
    padding_needed = target_size - len(data)
    if padding_needed <= 0 or padding_needed > 255:
        return data
    return data + bytes([padding_needed]) * padding_needed


def unpad_message(data: bytes) -> bytes:
    if not data:
        return data
    pad_len = data[-1]
    if pad_len == 0 or pad_len > len(data):
        return data
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        return data
    return data[:-pad_len]


def should_compress(payload: bytes) -> bool:
    if len(payload) < COMPRESSION_THRESHOLD:
        return False
    sample_size = min(len(payload), 256)
    unique_ratio = len(set(payload)) / sample_size
    return unique_ratio < 0.9


def maybe_compress(payload: bytes) -> tuple[bytes, bool, int | None]:
    if not should_compress(payload):
        return payload, False, None
    compressed = zlib.compress(payload)
    if len(compressed) >= len(payload):
        return payload, False, None
    if len(payload) > 0xFFFF:
        return payload, False, None
    return compressed, True, len(payload)


@dataclass(slots=True)
class Announcement:
    nickname: str
    noise_public_key: bytes
    signing_public_key: bytes
    direct_neighbors: list[bytes] = field(default_factory=list)

    def encode(self) -> bytes:
        nickname_data = self.nickname.encode("utf-8")
        if len(nickname_data) > 255:
            raise ValueError("nickname too long")
        if len(self.noise_public_key) > 255 or len(self.signing_public_key) > 255:
            raise ValueError("public key too long")

        chunks = [
            bytes([0x01, len(nickname_data)]) + nickname_data,
            bytes([0x02, len(self.noise_public_key)]) + self.noise_public_key,
            bytes([0x03, len(self.signing_public_key)]) + self.signing_public_key,
        ]
        neighbors = self.direct_neighbors[:10]
        neighbors_blob = b"".join(neighbors)
        if neighbors_blob and len(neighbors_blob) % 8 == 0 and len(neighbors_blob) <= 255:
            chunks.append(bytes([0x04, len(neighbors_blob)]) + neighbors_blob)
        return b"".join(chunks)

    @classmethod
    def decode(cls, data: bytes) -> "Announcement":
        nickname: str | None = None
        noise_public_key: bytes | None = None
        signing_public_key: bytes | None = None
        direct_neighbors: list[bytes] = []
        offset = 0
        while offset + 2 <= len(data):
            tlv_type = data[offset]
            length = data[offset + 1]
            offset += 2
            if offset + length > len(data):
                raise ValueError("invalid TLV length")
            value = data[offset : offset + length]
            offset += length
            if tlv_type == 0x01:
                nickname = value.decode("utf-8")
            elif tlv_type == 0x02:
                noise_public_key = value
            elif tlv_type == 0x03:
                signing_public_key = value
            elif tlv_type == 0x04 and length % 8 == 0:
                direct_neighbors = [value[i : i + 8] for i in range(0, length, 8)]
        if nickname is None or noise_public_key is None or signing_public_key is None:
            raise ValueError("announcement missing required fields")
        return cls(
            nickname=nickname,
            noise_public_key=noise_public_key,
            signing_public_key=signing_public_key,
            direct_neighbors=direct_neighbors,
        )


@dataclass(slots=True)
class PrivateMessagePacket:
    message_id: str
    content: str

    def encode(self) -> bytes:
        message_id_data = self.message_id.encode("utf-8")
        content_data = self.content.encode("utf-8")
        if len(message_id_data) > 255:
            raise ValueError("message_id too long")
        if len(content_data) > 255:
            raise ValueError("content too long")
        return (
            bytes([0x00, len(message_id_data)])
            + message_id_data
            + bytes([0x01, len(content_data)])
            + content_data
        )

    @classmethod
    def decode(cls, data: bytes) -> "PrivateMessagePacket":
        message_id: str | None = None
        content: str | None = None
        offset = 0
        while offset + 2 <= len(data):
            tlv_type = data[offset]
            length = data[offset + 1]
            offset += 2
            if offset + length > len(data):
                raise ValueError("invalid TLV length")
            value = data[offset : offset + length]
            offset += length
            if tlv_type == 0x00:
                message_id = value.decode("utf-8")
            elif tlv_type == 0x01:
                content = value.decode("utf-8")
            else:
                raise ValueError("unknown private message TLV")
        if message_id is None or content is None:
            raise ValueError("private message missing required fields")
        return cls(message_id=message_id, content=content)


@dataclass(slots=True)
class NoisePayload:
    payload_type: NoisePayloadType
    payload: bytes

    def encode(self) -> bytes:
        return bytes([self.payload_type]) + self.payload

    @classmethod
    def decode(cls, data: bytes) -> "NoisePayload":
        if not data:
            raise ValueError("noise payload missing type byte")
        try:
            payload_type = NoisePayloadType(data[0])
        except ValueError as exc:
            raise ValueError("unknown noise payload type") from exc
        return cls(payload_type=payload_type, payload=bytes(data[1:]))


@dataclass(slots=True)
class Packet:
    packet_type: int
    sender_id: bytes
    payload: bytes
    recipient_id: bytes | None = None
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    signature: bytes | None = None
    ttl: int = MESSAGE_TTL
    version: int = 1
    is_rsr: bool = False

    def encode(self, *, padding: bool = False) -> bytes:
        if self.version != 1:
            raise ValueError("only protocol v1 is supported")
        if len(self.sender_id) != 8:
            raise ValueError("sender_id must be 8 bytes")
        if self.recipient_id is not None and len(self.recipient_id) != 8:
            raise ValueError("recipient_id must be 8 bytes")

        payload, is_compressed, original_size = maybe_compress(self.payload)
        flags = 0
        if self.recipient_id is not None:
            flags |= FLAG_HAS_RECIPIENT
        if self.signature is not None:
            flags |= FLAG_HAS_SIGNATURE
        if is_compressed:
            flags |= FLAG_IS_COMPRESSED
        if self.is_rsr:
            flags |= FLAG_IS_RSR

        payload_bytes = payload
        if is_compressed and original_size is not None:
            payload_bytes = original_size.to_bytes(2, "big") + payload

        header = bytearray()
        header.append(self.version)
        header.append(int(self.packet_type))
        header.append(self.ttl & 0xFF)
        header.extend(int(self.timestamp_ms).to_bytes(8, "big"))
        header.append(flags)
        header.extend(len(payload_bytes).to_bytes(2, "big"))

        body = bytearray(header)
        body.extend(self.sender_id)
        if self.recipient_id is not None:
            body.extend(self.recipient_id)
        body.extend(payload_bytes)
        if self.signature is not None:
            if len(self.signature) != 64:
                raise ValueError("signature must be 64 bytes")
            body.extend(self.signature)

        encoded = bytes(body)
        if padding:
            return pad_message(encoded, optimal_block_size(len(encoded)))
        return encoded

    def canonical_bytes_for_signing(self) -> bytes:
        unsigned = Packet(
            packet_type=self.packet_type,
            sender_id=self.sender_id,
            recipient_id=self.recipient_id,
            timestamp_ms=self.timestamp_ms,
            payload=self.payload,
            signature=None,
            ttl=0,
            version=self.version,
            is_rsr=False,
        )
        return unsigned.encode(padding=True)

    def sign(self, identity: Identity) -> "Packet":
        self.signature = identity.sign(self.canonical_bytes_for_signing())
        return self

    def verify_signature(self, public_key: bytes) -> bool:
        if self.signature is None:
            return False
        return verify_signature(public_key, self.signature, self.canonical_bytes_for_signing())

    @classmethod
    def decode(cls, raw: bytes) -> "Packet":
        try:
            return cls._decode_core(raw)
        except ValueError:
            unpadded = unpad_message(raw)
            if unpadded == raw:
                raise
            return cls._decode_core(unpadded)

    @classmethod
    def _decode_core(cls, raw: bytes) -> "Packet":
        if len(raw) < 22:
            raise ValueError("packet too short")
        version = raw[0]
        if version != 1:
            raise ValueError("unsupported version")
        packet_type = raw[1]
        ttl = raw[2]
        timestamp_ms = int.from_bytes(raw[3:11], "big")
        flags = raw[11]
        payload_length = int.from_bytes(raw[12:14], "big")
        offset = 14

        sender_id = raw[offset : offset + 8]
        if len(sender_id) != 8:
            raise ValueError("missing sender_id")
        offset += 8

        recipient_id = None
        if flags & FLAG_HAS_RECIPIENT:
            recipient_id = raw[offset : offset + 8]
            if len(recipient_id) != 8:
                raise ValueError("missing recipient_id")
            offset += 8

        payload_blob = raw[offset : offset + payload_length]
        if len(payload_blob) != payload_length:
            raise ValueError("truncated payload")
        offset += payload_length

        signature = None
        if flags & FLAG_HAS_SIGNATURE:
            signature = raw[offset : offset + 64]
            if len(signature) != 64:
                raise ValueError("truncated signature")
            offset += 64

        payload = payload_blob
        if flags & FLAG_IS_COMPRESSED:
            if len(payload_blob) < 2:
                raise ValueError("compressed payload missing original size")
            original_size = int.from_bytes(payload_blob[:2], "big")
            payload = zlib.decompress(payload_blob[2:])
            if len(payload) != original_size:
                raise ValueError("decompressed payload size mismatch")

        return cls(
            packet_type=packet_type,
            sender_id=sender_id,
            recipient_id=recipient_id,
            timestamp_ms=timestamp_ms,
            payload=payload,
            signature=signature,
            ttl=ttl,
            version=version,
            is_rsr=bool(flags & FLAG_IS_RSR),
        )


def make_public_message_packet(identity: Identity, content: str, *, timestamp_ms: int | None = None) -> Packet:
    packet = Packet(
        packet_type=MessageType.MESSAGE,
        sender_id=bytes.fromhex(identity.peer_id),
        payload=content.encode("utf-8"),
        timestamp_ms=timestamp_ms or int(time.time() * 1000),
    )
    return packet.sign(identity)


def make_announce_packet(
    identity: Identity,
    nickname: str,
    *,
    direct_neighbors: list[bytes] | None = None,
    timestamp_ms: int | None = None,
) -> Packet:
    announcement = Announcement(
        nickname=nickname,
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        direct_neighbors=direct_neighbors or [],
    )
    packet = Packet(
        packet_type=MessageType.ANNOUNCE,
        sender_id=bytes.fromhex(identity.peer_id),
        payload=announcement.encode(),
        timestamp_ms=timestamp_ms or int(time.time() * 1000),
    )
    return packet.sign(identity)


def make_leave_packet(identity: Identity, *, timestamp_ms: int | None = None) -> Packet:
    packet = Packet(
        packet_type=MessageType.LEAVE,
        sender_id=bytes.fromhex(identity.peer_id),
        payload=b"",
        timestamp_ms=timestamp_ms or int(time.time() * 1000),
    )
    return packet.sign(identity)


def make_read_receipt_payload(message_id: str) -> bytes:
    return NoisePayload(
        payload_type=NoisePayloadType.READ_RECEIPT,
        payload=message_id.encode("utf-8"),
    ).encode()


def make_delivered_payload(message_id: str) -> bytes:
    return NoisePayload(
        payload_type=NoisePayloadType.DELIVERED,
        payload=message_id.encode("utf-8"),
    ).encode()


def make_noise_handshake_packet(
    identity: Identity,
    recipient_peer_id: str,
    payload: bytes,
    *,
    timestamp_ms: int | None = None,
) -> Packet:
    return Packet(
        packet_type=MessageType.NOISE_HANDSHAKE,
        sender_id=bytes.fromhex(identity.peer_id),
        recipient_id=bytes.fromhex(recipient_peer_id),
        payload=payload,
        timestamp_ms=timestamp_ms or int(time.time() * 1000),
    )


def make_noise_encrypted_packet(
    identity: Identity,
    recipient_peer_id: str,
    payload: bytes,
    *,
    timestamp_ms: int | None = None,
) -> Packet:
    return Packet(
        packet_type=MessageType.NOISE_ENCRYPTED,
        sender_id=bytes.fromhex(identity.peer_id),
        recipient_id=bytes.fromhex(recipient_peer_id),
        payload=payload,
        timestamp_ms=timestamp_ms or int(time.time() * 1000),
    )
