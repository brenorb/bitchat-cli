from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .files import format_file_size
from .social import SocialStore
from .state import MessageStore, PeerStore, RuntimePaths

_GEOHASH_PARTICIPANT_TTL_SECONDS = 300


def resolve_peer_record(records: list[dict[str, object]], raw_target: str) -> dict[str, object]:
    target = raw_target.strip()
    if target.startswith("@"):
        target = target[1:]
    direct_match = [record for record in records if record.get("peer_id") == target]
    if direct_match:
        return direct_match[0]
    nickname_match = [record for record in records if record.get("nickname") == target]
    if len(nickname_match) == 1:
        return nickname_match[0]
    if len(nickname_match) > 1:
        raise ValueError(f"ambiguous nickname: {raw_target}")
    raise ValueError(f"unknown peer: {raw_target}")


def resolve_social_record(records: list[dict[str, object]], raw_target: str) -> dict[str, object]:
    target = raw_target.strip()
    if target.startswith("@"):
        target = target[1:]
    direct_match = [record for record in records if record.get("peer_id") == target]
    if direct_match:
        return direct_match[0]
    nickname_match = [record for record in records if record.get("nickname") == target]
    if len(nickname_match) == 1:
        return nickname_match[0]
    if len(nickname_match) > 1:
        raise ValueError(f"ambiguous nickname: {raw_target}")
    raise ValueError(f"unknown peer: {raw_target}")


def read_dm_records(runtime: RuntimePaths) -> list[dict[str, object]]:
    rows = MessageStore(runtime.inbox_path).read_all(deduplicate=True)
    return [row for row in rows if row.get("scope") == "dm"]


def read_public_records(runtime: RuntimePaths) -> list[dict[str, object]]:
    rows = MessageStore(runtime.inbox_path).read_all(deduplicate=True)
    return [row for row in rows if row.get("direction") == "inbound" and row.get("scope", "public") == "public"]


def attachment_label(row: dict[str, object]) -> str:
    mime_type = str(row.get("attachment_mime") or "")
    return "image" if mime_type.startswith("image/") else "file"


def social_store(runtime: RuntimePaths) -> SocialStore:
    return SocialStore(runtime.social_path)


def attachment_summary(row: dict[str, object]) -> str:
    label = attachment_label(row)
    name = str(row.get("attachment_name") or Path(str(row.get("content") or "file")).name or "file")
    size_bytes = row.get("attachment_size_bytes")
    size_suffix = f" ({format_file_size(int(size_bytes))})" if isinstance(size_bytes, int) else ""
    path_suffix = f" -> {row.get('content')}" if row.get("content") else ""
    return f"[{label}] {name}{size_suffix}{path_suffix}"


def geohash_participants(
    records: list[dict[str, object]],
    *,
    geohash: str,
    self_pubkey: str,
    now: datetime,
) -> list[dict[str, str]]:
    cutoff = now.astimezone(timezone.utc).timestamp() - _GEOHASH_PARTICIPANT_TTL_SECONDS
    participants: dict[str, dict[str, str]] = {}
    for row in records:
        if str(row.get("conversation_id") or "") != geohash:
            continue
        if str(row.get("message_kind") or "") not in {"geohash_message", "geohash_presence"}:
            continue
        peer_id = str(row.get("peer_id") or "")
        if not peer_id or peer_id == self_pubkey:
            continue
        timestamp_raw = str(row.get("timestamp") or "")
        if not timestamp_raw:
            continue
        timestamp = datetime.fromisoformat(timestamp_raw).astimezone(timezone.utc)
        if timestamp.timestamp() < cutoff:
            continue
        current = participants.get(peer_id)
        if current is not None and str(current["last_seen"]) >= timestamp.isoformat():
            continue
        participants[peer_id] = {
            "peer_id": peer_id,
            "sender": str(row.get("sender") or ""),
            "last_seen": timestamp.isoformat(),
            "message_kind": str(row.get("message_kind") or ""),
            "teleported": bool(row.get("teleported") or False),
        }
    return sorted(participants.values(), key=lambda row: row["last_seen"], reverse=True)


def resolve_peer_or_social_target(runtime: RuntimePaths, raw_target: str) -> dict[str, object]:
    peer_rows = PeerStore(runtime.peers_path).read_all()
    try:
        return resolve_peer_record(peer_rows, raw_target)
    except ValueError:
        social_rows = social_store(runtime).read_all()
        return resolve_social_record(social_rows, raw_target)


def pending_read_receipt_ids(rows: list[dict[str, object]], peer_id: str) -> list[str]:
    sent = {
        str(row.get("message_id"))
        for row in rows
        if row.get("message_kind") == "receipt"
        and row.get("direction") == "outbound"
        and row.get("peer_id") == peer_id
        and row.get("content") == "read_receipt"
    }
    pending: list[str] = []
    for row in rows:
        if row.get("message_kind") != "private_message":
            continue
        if row.get("direction") != "inbound":
            continue
        if row.get("peer_id") != peer_id:
            continue
        message_id = str(row.get("message_id") or "")
        if not message_id or message_id in sent:
            continue
        pending.append(message_id)
    return pending
