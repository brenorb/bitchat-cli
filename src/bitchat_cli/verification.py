from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from secrets import token_bytes
from urllib.parse import parse_qs, urlencode, urlparse

from .crypto import Identity, verify_signature

_VERIFY_CONTEXT = "bitchat-verify-v1"
_VERIFY_SCHEME = "bitchat"
_VERIFY_HOST = "verify"
_DEFAULT_MAX_AGE_SECONDS = 300


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


@dataclass(slots=True, frozen=True)
class VerificationPayload:
    version: int
    noise_key_hex: str
    sign_key_hex: str
    npub: str | None
    nickname: str
    timestamp_seconds: int
    nonce_b64: str
    signature_hex: str

    def canonical_bytes(self) -> bytes:
        chunks = bytearray()

        def append_field(value: str) -> None:
            encoded = value.encode("utf-8")
            chunks.append(min(len(encoded), 255))
            chunks.extend(encoded[:255])

        append_field(_VERIFY_CONTEXT)
        append_field(str(self.version))
        append_field(self.noise_key_hex.lower())
        append_field(self.sign_key_hex.lower())
        append_field(self.npub or "")
        append_field(self.nickname)
        append_field(str(self.timestamp_seconds))
        append_field(self.nonce_b64)
        return bytes(chunks)

    def to_url(self) -> str:
        query = {
            "v": str(self.version),
            "noise": self.noise_key_hex,
            "sign": self.sign_key_hex,
            "nick": self.nickname,
            "ts": str(self.timestamp_seconds),
            "nonce": self.nonce_b64,
            "sig": self.signature_hex,
        }
        if self.npub:
            query["npub"] = self.npub
        return f"{_VERIFY_SCHEME}://{_VERIFY_HOST}?{urlencode(query)}"

    @classmethod
    def from_url(cls, value: str) -> "VerificationPayload":
        parsed = urlparse(value)
        if parsed.scheme != _VERIFY_SCHEME or parsed.netloc != _VERIFY_HOST:
            raise ValueError("invalid verification payload")
        query = parse_qs(parsed.query)

        def one(name: str, *, required: bool = True) -> str | None:
            raw = query.get(name, [])
            if not raw:
                if required:
                    raise ValueError("invalid verification payload")
                return None
            return str(raw[0])

        version = int(one("v") or "0")
        return cls(
            version=version,
            noise_key_hex=str(one("noise") or ""),
            sign_key_hex=str(one("sign") or ""),
            npub=one("npub", required=False),
            nickname=str(one("nick") or ""),
            timestamp_seconds=int(one("ts") or "0"),
            nonce_b64=str(one("nonce") or ""),
            signature_hex=str(one("sig") or ""),
        )


def build_verification_payload(*, identity: Identity, nickname: str, npub: str | None) -> VerificationPayload:
    payload = VerificationPayload(
        version=1,
        noise_key_hex=identity.noise_public_key.hex(),
        sign_key_hex=identity.signing_public_key.hex(),
        npub=npub,
        nickname=nickname,
        timestamp_seconds=int(time.time()),
        nonce_b64=_base64url_encode(token_bytes(16)),
        signature_hex="",
    )
    signature = identity.sign(payload.canonical_bytes()).hex()
    return VerificationPayload(
        version=payload.version,
        noise_key_hex=payload.noise_key_hex,
        sign_key_hex=payload.sign_key_hex,
        npub=payload.npub,
        nickname=payload.nickname,
        timestamp_seconds=payload.timestamp_seconds,
        nonce_b64=payload.nonce_b64,
        signature_hex=signature,
    )


def verify_verification_payload(
    payload: VerificationPayload,
    *,
    now_seconds: int | None = None,
    max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
) -> bool:
    current = int(time.time()) if now_seconds is None else now_seconds
    if payload.version != 1 or current - payload.timestamp_seconds > max_age_seconds:
        return False
    try:
        signature = bytes.fromhex(payload.signature_hex)
        public_key = bytes.fromhex(payload.sign_key_hex)
        _base64url_decode(payload.nonce_b64)
    except ValueError:
        return False
    unsigned = VerificationPayload(
        version=payload.version,
        noise_key_hex=payload.noise_key_hex,
        sign_key_hex=payload.sign_key_hex,
        npub=payload.npub,
        nickname=payload.nickname,
        timestamp_seconds=payload.timestamp_seconds,
        nonce_b64=payload.nonce_b64,
        signature_hex="",
    )
    return verify_signature(public_key, signature, unsigned.canonical_bytes())
