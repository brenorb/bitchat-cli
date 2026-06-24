from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .protocol import Packet


def packet_cache_key(packet: Packet) -> str:
    if packet.signature is not None:
        return f"sig:{packet.signature.hex()}"

    digest = hashlib.sha256()
    digest.update(packet.version.to_bytes(1, "big"))
    digest.update(int(packet.packet_type).to_bytes(1, "big"))
    digest.update(packet.sender_id)
    digest.update((packet.recipient_id or b""))
    digest.update(int(packet.timestamp_ms).to_bytes(8, "big"))
    digest.update(packet.payload)
    return f"pkt:{digest.hexdigest()}"


@dataclass(slots=True)
class _SeenEntry:
    key: str
    seen_at: float


class ExpiringSet:
    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock or time.monotonic
        self._entries: deque[_SeenEntry] = deque()
        self._lookup: dict[str, float] = {}

    def mark_seen(self, key: str) -> bool:
        now = self.clock()
        self._evict(now)
        if key in self._lookup:
            return True

        self._entries.append(_SeenEntry(key=key, seen_at=now))
        self._lookup[key] = now
        self._trim()
        return False

    def _trim(self) -> None:
        while len(self._entries) > self.max_entries:
            entry = self._entries.popleft()
            self._lookup.pop(entry.key, None)

    def _evict(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        while self._entries and self._entries[0].seen_at < cutoff:
            entry = self._entries.popleft()
            if self._lookup.get(entry.key) == entry.seen_at:
                self._lookup.pop(entry.key, None)
