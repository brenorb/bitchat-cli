from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def peer_id_from_noise_public_key(noise_public_key: bytes) -> str:
    return sha256_hex(noise_public_key)[:16]


@dataclass(slots=True)
class Identity:
    noise_private_key: x25519.X25519PrivateKey
    signing_private_key: ed25519.Ed25519PrivateKey

    @classmethod
    def generate(cls) -> "Identity":
        return cls(
            noise_private_key=x25519.X25519PrivateKey.generate(),
            signing_private_key=ed25519.Ed25519PrivateKey.generate(),
        )

    @classmethod
    def from_hex(cls, noise_private_key_hex: str, signing_private_key_hex: str) -> "Identity":
        return cls(
            noise_private_key=x25519.X25519PrivateKey.from_private_bytes(bytes.fromhex(noise_private_key_hex)),
            signing_private_key=ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signing_private_key_hex)),
        )

    @property
    def noise_public_key(self) -> bytes:
        return self.noise_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def signing_public_key(self) -> bytes:
        return self.signing_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def peer_id(self) -> str:
        return peer_id_from_noise_public_key(self.noise_public_key)

    def serialize(self) -> dict[str, str]:
        return {
            "noise_private_key": self.noise_private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            ).hex(),
            "signing_private_key": self.signing_private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            ).hex(),
        }

    def sign(self, data: bytes) -> bytes:
        return self.signing_private_key.sign(data)


def verify_signature(public_key: bytes, signature: bytes, data: bytes) -> bool:
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(public_key).verify(signature, data)
    except Exception:
        return False
    return True
