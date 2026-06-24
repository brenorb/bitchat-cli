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

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


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
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
            "direction": direction,
            "sender": sender,
            "peer_id": peer_id,
            "content": content,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return rows


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
