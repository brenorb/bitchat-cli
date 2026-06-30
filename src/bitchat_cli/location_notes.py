from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

from coincurve import PrivateKey
from websockets.asyncio.client import connect as ws_connect

from .geochat import (
    closest_relays,
    derive_nostr_identity,
    event_digest,
    load_relay_entries,
    normalize_geohash,
)
from .network import NetworkConfig

_NOTE_KIND = 1


@dataclass(slots=True, frozen=True)
class LocationNote:
    event_id: str
    pubkey: str
    geohash: str
    content: str
    created_at: int
    nickname: str | None

    @property
    def display_name(self) -> str:
        suffix = self.pubkey[-4:] if len(self.pubkey) >= 4 else self.pubkey
        if self.nickname:
            return f"{self.nickname}#{suffix}"
        return f"anon#{suffix}"


def _create_location_note_event(*, geohash: str, content: str, nickname: str, seed_hex: str) -> dict[str, object]:
    normalized = normalize_geohash(geohash)
    if len(normalized) != 8:
        raise ValueError("location notes require building-level geohashes (8 characters)")
    identity = derive_nostr_identity(bytes.fromhex(seed_hex), normalized)
    event = {
        "pubkey": identity.public_key_hex,
        "created_at": int(time.time()),
        "kind": _NOTE_KIND,
        "tags": [["g", normalized], ["n", nickname.strip()]],
        "content": content.strip(),
    }
    digest = event_digest(event)
    event["id"] = digest.hex()
    event["sig"] = PrivateKey(bytes.fromhex(identity.private_key_hex)).sign_schnorr(digest).hex()
    return event


async def publish_location_note(
    *,
    relay_cache_path: Path,
    network_config: NetworkConfig,
    geohash: str,
    content: str,
    nickname: str,
    seed_hex: str,
) -> dict[str, object]:
    event = _create_location_note_event(geohash=geohash, content=content, nickname=nickname, seed_hex=seed_hex)
    relays = closest_relays(load_relay_entries(relay_cache_path, network_config=network_config), geohash)
    if not relays:
        raise RuntimeError(f"no relays available for #{normalize_geohash(geohash)}")
    payload = json.dumps(["EVENT", event], separators=(",", ":"))
    await asyncio.gather(
        *[
            _publish_to_relay(relay_url, payload, network_config=network_config)
            for relay_url in relays
        ]
    )
    return event


async def fetch_location_notes(
    *,
    relay_cache_path: Path,
    network_config: NetworkConfig,
    geohash: str,
    limit: int = 50,
) -> list[LocationNote]:
    normalized = normalize_geohash(geohash)
    if len(normalized) != 8:
        raise ValueError("location notes require building-level geohashes (8 characters)")
    relays = closest_relays(load_relay_entries(relay_cache_path, network_config=network_config), normalized)
    if not relays:
        return []
    results = await asyncio.gather(
        *[
            _fetch_from_relay(relay_url, geohash=normalized, limit=limit, network_config=network_config)
            for relay_url in relays
        ]
    )
    deduped: dict[str, LocationNote] = {}
    for notes in results:
        for note in notes:
            deduped.setdefault(note.event_id, note)
    return sorted(deduped.values(), key=lambda row: row.created_at, reverse=True)[:limit]


async def _publish_to_relay(relay_url: str, payload: str, *, network_config: NetworkConfig) -> None:
    async with ws_connect(
        relay_url,
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        **network_config.websocket_proxy_argument(),
    ) as websocket:
        await websocket.send(payload)


async def _fetch_from_relay(
    relay_url: str,
    *,
    geohash: str,
    limit: int,
    network_config: NetworkConfig,
) -> list[LocationNote]:
    subscription_id = f"locnotes-{geohash}-{int(time.time() * 1000)}"
    request = json.dumps(
        [
            "REQ",
            subscription_id,
            {"kinds": [_NOTE_KIND], "#g": [geohash], "limit": max(1, min(limit, 200))},
        ],
        separators=(",", ":"),
    )
    rows: list[LocationNote] = []
    try:
        async with ws_connect(
            relay_url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            **network_config.websocket_proxy_argument(),
        ) as websocket:
            await websocket.send(request)
            while True:
                raw = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                payload = json.loads(raw)
                if not isinstance(payload, list) or len(payload) < 2:
                    continue
                if payload[0] == "EOSE":
                    break
                if payload[0] != "EVENT" or len(payload) < 3 or not isinstance(payload[2], dict):
                    continue
                event = payload[2]
                if int(event.get("kind") or 0) != _NOTE_KIND:
                    continue
                nickname = None
                for tag in event.get("tags", []):
                    if isinstance(tag, list) and len(tag) >= 2 and str(tag[0]).lower() == "n":
                        nickname = str(tag[1]).strip() or None
                        break
                rows.append(
                    LocationNote(
                        event_id=str(event.get("id") or ""),
                        pubkey=str(event.get("pubkey") or ""),
                        geohash=geohash,
                        content=str(event.get("content") or ""),
                        created_at=int(event.get("created_at") or 0),
                        nickname=nickname,
                    )
                )
    except Exception:
        return []
    return rows
