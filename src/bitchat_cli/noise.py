from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305


NOISE_PROTOCOL_NAME = "Noise_XX_25519_ChaChaPoly_SHA256"
MAX_HANDSHAKE_MESSAGE_SIZE = 2048
MAX_TRANSPORT_MESSAGE_SIZE = 65535
_REPLAY_WINDOW_SIZE = 1024
_TAG_SIZE = 16


class NoiseError(ValueError):
    pass


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _hkdf(chaining_key: bytes, input_key_material: bytes, num_outputs: int) -> list[bytes]:
    temp_key = hmac.new(chaining_key, input_key_material, hashlib.sha256).digest()
    outputs: list[bytes] = []
    current_output = b""
    for index in range(1, num_outputs + 1):
        current_output = hmac.new(temp_key, current_output + bytes([index]), hashlib.sha256).digest()
        outputs.append(current_output)
    return outputs


def _public_bytes(key: x25519.X25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _nonce_bytes(nonce: int) -> bytes:
    return b"\x00\x00\x00\x00" + nonce.to_bytes(8, "little")


class CipherState:
    def __init__(self, key: bytes | None = None, *, use_extracted_nonce: bool = False) -> None:
        self.key = key
        self.nonce = 0
        self.use_extracted_nonce = use_extracted_nonce
        self._highest_received_nonce = -1
        self._seen_nonces: set[int] = set()

    def has_key(self) -> bool:
        return self.key is not None

    def initialize_key(self, key: bytes) -> None:
        self.key = key
        self.nonce = 0
        self._highest_received_nonce = -1
        self._seen_nonces.clear()

    def encrypt(self, plaintext: bytes, associated_data: bytes = b"") -> bytes:
        if self.key is None:
            raise NoiseError("cipher key is not initialized")
        if len(plaintext) > MAX_TRANSPORT_MESSAGE_SIZE:
            raise NoiseError("transport payload too large")
        if self.nonce > 0xFFFFFFFF:
            raise NoiseError("nonce exhausted")

        current_nonce = self.nonce
        ciphertext = ChaCha20Poly1305(self.key).encrypt(
            _nonce_bytes(current_nonce),
            plaintext,
            associated_data,
        )
        self.nonce += 1
        if self.use_extracted_nonce:
            return current_nonce.to_bytes(4, "big") + ciphertext
        return ciphertext

    def decrypt(self, ciphertext: bytes, associated_data: bytes = b"") -> bytes:
        if self.key is None:
            raise NoiseError("cipher key is not initialized")

        if self.use_extracted_nonce:
            if len(ciphertext) < 4 + _TAG_SIZE:
                raise NoiseError("invalid ciphertext")
            received_nonce = int.from_bytes(ciphertext[:4], "big")
            self._validate_received_nonce(received_nonce)
            payload = ciphertext[4:]
        else:
            if len(ciphertext) < _TAG_SIZE:
                raise NoiseError("invalid ciphertext")
            received_nonce = self.nonce
            payload = ciphertext

        try:
            plaintext = ChaCha20Poly1305(self.key).decrypt(
                _nonce_bytes(received_nonce),
                payload,
                associated_data,
            )
        except Exception as exc:  # cryptography raises InvalidTag on failure
            raise NoiseError("decryption failed") from exc

        if not self.use_extracted_nonce and self.nonce > 0xFFFFFFFF:
            raise NoiseError("nonce exhausted")
        self.nonce += 1
        if self.use_extracted_nonce:
            self._mark_received_nonce(received_nonce)
        return plaintext

    def _validate_received_nonce(self, received_nonce: int) -> None:
        if self._highest_received_nonce >= _REPLAY_WINDOW_SIZE:
            oldest_allowed = self._highest_received_nonce - _REPLAY_WINDOW_SIZE
            if received_nonce <= oldest_allowed:
                raise NoiseError("replay detected")
        if received_nonce in self._seen_nonces:
            raise NoiseError("replay detected")

    def _mark_received_nonce(self, received_nonce: int) -> None:
        if received_nonce > self._highest_received_nonce:
            self._highest_received_nonce = received_nonce
            floor = max(0, received_nonce - _REPLAY_WINDOW_SIZE)
            self._seen_nonces = {nonce for nonce in self._seen_nonces if nonce > floor}
        self._seen_nonces.add(received_nonce)


class SymmetricState:
    def __init__(self, protocol_name: str) -> None:
        name = protocol_name.encode("utf-8")
        if len(name) <= 32:
            self.handshake_hash = name + (b"\x00" * (32 - len(name)))
        else:
            self.handshake_hash = _sha256(name)
        self.chaining_key = self.handshake_hash
        self.cipher_state = CipherState()

    def mix_key(self, input_key_material: bytes) -> None:
        self.chaining_key, temp_key = _hkdf(self.chaining_key, input_key_material, 2)
        self.cipher_state.initialize_key(temp_key)

    def mix_hash(self, data: bytes) -> None:
        self.handshake_hash = _sha256(self.handshake_hash + data)

    def has_cipher_key(self) -> bool:
        return self.cipher_state.has_key()

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        if self.cipher_state.has_key():
            ciphertext = self.cipher_state.encrypt(plaintext, self.handshake_hash)
            self.mix_hash(ciphertext)
            return ciphertext
        self.mix_hash(plaintext)
        return plaintext

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        if self.cipher_state.has_key():
            plaintext = self.cipher_state.decrypt(ciphertext, self.handshake_hash)
            self.mix_hash(ciphertext)
            return plaintext
        self.mix_hash(ciphertext)
        return ciphertext

    def split(self, *, use_extracted_nonce: bool) -> tuple[CipherState, CipherState]:
        key_1, key_2 = _hkdf(self.chaining_key, b"", 2)
        return (
            CipherState(key_1, use_extracted_nonce=use_extracted_nonce),
            CipherState(key_2, use_extracted_nonce=use_extracted_nonce),
        )


@dataclass
class XXHandshake:
    role: str
    local_static_private_key: x25519.X25519PrivateKey
    remote_static_public_key: x25519.X25519PublicKey | None = None
    prologue: bytes = b""
    predetermined_ephemeral_private_key: x25519.X25519PrivateKey | None = None

    def __post_init__(self) -> None:
        if self.role not in {"initiator", "responder"}:
            raise NoiseError("role must be initiator or responder")
        self._symmetric_state = SymmetricState(NOISE_PROTOCOL_NAME)
        self._symmetric_state.mix_hash(self.prologue)
        self._local_static_public_key = self.local_static_private_key.public_key()
        self._local_ephemeral_private_key: x25519.X25519PrivateKey | None = None
        self._remote_ephemeral_public_key: x25519.X25519PublicKey | None = None
        self._current_pattern = 0

    @property
    def handshake_hash(self) -> bytes:
        return self._symmetric_state.handshake_hash

    def write_message(self, payload: bytes = b"") -> bytes:
        if len(payload) > MAX_HANDSHAKE_MESSAGE_SIZE:
            raise NoiseError("handshake payload too large")
        if self._current_pattern >= len(_XX_MESSAGE_PATTERNS):
            raise NoiseError("handshake is already complete")

        buffer = bytearray()
        for token in _XX_MESSAGE_PATTERNS[self._current_pattern]:
            if token == "e":
                self._local_ephemeral_private_key = (
                    self.predetermined_ephemeral_private_key or x25519.X25519PrivateKey.generate()
                )
                self.predetermined_ephemeral_private_key = None
                public_key = _public_bytes(self._local_ephemeral_private_key.public_key())
                buffer.extend(public_key)
                self._symmetric_state.mix_hash(public_key)
                continue
            if token == "s":
                public_key = _public_bytes(self._local_static_public_key)
                buffer.extend(self._symmetric_state.encrypt_and_hash(public_key))
                continue
            self._mix_dh(token)

        buffer.extend(self._symmetric_state.encrypt_and_hash(payload))
        if len(buffer) > MAX_HANDSHAKE_MESSAGE_SIZE:
            raise NoiseError("handshake message too large")
        self._current_pattern += 1
        return bytes(buffer)

    def read_message(self, message: bytes) -> bytes:
        if len(message) > MAX_HANDSHAKE_MESSAGE_SIZE:
            raise NoiseError("handshake message too large")
        if self._current_pattern >= len(_XX_MESSAGE_PATTERNS):
            raise NoiseError("handshake is already complete")

        offset = 0
        for token in _XX_MESSAGE_PATTERNS[self._current_pattern]:
            if token == "e":
                if len(message) - offset < 32:
                    raise NoiseError("invalid handshake message")
                public_key = message[offset : offset + 32]
                offset += 32
                self._remote_ephemeral_public_key = x25519.X25519PublicKey.from_public_bytes(public_key)
                self._symmetric_state.mix_hash(public_key)
                continue
            if token == "s":
                key_length = 48 if self._symmetric_state.has_cipher_key() else 32
                if len(message) - offset < key_length:
                    raise NoiseError("invalid handshake message")
                encrypted_key = message[offset : offset + key_length]
                offset += key_length
                public_key = self._symmetric_state.decrypt_and_hash(encrypted_key)
                self.remote_static_public_key = x25519.X25519PublicKey.from_public_bytes(public_key)
                continue
            self._mix_dh(token)

        payload = self._symmetric_state.decrypt_and_hash(message[offset:])
        self._current_pattern += 1
        return payload

    def transport_ciphers(self, *, use_extracted_nonce: bool) -> tuple[CipherState, CipherState]:
        if self._current_pattern < len(_XX_MESSAGE_PATTERNS):
            raise NoiseError("handshake not complete")
        c1, c2 = self._symmetric_state.split(use_extracted_nonce=use_extracted_nonce)
        if self.role == "initiator":
            return c1, c2
        return c2, c1

    def _mix_dh(self, token: str) -> None:
        if token == "ee":
            shared_secret = self._require_local_ephemeral().exchange(self._require_remote_ephemeral())
        elif token == "es":
            if self.role == "initiator":
                shared_secret = self._require_local_ephemeral().exchange(self._require_remote_static())
            else:
                shared_secret = self.local_static_private_key.exchange(self._require_remote_ephemeral())
        elif token == "se":
            if self.role == "initiator":
                shared_secret = self.local_static_private_key.exchange(self._require_remote_ephemeral())
            else:
                shared_secret = self._require_local_ephemeral().exchange(self._require_remote_static())
        else:
            raise NoiseError(f"unsupported DH token: {token}")
        self._symmetric_state.mix_key(shared_secret)

    def _require_local_ephemeral(self) -> x25519.X25519PrivateKey:
        if self._local_ephemeral_private_key is None:
            raise NoiseError("local ephemeral key is missing")
        return self._local_ephemeral_private_key

    def _require_remote_ephemeral(self) -> x25519.X25519PublicKey:
        if self._remote_ephemeral_public_key is None:
            raise NoiseError("remote ephemeral key is missing")
        return self._remote_ephemeral_public_key

    def _require_remote_static(self) -> x25519.X25519PublicKey:
        if self.remote_static_public_key is None:
            raise NoiseError("remote static key is missing")
        return self.remote_static_public_key


_XX_MESSAGE_PATTERNS = [
    ("e",),
    ("e", "ee", "s", "es"),
    ("s", "se"),
]
