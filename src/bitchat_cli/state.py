from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coincurve import PrivateKey

from .crypto import Identity

_DEFAULT_NOSTR_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://offchain.pub",
]
_APP_ARTIFACT_NAMES = (
    "state.json",
    "messages.jsonl",
    "peers.json",
    "social.json",
    "daemon.pid",
    "daemon.sock",
    "radio.pid",
)


def _has_app_artifacts(path: Path) -> bool:
    return any((path / name).exists() for name in _APP_ARTIFACT_NAMES)


def default_app_dir() -> Path:
    preferred = Path.home() / ".config" / "bitchat"
    legacy = Path.home() / ".config" / "bitchat-cli"
    if _has_app_artifacts(legacy) and not _has_app_artifacts(preferred):
        return legacy
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def default_state_path() -> Path:
    return default_app_dir() / "state.json"


@dataclass(slots=True)
class AppState:
    nickname: str
    identity: Identity
    nostr_device_seed_hex: str
    nostr_private_key_hex: str
    nostr_relays: list[str]
    network_proxy_mode: str
    network_proxy_url: str | None
    network_proxy_policy: str
    watched_geohashes: list[str]

    @classmethod
    def create(cls) -> "AppState":
        suffix = secrets.token_hex(2)
        nostr_private_key = PrivateKey().to_hex()
        return cls(
            nickname=f"anon-{suffix}",
            identity=Identity.generate(),
            nostr_device_seed_hex=secrets.token_hex(32),
            nostr_private_key_hex=nostr_private_key,
            nostr_relays=list(_DEFAULT_NOSTR_RELAYS),
            network_proxy_mode="disabled",
            network_proxy_url=None,
            network_proxy_policy="prefer",
            watched_geohashes=[],
        )

    def to_json(self) -> dict[str, str]:
        data = {"nickname": self.nickname}
        data.update(self.identity.serialize())
        data["nostr_device_seed"] = self.nostr_device_seed_hex
        data["nostr_private_key"] = self.nostr_private_key_hex
        data["nostr_relays"] = list(self.nostr_relays)
        data["network_proxy_mode"] = self.network_proxy_mode
        data["network_proxy_url"] = self.network_proxy_url
        data["network_proxy_policy"] = self.network_proxy_policy
        data["watched_geohashes"] = list(self.watched_geohashes)
        return data


class StateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_state_path()

    def load(self) -> AppState:
        if not self.path.exists():
            state = AppState.create()
            self.save(state)
            return state
        data = json.loads(self.path.read_text())
        nostr_private_key_hex = str(data.get("nostr_private_key") or "")
        if len(nostr_private_key_hex) != 64:
            nostr_private_key_hex = PrivateKey().to_hex()
        raw_proxy_mode = str(data.get("network_proxy_mode") or "disabled").strip().lower()
        network_proxy_mode = raw_proxy_mode if raw_proxy_mode in {"disabled", "system", "custom"} else "disabled"
        raw_proxy_policy = str(data.get("network_proxy_policy") or "prefer").strip().lower()
        network_proxy_policy = raw_proxy_policy if raw_proxy_policy in {"prefer", "require"} else "prefer"
        raw_relays = data.get("nostr_relays", _DEFAULT_NOSTR_RELAYS)
        nostr_relays = [str(item).strip() for item in raw_relays if str(item).strip()]
        if not nostr_relays:
            nostr_relays = list(_DEFAULT_NOSTR_RELAYS)
        state = AppState(
            nickname=data["nickname"],
            identity=Identity.from_hex(
                noise_private_key_hex=data["noise_private_key"],
                signing_private_key_hex=data["signing_private_key"],
            ),
            nostr_device_seed_hex=str(data.get("nostr_device_seed") or secrets.token_hex(32)),
            nostr_private_key_hex=nostr_private_key_hex,
            nostr_relays=nostr_relays,
            network_proxy_mode=network_proxy_mode,
            network_proxy_url=(
                str(data.get("network_proxy_url")).strip()
                if data.get("network_proxy_url") is not None and str(data.get("network_proxy_url")).strip()
                else None
            ),
            network_proxy_policy=network_proxy_policy,
            watched_geohashes=[
                str(item)
                for item in data.get("watched_geohashes", [])
                if str(item)
            ],
        )
        if (
            "nostr_device_seed" not in data
            or "nostr_private_key" not in data
            or "nostr_relays" not in data
            or "network_proxy_mode" not in data
            or "network_proxy_policy" not in data
            or "watched_geohashes" not in data
        ):
            self.save(state)
        return state

    def save(self, state: AppState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state.to_json(), indent=2) + "\n")


class RuntimePaths:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_app_dir()

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def daemon_pid_path(self) -> Path:
        return self.root / "daemon.pid"

    @property
    def daemon_log_path(self) -> Path:
        return self.root / "daemon.log"

    @property
    def daemon_socket_path(self) -> Path:
        return self.root / "daemon.sock"

    @property
    def inbox_path(self) -> Path:
        return self.root / "messages.jsonl"

    @property
    def peers_path(self) -> Path:
        return self.root / "peers.json"

    @property
    def social_path(self) -> Path:
        return self.root / "social.json"

    @property
    def georelays_cache_path(self) -> Path:
        return self.root / "georelays_cache.csv"

    @property
    def incoming_files_dir(self) -> Path:
        return self.root / "files" / "incoming"

    @property
    def incoming_images_dir(self) -> Path:
        return self.root / "images" / "incoming"

    @property
    def radio_pid_path(self) -> Path:
        return self.root / "radio.pid"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ActiveSessionLease:
    def __init__(self, path: Path, *, is_running=pid_is_running) -> None:
        self.path = path
        self.is_running = is_running

    def acquire(self, *, pid: int | None = None) -> tuple[bool, int | None]:
        owner_pid = self.read_owner_pid()
        current_pid = pid or os.getpid()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if owner_pid is not None and owner_pid != current_pid and self.is_running(owner_pid):
            return False, owner_pid
        self.path.write_text(f"{current_pid}\n", encoding="utf-8")
        return True, None

    def release(self, *, pid: int | None = None) -> None:
        owner_pid = self.read_owner_pid()
        current_pid = pid or os.getpid()
        if owner_pid is None or owner_pid != current_pid:
            return
        self.path.unlink(missing_ok=True)

    def read_owner_pid(self) -> int | None:
        if not self.path.exists():
            return None
        try:
            return int(self.path.read_text(encoding="utf-8").strip())
        except ValueError:
            self.path.unlink(missing_ok=True)
            return None


class MessageStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or RuntimePaths().inbox_path
        self._seen_append_keys: set[str] | None = None

    def append(
        self,
        *,
        direction: str,
        sender: str,
        content: str,
        timestamp: datetime,
        peer_id: str | None = None,
        message_id: str | None = None,
        scope: str = "public",
        conversation_id: str | None = None,
        message_kind: str = "message",
        target_peer_id: str | None = None,
        delivery_state: str | None = None,
        pow_difficulty: int | None = None,
        teleported: bool | None = None,
        attachment_name: str | None = None,
        attachment_mime: str | None = None,
        attachment_size_bytes: int | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
            "direction": direction,
            "sender": sender,
            "peer_id": peer_id,
            "content": content,
            "message_id": message_id,
            "scope": scope,
            "conversation_id": conversation_id,
            "message_kind": message_kind,
            "target_peer_id": target_peer_id,
            "delivery_state": delivery_state,
            "pow_difficulty": pow_difficulty,
            "teleported": teleported,
        }
        if attachment_name is not None:
            record["attachment_name"] = attachment_name
        if attachment_mime is not None:
            record["attachment_mime"] = attachment_mime
        if attachment_size_bytes is not None:
            record["attachment_size_bytes"] = attachment_size_bytes
        append_key = self._append_key(record)
        if append_key is not None:
            self._ensure_append_index()
            assert self._seen_append_keys is not None
            if append_key in self._seen_append_keys:
                return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        if append_key is not None:
            assert self._seen_append_keys is not None
            self._seen_append_keys.add(append_key)

    def read_all(self, *, deduplicate: bool = False) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        if deduplicate:
            rows = self._deduplicate(rows)
        return rows

    def _ensure_append_index(self) -> None:
        if self._seen_append_keys is not None:
            return
        seen: set[str] = set()
        if self.path.exists():
            for row in self.read_all():
                key = self._append_key(row)
                if key is not None:
                    seen.add(key)
        self._seen_append_keys = seen

    def _append_key(self, row: dict[str, Any]) -> str | None:
        message_id = row.get("message_id")
        if not message_id:
            return None
        return "|".join(
            [
                str(row.get("scope", "public")),
                str(row.get("message_kind", "message")),
                str(row.get("direction")),
                str(row.get("peer_id")),
                str(message_id),
            ]
        )

    def _deduplicate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        deduplicated: list[dict[str, Any]] = []
        for row in rows:
            key = self._append_key(row)
            if key is None:
                key = "|".join(
                    [
                        str(row.get("direction")),
                        str(row.get("peer_id")),
                        str(row.get("sender")),
                        str(row.get("timestamp")),
                        str(row.get("content")),
                    ]
                )
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(row)
        return deduplicated


class PeerStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or RuntimePaths().peers_path

    def upsert(
        self,
        *,
        peer_id: str,
        nickname: str,
        noise_public_key: bytes,
        signing_public_key: bytes,
        addresses: set[str],
        last_seen: datetime,
    ) -> None:
        peers = {row["peer_id"]: row for row in self.read_all()}
        existing = peers.get(peer_id)
        existing_last_seen = None
        if existing is not None:
            existing_last_seen = datetime.fromisoformat(existing["last_seen"])

        if existing_last_seen is None or last_seen >= existing_last_seen:
            nickname_to_store = nickname
            noise_public_key_to_store = noise_public_key.hex()
            signing_public_key_to_store = signing_public_key.hex()
            last_seen_to_store = last_seen.astimezone(timezone.utc).isoformat()
            addresses_to_store = sorted(addresses)
        else:
            nickname_to_store = existing["nickname"]
            noise_public_key_to_store = existing["noise_public_key"]
            signing_public_key_to_store = existing["signing_public_key"]
            last_seen_to_store = existing["last_seen"]
            addresses_to_store = list(existing.get("addresses", []))

        peers[peer_id] = {
            "peer_id": peer_id,
            "nickname": nickname_to_store,
            "noise_public_key": noise_public_key_to_store,
            "signing_public_key": signing_public_key_to_store,
            "addresses": addresses_to_store,
            "last_seen": last_seen_to_store,
        }
        self._write_all(list(peers.values()))

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        return sorted(
            rows,
            key=lambda row: (
                row.get("nickname", "").lower(),
                row.get("peer_id", ""),
            ),
        )

    def _write_all(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(
            rows,
            key=lambda row: (
                row.get("nickname", "").lower(),
                row.get("peer_id", ""),
            ),
        )
        self.path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
