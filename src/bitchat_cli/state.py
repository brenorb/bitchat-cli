from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .crypto import Identity


def default_app_dir() -> Path:
    return Path.home() / ".config" / "bitchat-cli"


def default_state_path() -> Path:
    return default_app_dir() / "state.json"


@dataclass(slots=True)
class AppState:
    nickname: str
    identity: Identity

    @classmethod
    def create(cls) -> "AppState":
        suffix = secrets.token_hex(2)
        return cls(nickname=f"anon-{suffix}", identity=Identity.generate())

    def to_json(self) -> dict[str, str]:
        data = {"nickname": self.nickname}
        data.update(self.identity.serialize())
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
        return AppState(
            nickname=data["nickname"],
            identity=Identity.from_hex(
                noise_private_key_hex=data["noise_private_key"],
                signing_private_key_hex=data["signing_private_key"],
            ),
        )

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
    def inbox_path(self) -> Path:
        return self.root / "messages.jsonl"

    @property
    def peers_path(self) -> Path:
        return self.root / "peers.json"

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

    def append(
        self,
        *,
        direction: str,
        sender: str,
        content: str,
        timestamp: datetime,
        peer_id: str | None = None,
        message_id: str | None = None,
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
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")

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

    def _deduplicate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        deduplicated: list[dict[str, Any]] = []
        for row in rows:
            key = row.get("message_id") or "|".join(
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
        else:
            nickname_to_store = existing["nickname"]
            noise_public_key_to_store = existing["noise_public_key"]
            signing_public_key_to_store = existing["signing_public_key"]
            last_seen_to_store = existing["last_seen"]

        merged_addresses = set(existing.get("addresses", [])) if existing is not None else set()
        merged_addresses.update(addresses)

        peers[peer_id] = {
            "peer_id": peer_id,
            "nickname": nickname_to_store,
            "noise_public_key": noise_public_key_to_store,
            "signing_public_key": signing_public_key_to_store,
            "addresses": sorted(merged_addresses),
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
