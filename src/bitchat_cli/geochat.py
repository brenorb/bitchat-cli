from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import json
import math
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import urlopen

from coincurve import PrivateKey, PublicKeyXOnly
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from .dedupe import ExpiringSet

_GEOHASH_ALLOWED = set("0123456789bcdefghjkmnpqrstuvwxyz")
_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_GEOHASH_BASE32_MAP = {char: index for index, char in enumerate(_GEOHASH_BASE32)}
_EVENT_KIND_GEOHASH_MESSAGE = 20000
_EVENT_KIND_GEOHASH_PRESENCE = 20001
_GEORELAYS_URL = "https://raw.githubusercontent.com/permissionlesstech/georelays/refs/heads/main/nostr_relays.csv"
_DEFAULT_POW_MAX_ITERATIONS = 2_000_000


@dataclass(slots=True, frozen=True)
class NostrIdentity:
    private_key_hex: str
    public_key_hex: str


@dataclass(slots=True, frozen=True)
class GeoRelayEntry:
    host: str
    lat: float
    lon: float


def normalize_geohash(raw: str) -> str:
    geohash = raw.strip().lower().replace("#", "")
    if not geohash or len(geohash) > 12 or any(char not in _GEOHASH_ALLOWED for char in geohash):
        raise ValueError("invalid geohash")
    return geohash


def _candidate_geohash_private_key(seed: bytes, geohash: str, iteration: int) -> bytes:
    return hmac.new(
        seed,
        geohash.encode("utf-8") + int(iteration).to_bytes(4, "big"),
        hashlib.sha256,
    ).digest()


def derive_nostr_identity(seed: bytes, geohash: str) -> NostrIdentity:
    normalized = normalize_geohash(geohash)
    for iteration in range(10):
        candidate = _candidate_geohash_private_key(seed, normalized, iteration)
        if _valid_private_key(candidate):
            return _nostr_identity_from_secret(candidate)
    fallback = hashlib.sha256(seed + normalized.encode("utf-8")).digest()
    return _nostr_identity_from_secret(fallback)


def _valid_private_key(secret: bytes) -> bool:
    try:
        PrivateKey(secret)
    except ValueError:
        return False
    return True


def _nostr_identity_from_secret(secret: bytes) -> NostrIdentity:
    private_key = PrivateKey(secret)
    public_key = PublicKeyXOnly.from_secret(private_key.secret).format().hex()
    return NostrIdentity(private_key_hex=private_key.to_hex(), public_key_hex=public_key)


def create_ephemeral_geohash_event(
    *,
    content: str,
    geohash: str,
    identity: NostrIdentity,
    nickname: str | None = None,
    teleported: bool = False,
    created_at: int | None = None,
    pow_difficulty: int = 0,
) -> dict[str, object]:
    normalized = normalize_geohash(geohash)
    trimmed = content.strip()
    tags: list[list[str]] = [["g", normalized]]
    if nickname:
        nick = nickname.strip()
        if nick:
            tags.append(["n", nick])
    if teleported:
        tags.append(["t", "teleport"])
    event = {
        "pubkey": identity.public_key_hex,
        "created_at": int(created_at if created_at is not None else time.time()),
        "kind": _EVENT_KIND_GEOHASH_MESSAGE,
        "tags": tags,
        "content": trimmed,
    }
    if pow_difficulty < 0:
        raise ValueError("pow difficulty must be non-negative")
    if pow_difficulty > 0:
        event = mine_event_pow(event, pow_difficulty)
    digest = event_digest(event)
    signature = PrivateKey(bytes.fromhex(identity.private_key_hex)).sign_schnorr(digest).hex()
    event["id"] = digest.hex()
    event["sig"] = signature
    return event


def event_digest(event: dict[str, object]) -> bytes:
    payload = [
        0,
        event["pubkey"],
        int(event["created_at"]),
        int(event["kind"]),
        event["tags"],
        event["content"],
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).digest()


def verify_event_signature(event: dict[str, object]) -> bool:
    try:
        event_id = str(event["id"])
        pubkey = bytes.fromhex(str(event["pubkey"]))
        signature = bytes.fromhex(str(event["sig"]))
    except (KeyError, ValueError):
        return False
    digest = event_digest(event)
    if digest.hex() != event_id:
        return False
    try:
        return PublicKeyXOnly(pubkey).verify(signature, digest)
    except ValueError:
        return False


def event_pow_difficulty(event_id_hex: str) -> int:
    count = 0
    for character in event_id_hex.strip().lower():
        nibble = int(character, 16)
        if nibble == 0:
            count += 4
            continue
        if nibble == 1:
            count += 3
        elif nibble in (2, 3):
            count += 2
        elif nibble in (4, 5, 6, 7):
            count += 1
        break
    return count


def mine_event_pow(
    event: dict[str, object],
    target_difficulty: int,
    *,
    max_iterations: int = _DEFAULT_POW_MAX_ITERATIONS,
) -> dict[str, object]:
    if target_difficulty <= 0:
        return dict(event)
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")

    base_event = dict(event)
    base_tags = [
        [str(part) for part in tag]
        for tag in event.get("tags", [])
        if isinstance(tag, list) and not (tag and str(tag[0]) == "nonce")
    ]
    nonce = secrets.randbelow(1_000_000)
    created_at = int(base_event["created_at"])
    for _ in range(max_iterations):
        candidate = dict(base_event)
        candidate["created_at"] = created_at
        candidate["tags"] = base_tags + [["nonce", str(nonce), str(target_difficulty)]]
        digest = event_digest(candidate)
        if event_pow_difficulty(digest.hex()) >= target_difficulty:
            candidate["id"] = digest.hex()
            return candidate
        nonce += 1
    raise RuntimeError(f"failed to mine Nostr PoW difficulty {target_difficulty}")


def event_geohash(event: dict[str, object]) -> str | None:
    raw_tags = event.get("tags")
    if not isinstance(raw_tags, list):
        return None
    for item in raw_tags:
        if isinstance(item, list) and len(item) >= 2 and item[0] == "g":
            try:
                return normalize_geohash(str(item[1]))
            except ValueError:
                return None
    return None


def event_display_name(event: dict[str, object]) -> str:
    pubkey = str(event.get("pubkey") or "")
    suffix = pubkey[-4:] if len(pubkey) >= 4 else pubkey
    raw_tags = event.get("tags")
    if isinstance(raw_tags, list):
        for item in raw_tags:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "n":
                nickname = str(item[1]).strip()
                if nickname:
                    return f"{nickname}#{suffix}"
    return f"anon#{suffix}"


def decode_center(geohash: str) -> tuple[float, float]:
    normalized = normalize_geohash(geohash)
    lat_interval = [-90.0, 90.0]
    lon_interval = [-180.0, 180.0]
    is_even = True
    for char in normalized:
        value = _GEOHASH_BASE32_MAP[char]
        for mask in (16, 8, 4, 2, 1):
            if is_even:
                midpoint = (lon_interval[0] + lon_interval[1]) / 2
                if value & mask:
                    lon_interval[0] = midpoint
                else:
                    lon_interval[1] = midpoint
            else:
                midpoint = (lat_interval[0] + lat_interval[1]) / 2
                if value & mask:
                    lat_interval[0] = midpoint
                else:
                    lat_interval[1] = midpoint
            is_even = not is_even
    return (lat_interval[0] + lat_interval[1]) / 2, (lon_interval[0] + lon_interval[1]) / 2


def closest_relays(entries: list[GeoRelayEntry], geohash: str, *, count: int = 5) -> list[str]:
    lat, lon = decode_center(geohash)
    ranked = sorted(entries, key=lambda entry: (_haversine_km(lat, lon, entry.lat, entry.lon), entry.host))
    return [f"wss://{entry.host}" for entry in ranked[:count]]


def load_relay_entries(cache_path: Path) -> list[GeoRelayEntry]:
    text = cache_path.read_text(encoding="utf-8") if cache_path.exists() else ""
    if not text:
        text = _download_relay_csv()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
    entries = _parse_relay_csv(text)
    if entries:
        return entries
    if cache_path.exists():
        cache_path.unlink(missing_ok=True)
    text = _download_relay_csv()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return _parse_relay_csv(text)


def _download_relay_csv() -> str:
    try:
        with urlopen(_GEORELAYS_URL, timeout=10) as response:
            return response.read().decode("utf-8")
    except URLError as exc:
        raise RuntimeError(f"failed to fetch georelays directory: {exc.reason}") from exc


def _parse_relay_csv(text: str) -> list[GeoRelayEntry]:
    rows: list[GeoRelayEntry] = []
    reader = csv.DictReader(text.splitlines())
    for row in reader:
        host = (row.get("Relay URL") or "").strip()
        if not host:
            continue
        try:
            lat = float(row.get("Latitude") or "")
            lon = float(row.get("Longitude") or "")
        except ValueError:
            continue
        rows.append(GeoRelayEntry(host=host, lat=lat, lon=lon))
    return rows


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    delta_lat = lat2_rad - lat1_rad
    delta_lon = lon2_rad - lon1_rad
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


class GeoChatDaemon:
    def __init__(
        self,
        *,
        relay_cache_path: Path,
        seed_hex: str,
        nickname: str,
        on_message: Callable[[dict[str, object]], None],
        system_log: Callable[[str], None] | None = None,
    ) -> None:
        self.relay_cache_path = relay_cache_path
        self.seed = bytes.fromhex(seed_hex)
        self.nickname = nickname
        self.on_message = on_message
        self.system_log = system_log or (lambda _message: None)
        self._watch_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._watched_geohashes: list[str] = []
        self._relay_entries: list[GeoRelayEntry] = []
        self._recent_event_ids = ExpiringSet(ttl_seconds=600.0, max_entries=10_000)

    async def start(self, geohashes: list[str]) -> None:
        if geohashes:
            self._relay_entries = load_relay_entries(self.relay_cache_path)
        await self.set_watches(geohashes)

    async def stop(self) -> None:
        tasks = list(self._watch_tasks.values())
        self._watch_tasks.clear()
        self._watched_geohashes = []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def set_watches(self, geohashes: list[str]) -> None:
        normalized = sorted({normalize_geohash(item) for item in geohashes})
        if normalized and not self._relay_entries:
            self._relay_entries = load_relay_entries(self.relay_cache_path)
        desired: set[tuple[str, str]] = set()
        for geohash in normalized:
            for relay in closest_relays(self._relay_entries, geohash):
                desired.add((relay, geohash))
        existing = set(self._watch_tasks)
        removed_tasks: list[asyncio.Task[None]] = []
        for key in existing - desired:
            task = self._watch_tasks.pop(key)
            task.cancel()
            removed_tasks.append(task)
        if removed_tasks:
            await asyncio.gather(*removed_tasks, return_exceptions=True)
        for key in desired - existing:
            relay_url, geohash = key
            self._watch_tasks[key] = asyncio.create_task(self._watch_loop(relay_url, geohash))
        self._watched_geohashes = normalized

    async def publish(self, geohash: str, content: str, *, pow_difficulty: int = 0) -> dict[str, object]:
        normalized = normalize_geohash(geohash)
        if not self._relay_entries:
            self._relay_entries = load_relay_entries(self.relay_cache_path)
        identity = derive_nostr_identity(self.seed, normalized)
        event = create_ephemeral_geohash_event(
            content=content,
            geohash=normalized,
            identity=identity,
            nickname=self.nickname,
            pow_difficulty=pow_difficulty,
        )
        relays = closest_relays(self._relay_entries, normalized)
        if not relays:
            raise RuntimeError(f"no relays available for #{normalized}")
        await asyncio.gather(*(self._publish_to_relay(relay, event) for relay in relays))
        return event

    def runtime_status(self) -> dict[str, object]:
        return {
            "watched_geohashes": list(self._watched_geohashes),
            "relay_subscriptions": len(self._watch_tasks),
        }

    async def _publish_to_relay(self, relay_url: str, event: dict[str, object]) -> None:
        async with ws_connect(relay_url, open_timeout=10, ping_interval=20, ping_timeout=20) as websocket:
            await websocket.send(json.dumps(["EVENT", event], separators=(",", ":")))

    async def _watch_loop(self, relay_url: str, geohash: str) -> None:
        subscription_id = f"geo-{geohash}"
        filter_payload = {
            "kinds": [_EVENT_KIND_GEOHASH_MESSAGE, _EVENT_KIND_GEOHASH_PRESENCE],
            "#g": [geohash],
            "since": max(0, int(time.time()) - 300),
        }
        while True:
            try:
                async with ws_connect(relay_url, open_timeout=10, ping_interval=20, ping_timeout=20) as websocket:
                    await websocket.send(
                        json.dumps(["REQ", subscription_id, filter_payload], separators=(",", ":"))
                    )
                    async for raw_message in websocket:
                        self._handle_relay_message(geohash, raw_message)
            except asyncio.CancelledError:
                return
            except ConnectionClosed as exc:
                self.system_log(f"geo relay closed {relay_url} #{geohash}: {exc.code}")
            except Exception as exc:
                self.system_log(f"geo relay error {relay_url} #{geohash}: {exc}")
            await asyncio.sleep(2.0)

    def _handle_relay_message(self, geohash: str, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, list) or len(payload) < 3 or payload[0] != "EVENT":
            return
        event = payload[2]
        if not isinstance(event, dict):
            return
        if int(event.get("kind") or -1) not in (_EVENT_KIND_GEOHASH_MESSAGE, _EVENT_KIND_GEOHASH_PRESENCE):
            return
        if event_geohash(event) != geohash:
            return
        if not verify_event_signature(event):
            return
        if event.get("pubkey") == derive_nostr_identity(self.seed, geohash).public_key_hex:
            return
        event_id = str(event.get("id") or "")
        if not event_id:
            return
        if self._recent_event_ids.mark_seen(event_id):
            return
        self.on_message(event)
