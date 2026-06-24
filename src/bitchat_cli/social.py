from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SocialStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        return sorted(rows, key=lambda row: (str(row.get("nickname") or "").lower(), str(row.get("peer_id") or "")))

    def get(self, peer_id: str) -> dict[str, Any] | None:
        for row in self.read_all():
            if str(row.get("peer_id") or "") == peer_id:
                return row
        return None

    def get_by_pubkey(self, pubkey_hex: str) -> dict[str, Any] | None:
        normalized = pubkey_hex.strip().lower()
        for row in self.read_all():
            if str(row.get("nostr_pubkey_hex") or "").lower() == normalized:
                return row
        return None

    def upsert(
        self,
        *,
        peer_id: str,
        nickname: str,
        nostr_npub: str | None = None,
        nostr_pubkey_hex: str | None = None,
        favorite: bool | None = None,
        blocked: bool | None = None,
        verified: bool | None = None,
    ) -> dict[str, Any]:
        rows = {str(row.get("peer_id") or ""): row for row in self.read_all()}
        current = rows.get(peer_id, {})
        row = {
            "peer_id": peer_id,
            "nickname": nickname or str(current.get("nickname") or peer_id),
            "nostr_npub": nostr_npub if nostr_npub is not None else current.get("nostr_npub"),
            "nostr_pubkey_hex": nostr_pubkey_hex if nostr_pubkey_hex is not None else current.get("nostr_pubkey_hex"),
            "favorite": bool(favorite if favorite is not None else current.get("favorite", False)),
            "blocked": bool(blocked if blocked is not None else current.get("blocked", False)),
            "verified_at": current.get("verified_at"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if verified is True:
            row["verified_at"] = datetime.now(timezone.utc).isoformat()
        elif verified is False:
            row["verified_at"] = None
        rows[peer_id] = row
        self._write_all(list(rows.values()))
        return row

    def delete(self, peer_id: str) -> None:
        rows = [row for row in self.read_all() if str(row.get("peer_id") or "") != peer_id]
        self._write_all(rows)

    def _write_all(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(rows, key=lambda row: (str(row.get("nickname") or "").lower(), str(row.get("peer_id") or "")))
        self.path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
