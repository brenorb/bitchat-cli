from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import time
from dataclasses import dataclass
from typing import Callable

from coincurve import PrivateKey, PublicKey, PublicKeyXOnly
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from .constants import MessageType
from .network import NetworkConfig
from .protocol import NoisePayload, NoisePayloadType, Packet, PrivateMessagePacket

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_GENERATOR = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
_READ_ACK_THROTTLE_SECONDS = 0.35
_LOOKBACK_SECONDS = 48 * 60 * 60


class NostrError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class NostrIdentity:
    private_key_hex: str
    public_key_hex: str
    npub: str

    @classmethod
    def from_private_key_hex(cls, private_key_hex: str) -> "NostrIdentity":
        private_key = PrivateKey(bytes.fromhex(private_key_hex))
        public_key_hex = PublicKeyXOnly.from_secret(private_key.secret).format().hex()
        return cls(
            private_key_hex=private_key.to_hex(),
            public_key_hex=public_key_hex,
            npub=bech32_encode("npub", bytes.fromhex(public_key_hex)),
        )


@dataclass(slots=True)
class NostrEvent:
    pubkey: str
    created_at: int
    kind: int
    tags: list[list[str]]
    content: str
    id: str = ""
    sig: str | None = None

    def event_digest(self) -> bytes:
        payload = [0, self.pubkey, int(self.created_at), int(self.kind), self.tags, self.content]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).digest()

    def sign(self, identity: NostrIdentity) -> "NostrEvent":
        digest = self.event_digest()
        signature = PrivateKey(bytes.fromhex(identity.private_key_hex)).sign_schnorr(digest).hex()
        self.id = digest.hex()
        self.sig = signature
        return self

    def verify(self) -> bool:
        if not self.id or not self.sig:
            return False
        digest = self.event_digest()
        if digest.hex() != self.id:
            return False
        try:
            return PublicKeyXOnly(bytes.fromhex(self.pubkey)).verify(bytes.fromhex(self.sig), digest)
        except ValueError:
            return False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "pubkey": self.pubkey,
            "created_at": self.created_at,
            "kind": self.kind,
            "tags": self.tags,
            "content": self.content,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "NostrEvent":
        return cls(
            id=str(data.get("id") or ""),
            pubkey=str(data.get("pubkey") or ""),
            created_at=int(data.get("created_at") or 0),
            kind=int(data.get("kind") or 0),
            tags=[[str(part) for part in tag] for tag in data.get("tags", []) if isinstance(tag, list)],
            content=str(data.get("content") or ""),
            sig=str(data.get("sig")) if data.get("sig") is not None else None,
        )


@dataclass(slots=True, frozen=True)
class InboundNostrMessage:
    peer_id: str
    sender_pubkey_hex: str
    sender_peer_id: str
    recipient_pubkey_hex: str
    timestamp_ms: int
    event_id: str
    message_kind: str
    message_id: str
    content: str


def bech32_encode(hrp: str, data: bytes) -> str:
    values = _convert_bits(data, from_bits=8, to_bits=5, pad=True)
    checksum = _bech32_create_checksum(hrp, values)
    combined = values + checksum
    return hrp + "1" + "".join(_BECH32_CHARSET[item] for item in combined)


def bech32_decode(value: str) -> tuple[str, bytes]:
    if "1" not in value:
        raise NostrError("invalid bech32 string")
    separator = value.rfind("1")
    hrp = value[:separator]
    payload = value[separator + 1 :]
    values: list[int] = []
    for char in payload:
        try:
            values.append(_BECH32_CHARSET.index(char))
        except ValueError as exc:
            raise NostrError("invalid bech32 character") from exc
    if len(values) < 6:
        raise NostrError("invalid bech32 checksum")
    data = values[:-6]
    if _bech32_create_checksum(hrp, data) != values[-6:]:
        raise NostrError("invalid bech32 checksum")
    return hrp, bytes(_convert_bits(data, from_bits=5, to_bits=8, pad=False))


def decode_npub(npub: str) -> str:
    hrp, data = bech32_decode(npub.strip())
    if hrp != "npub" or len(data) != 32:
        raise NostrError("invalid npub")
    return data.hex()


def encode_bitchat_private_message(*, sender_peer_id: str, recipient_peer_id: str, message_id: str, content: str) -> str:
    payload = NoisePayload(
        payload_type=NoisePayloadType.PRIVATE_MESSAGE,
        payload=PrivateMessagePacket(message_id=message_id, content=content).encode(),
    ).encode()
    packet = Packet(
        packet_type=MessageType.NOISE_ENCRYPTED,
        sender_id=bytes.fromhex(sender_peer_id),
        recipient_id=bytes.fromhex(recipient_peer_id),
        payload=payload,
        ttl=7,
        version=1,
    )
    return "bitchat1:" + base64url_encode(packet.encode(padding=True))


def encode_bitchat_receipt(*, sender_peer_id: str, recipient_peer_id: str, receipt_type: str, message_id: str) -> str:
    payload_type = {
        "delivered": NoisePayloadType.DELIVERED,
        "read_receipt": NoisePayloadType.READ_RECEIPT,
    }.get(receipt_type)
    if payload_type is None:
        raise NostrError("unsupported receipt type")
    payload = NoisePayload(payload_type=payload_type, payload=message_id.encode("utf-8")).encode()
    packet = Packet(
        packet_type=MessageType.NOISE_ENCRYPTED,
        sender_id=bytes.fromhex(sender_peer_id),
        recipient_id=bytes.fromhex(recipient_peer_id),
        payload=payload,
        ttl=7,
        version=1,
    )
    return "bitchat1:" + base64url_encode(packet.encode(padding=True))


def encode_bitchat_private_message_no_recipient(*, sender_peer_id: str, message_id: str, content: str) -> str:
    payload = NoisePayload(
        payload_type=NoisePayloadType.PRIVATE_MESSAGE,
        payload=PrivateMessagePacket(message_id=message_id, content=content).encode(),
    ).encode()
    packet = Packet(
        packet_type=MessageType.NOISE_ENCRYPTED,
        sender_id=bytes.fromhex(sender_peer_id),
        recipient_id=None,
        payload=payload,
        ttl=7,
        version=1,
    )
    return "bitchat1:" + base64url_encode(packet.encode(padding=True))


def encode_bitchat_receipt_no_recipient(*, sender_peer_id: str, receipt_type: str, message_id: str) -> str:
    payload_type = {
        "delivered": NoisePayloadType.DELIVERED,
        "read_receipt": NoisePayloadType.READ_RECEIPT,
    }.get(receipt_type)
    if payload_type is None:
        raise NostrError("unsupported receipt type")
    payload = NoisePayload(payload_type=payload_type, payload=message_id.encode("utf-8")).encode()
    packet = Packet(
        packet_type=MessageType.NOISE_ENCRYPTED,
        sender_id=bytes.fromhex(sender_peer_id),
        recipient_id=None,
        payload=payload,
        ttl=7,
        version=1,
    )
    return "bitchat1:" + base64url_encode(packet.encode(padding=True))


def decode_bitchat_payload(content: str) -> InboundNostrMessage:
    if not content.startswith("bitchat1:"):
        raise NostrError("unsupported Nostr payload")
    packet = Packet.decode(base64url_decode(content.removeprefix("bitchat1:")))
    if packet.packet_type != MessageType.NOISE_ENCRYPTED:
        raise NostrError("unsupported embedded packet type")
    payload = NoisePayload.decode(packet.payload)
    if payload.payload_type == NoisePayloadType.PRIVATE_MESSAGE:
        message = PrivateMessagePacket.decode(payload.payload)
        message_kind = "private_message"
        message_id = message.message_id
        body = message.content
    elif payload.payload_type == NoisePayloadType.DELIVERED:
        message_kind = "receipt"
        message_id = payload.payload.decode("utf-8")
        body = "delivered"
    elif payload.payload_type == NoisePayloadType.READ_RECEIPT:
        message_kind = "receipt"
        message_id = payload.payload.decode("utf-8")
        body = "read_receipt"
    else:
        raise NostrError("unsupported embedded payload type")
    return InboundNostrMessage(
        peer_id=packet.recipient_id.hex() if packet.recipient_id is not None else "",
        sender_pubkey_hex="",
        sender_peer_id=packet.sender_id.hex(),
        recipient_pubkey_hex="",
        timestamp_ms=packet.timestamp_ms,
        event_id="",
        message_kind=message_kind,
        message_id=message_id,
        content=body,
    )


def create_private_message_event(*, content: str, recipient_pubkey_hex: str, sender_identity: NostrIdentity) -> NostrEvent:
    rumor = NostrEvent(
        pubkey=sender_identity.public_key_hex,
        created_at=int(time.time()),
        kind=14,
        tags=[],
        content=content,
    )
    seal = _create_seal(rumor=rumor, recipient_pubkey_hex=recipient_pubkey_hex, sender_identity=sender_identity)
    return _create_gift_wrap(seal=seal, recipient_pubkey_hex=recipient_pubkey_hex)


def decrypt_private_message(*, gift_wrap: NostrEvent, recipient_identity: NostrIdentity) -> tuple[str, str, int]:
    if gift_wrap.kind != 1059 or not gift_wrap.verify():
        raise NostrError("invalid gift wrap")
    seal = NostrEvent.from_dict(json.loads(_decrypt_content(gift_wrap.content, gift_wrap.pubkey, recipient_identity)))
    if not seal.verify():
        raise NostrError("invalid seal signature")
    rumor = NostrEvent.from_dict(json.loads(_decrypt_content(seal.content, seal.pubkey, recipient_identity)))
    if seal.pubkey != rumor.pubkey:
        raise NostrError("mismatched rumor sender")
    return rumor.content, seal.pubkey, rumor.created_at


class NostrDMClient:
    def __init__(
        self,
        *,
        identity: NostrIdentity,
        relay_urls: list[str],
        network_config: NetworkConfig,
        on_message: Callable[[InboundNostrMessage], None],
        system_log: Callable[[str], None] | None = None,
    ) -> None:
        self.identity = identity
        self.relay_urls = sorted({item.strip() for item in relay_urls if item.strip()})
        self.network_config = network_config
        self.on_message = on_message
        self.system_log = system_log or (lambda _message: None)
        self._watch_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._recent_event_ids: set[str] = set()
        self._read_ack_lock = asyncio.Lock()
        self._extra_watch_identities: dict[str, NostrIdentity] = {}

    async def start(self) -> None:
        if self._watch_tasks or not self.relay_urls:
            return
        await self._sync_watch_tasks()

    async def stop(self) -> None:
        tasks = list(self._watch_tasks.values())
        self._watch_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def set_extra_watch_identities(self, identities: list[NostrIdentity]) -> None:
        self._extra_watch_identities = {
            item.public_key_hex: item
            for item in identities
        }
        if self.relay_urls:
            await self._sync_watch_tasks()

    async def send_private_message(
        self,
        *,
        sender_peer_id: str,
        recipient_peer_id: str,
        recipient_npub: str,
        message_id: str,
        content: str,
    ) -> str:
        embedded = encode_bitchat_private_message(
            sender_peer_id=sender_peer_id,
            recipient_peer_id=recipient_peer_id,
            message_id=message_id,
            content=content,
        )
        event = create_private_message_event(
            content=embedded,
            recipient_pubkey_hex=decode_npub(recipient_npub),
            sender_identity=self.identity,
        )
        await self._publish_event(event)
        return event.id

    async def send_geohash_private_message(
        self,
        *,
        sender_peer_id: str,
        sender_identity: NostrIdentity,
        recipient_pubkey_hex: str,
        message_id: str,
        content: str,
    ) -> str:
        embedded = encode_bitchat_private_message_no_recipient(
            sender_peer_id=sender_peer_id,
            message_id=message_id,
            content=content,
        )
        event = create_private_message_event(
            content=embedded,
            recipient_pubkey_hex=recipient_pubkey_hex,
            sender_identity=sender_identity,
        )
        await self._publish_event(event)
        return event.id

    async def send_receipt(
        self,
        *,
        sender_peer_id: str,
        recipient_peer_id: str,
        recipient_npub: str,
        receipt_type: str,
        message_id: str,
    ) -> str:
        embedded = encode_bitchat_receipt(
            sender_peer_id=sender_peer_id,
            recipient_peer_id=recipient_peer_id,
            receipt_type=receipt_type,
            message_id=message_id,
        )
        event = create_private_message_event(
            content=embedded,
            recipient_pubkey_hex=decode_npub(recipient_npub),
            sender_identity=self.identity,
        )
        if receipt_type == "read_receipt":
            async with self._read_ack_lock:
                await self._publish_event(event)
                await asyncio.sleep(_READ_ACK_THROTTLE_SECONDS)
        else:
            await self._publish_event(event)
        return event.id

    async def send_geohash_receipt(
        self,
        *,
        sender_peer_id: str,
        sender_identity: NostrIdentity,
        recipient_pubkey_hex: str,
        receipt_type: str,
        message_id: str,
    ) -> str:
        embedded = encode_bitchat_receipt_no_recipient(
            sender_peer_id=sender_peer_id,
            receipt_type=receipt_type,
            message_id=message_id,
        )
        event = create_private_message_event(
            content=embedded,
            recipient_pubkey_hex=recipient_pubkey_hex,
            sender_identity=sender_identity,
        )
        if receipt_type == "read_receipt":
            async with self._read_ack_lock:
                await self._publish_event(event)
                await asyncio.sleep(_READ_ACK_THROTTLE_SECONDS)
        else:
            await self._publish_event(event)
        return event.id

    async def _publish_event(self, event: NostrEvent) -> None:
        if not self.relay_urls:
            raise NostrError("no Nostr relays configured")
        payload = json.dumps(["EVENT", event.to_dict()], separators=(",", ":"))
        successes = 0
        for relay_url in self.relay_urls:
            try:
                async with ws_connect(
                    relay_url,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    **self.network_config.websocket_proxy_argument(),
                ) as websocket:
                    await websocket.send(payload)
                    successes += 1
            except Exception as exc:
                self.system_log(f"nostr publish error {relay_url}: {exc}")
        if successes == 0:
            raise NostrError("failed to publish to any Nostr relay")

    async def _sync_watch_tasks(self) -> None:
        desired: dict[tuple[str, str], NostrIdentity] = {}
        identities = [self.identity, *self._extra_watch_identities.values()]
        for relay_url in self.relay_urls:
            for identity in identities:
                desired[(relay_url, identity.public_key_hex)] = identity

        existing = set(self._watch_tasks)
        desired_keys = set(desired)
        removed_tasks: list[asyncio.Task[None]] = []
        for key in existing - desired_keys:
            task = self._watch_tasks.pop(key)
            task.cancel()
            removed_tasks.append(task)
        if removed_tasks:
            await asyncio.gather(*removed_tasks, return_exceptions=True)
        for key in desired_keys - existing:
            relay_url, _ = key
            self._watch_tasks[key] = asyncio.create_task(self._watch_loop(relay_url, desired[key]))

    async def _watch_loop(self, relay_url: str, recipient_identity: NostrIdentity) -> None:
        subscription_id = f"dm-{recipient_identity.public_key_hex[:12]}"
        filter_payload = {
            "kinds": [1059],
            "#p": [recipient_identity.public_key_hex],
            "since": max(0, int(time.time()) - _LOOKBACK_SECONDS),
        }
        while True:
            try:
                async with ws_connect(
                    relay_url,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    **self.network_config.websocket_proxy_argument(),
                ) as websocket:
                    await websocket.send(json.dumps(["REQ", subscription_id, filter_payload], separators=(",", ":")))
                    async for raw_message in websocket:
                        self._handle_relay_message(raw_message, recipient_identity=recipient_identity)
            except asyncio.CancelledError:
                return
            except ConnectionClosed as exc:
                self.system_log(f"nostr relay closed {relay_url}: {exc.code}")
            except Exception as exc:
                self.system_log(f"nostr relay error {relay_url}: {exc}")
            await asyncio.sleep(2.0)

    def _handle_relay_message(self, raw_message: str, *, recipient_identity: NostrIdentity) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, list) or len(payload) < 3 or payload[0] != "EVENT":
            return
        raw_event = payload[2]
        if not isinstance(raw_event, dict):
            return
        event = NostrEvent.from_dict(raw_event)
        if event.id in self._recent_event_ids:
            return
        try:
            content, sender_pubkey_hex, rumor_timestamp = decrypt_private_message(
                gift_wrap=event,
                recipient_identity=recipient_identity,
            )
            decoded = decode_bitchat_payload(content)
        except Exception:
            return
        decoded = InboundNostrMessage(
            peer_id=decoded.peer_id,
            sender_pubkey_hex=sender_pubkey_hex,
            sender_peer_id=decoded.sender_peer_id,
            recipient_pubkey_hex=recipient_identity.public_key_hex,
            timestamp_ms=rumor_timestamp * 1000,
            event_id=event.id,
            message_kind=decoded.message_kind,
            message_id=decoded.message_id,
            content=decoded.content,
        )
        self._recent_event_ids.add(event.id)
        self.on_message(decoded)


def _create_seal(*, rumor: NostrEvent, recipient_pubkey_hex: str, sender_identity: NostrIdentity) -> NostrEvent:
    encrypted = _encrypt_content(_event_json(rumor), recipient_pubkey_hex, sender_identity.private_key_hex)
    return NostrEvent(
        pubkey=sender_identity.public_key_hex,
        created_at=_randomized_timestamp(),
        kind=13,
        tags=[],
        content=encrypted,
    ).sign(sender_identity)


def _create_gift_wrap(*, seal: NostrEvent, recipient_pubkey_hex: str) -> NostrEvent:
    wrap_identity = NostrIdentity.from_private_key_hex(PrivateKey().to_hex())
    encrypted = _encrypt_content(_event_json(seal), recipient_pubkey_hex, wrap_identity.private_key_hex)
    return NostrEvent(
        pubkey=wrap_identity.public_key_hex,
        created_at=_randomized_timestamp(),
        kind=1059,
        tags=[["p", recipient_pubkey_hex]],
        content=encrypted,
    ).sign(wrap_identity)


def _event_json(event: NostrEvent) -> str:
    return json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _randomized_timestamp() -> int:
    return int(time.time() + random.uniform(-900.0, 900.0))


def _encrypt_content(plaintext: str, recipient_pubkey_hex: str, sender_private_key_hex: str) -> str:
    shared_secret = _shared_secret(sender_private_key_hex, recipient_pubkey_hex)
    key = _derive_nip44_v2_key(shared_secret)
    nonce24 = random.randbytes(24)
    ciphertext = _xchacha20poly1305_encrypt(key=key, nonce24=nonce24, plaintext=plaintext.encode("utf-8"))
    return "v2:" + base64url_encode(nonce24 + ciphertext)


def _decrypt_content(ciphertext: str, sender_pubkey_hex: str, recipient_identity: NostrIdentity) -> str:
    if not ciphertext.startswith("v2:"):
        raise NostrError("invalid ciphertext version")
    data = base64url_decode(ciphertext.removeprefix("v2:"))
    if len(data) <= 40:
        raise NostrError("invalid ciphertext")
    nonce24 = data[:24]
    body = data[24:]
    for prefix in ("02", "03"):
        try:
            shared_secret = _shared_secret(recipient_identity.private_key_hex, prefix + sender_pubkey_hex)
            key = _derive_nip44_v2_key(shared_secret)
            plaintext = _xchacha20poly1305_decrypt(key=key, nonce24=nonce24, ciphertext=body)
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, NostrError):
            continue
    raise NostrError("failed to decrypt Nostr message")


def _shared_secret(private_key_hex: str, public_key_hex: str) -> bytes:
    compressed = bytes.fromhex(public_key_hex)
    if len(compressed) == 32:
        compressed = bytes.fromhex("02" + public_key_hex)
    public_key = PublicKey(compressed)
    shared_public_key = public_key.multiply(bytes.fromhex(private_key_hex))
    return shared_public_key.format(compressed=True)


def _derive_nip44_v2_key(shared_secret: bytes) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"nip44-v2")
    return hkdf.derive(shared_secret)


def _xchacha20poly1305_encrypt(*, key: bytes, nonce24: bytes, plaintext: bytes) -> bytes:
    subkey = _hchacha20(key, nonce24[:16])
    nonce12 = b"\x00\x00\x00\x00" + nonce24[16:]
    cipher = ChaCha20Poly1305(subkey)
    return cipher.encrypt(nonce12, plaintext, b"")


def _xchacha20poly1305_decrypt(*, key: bytes, nonce24: bytes, ciphertext: bytes) -> bytes:
    subkey = _hchacha20(key, nonce24[:16])
    nonce12 = b"\x00\x00\x00\x00" + nonce24[16:]
    cipher = ChaCha20Poly1305(subkey)
    return cipher.decrypt(nonce12, ciphertext, b"")


def _hchacha20(key: bytes, nonce16: bytes) -> bytes:
    if len(key) != 32 or len(nonce16) != 16:
        raise NostrError("invalid HChaCha20 input lengths")
    words = [
        0x61707865,
        0x3320646E,
        0x79622D32,
        0x6B206574,
        *_load_words_le(key),
        *_load_words_le(nonce16),
    ]
    for _ in range(10):
        _quarter_round(words, 0, 4, 8, 12)
        _quarter_round(words, 1, 5, 9, 13)
        _quarter_round(words, 2, 6, 10, 14)
        _quarter_round(words, 3, 7, 11, 15)
        _quarter_round(words, 0, 5, 10, 15)
        _quarter_round(words, 1, 6, 11, 12)
        _quarter_round(words, 2, 7, 8, 13)
        _quarter_round(words, 3, 4, 9, 14)
    output = words[0:4] + words[12:16]
    return b"".join(item.to_bytes(4, "little") for item in output)


def _load_words_le(data: bytes) -> list[int]:
    return [int.from_bytes(data[index : index + 4], "little") for index in range(0, len(data), 4)]


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = ((state[d] << 16) | (state[d] >> 16)) & 0xFFFFFFFF
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = ((state[b] << 12) | (state[b] >> 20)) & 0xFFFFFFFF
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = ((state[d] << 8) | (state[d] >> 24)) & 0xFFFFFFFF
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = ((state[b] << 7) | (state[b] >> 25)) & 0xFFFFFFFF


def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def base64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _convert_bits(data: bytes | list[int], *, from_bits: int, to_bits: int, pad: bool) -> list[int]:
    acc = 0
    bits = 0
    result: list[int] = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            raise NostrError("invalid bit group")
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            result.append((acc >> bits) & maxv)
    if pad:
        if bits:
            result.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        raise NostrError("invalid padding")
    return result


def _bech32_create_checksum(hrp: str, data: list[int]) -> list[int]:
    values = _bech32_hrp_expand(hrp) + data + [0, 0, 0, 0, 0, 0]
    polymod = _bech32_polymod(values) ^ 1
    return [(polymod >> (5 * (5 - index))) & 31 for index in range(6)]


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def _bech32_polymod(values: list[int]) -> int:
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for index in range(5):
            if (top >> index) & 1:
                chk ^= _BECH32_GENERATOR[index]
    return chk
