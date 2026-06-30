from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .files import format_file_size
from .geochat import (
    decode_bounds,
    derive_nostr_identity,
    encode_geohash,
    encode_geohash_levels,
    geohash_levels,
    geohash_precision_for_level,
    lookup_place,
    normalize_geohash,
)
from .network import NetworkConfig
from .social import SocialStore
from .state import MessageStore, PeerStore, RuntimePaths, StateStore

_MESH_CONVERSATION_ID = "mesh"
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
    normalized_target = target.lower()
    direct_match = [record for record in records if record.get("peer_id") == target]
    if direct_match:
        return direct_match[0]
    pubkey_match = [
        record
        for record in records
        if str(record.get("nostr_pubkey_hex") or "").lower() == normalized_target
    ]
    if len(pubkey_match) == 1:
        return pubkey_match[0]
    if len(pubkey_match) > 1:
        raise ValueError(f"ambiguous Nostr pubkey: {raw_target}")
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


def _normalize_geo_target(raw: str) -> str:
    try:
        return normalize_geohash(raw)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def run_inbox(
    args: argparse.Namespace,
    *,
    runtime_factory=RuntimePaths,
    mesh_conversation_id: str = _MESH_CONVERSATION_ID,
) -> int:
    runtime = runtime_factory()
    records = [
        row
        for row in read_public_records(runtime)
        if str(row.get("conversation_id") or mesh_conversation_id) == mesh_conversation_id
    ]
    if args.tail is not None:
        records = records[-args.tail :]
    if args.json:
        print(json.dumps(records, indent=2))
        return 0
    if not records:
        print("no inbound messages recorded")
        return 0
    for row in records:
        content = (
            attachment_summary(row)
            if str(row.get("message_kind") or "") in {"public_file", "private_file"}
            else row["content"]
        )
        print(f"{row['timestamp']} #mesh <{row['sender']}> {content}")
    return 0


def run_geo_inbox(
    args: argparse.Namespace,
    *,
    runtime_factory=RuntimePaths,
    normalize_geo_target=_normalize_geo_target,
) -> int:
    runtime = runtime_factory()
    try:
        geohash = normalize_geo_target(args.geohash)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    records = [
        row
        for row in read_public_records(runtime)
        if str(row.get("conversation_id") or "") == geohash
        and str(row.get("message_kind") or "") == "geohash_message"
    ]
    if args.tail is not None:
        records = records[-args.tail :]
    if args.json:
        print(json.dumps(records, indent=2))
        return 0
    if not records:
        print(f"no inbound messages recorded for #{geohash}")
        return 0
    for row in records:
        suffixes: list[str] = []
        if row.get("pow_difficulty") is not None:
            suffixes.append(f"[{int(row['pow_difficulty'])}b]")
        if row.get("teleported"):
            suffixes.append("[teleport]")
        suffix = f" {' '.join(suffixes)}" if suffixes else ""
        print(f"{row['timestamp']} #{geohash} <{row['sender']}> {row['content']}{suffix}")
    return 0


def run_geo_levels(args: argparse.Namespace) -> int:
    try:
        levels = geohash_levels(args.geohash)
    except ValueError as exc:
        print(str(exc))
        return 1
    if args.json:
        print(json.dumps(levels, indent=2))
        return 0
    for level, value in levels.items():
        print(f"{level:14} #{value}")
    return 0


def run_geo_encode(args: argparse.Namespace) -> int:
    try:
        if args.all_levels:
            payload = encode_geohash_levels(float(args.lat), float(args.lon))
        else:
            level = args.level or "city"
            payload = {level: encode_geohash(float(args.lat), float(args.lon), precision=geohash_precision_for_level(level))}
    except ValueError as exc:
        print(str(exc))
        return 1
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    for level, value in payload.items():
        print(f"{level:14} #{value}")
    return 0


def run_geo_decode(
    args: argparse.Namespace,
    *,
    normalize_geo_target=_normalize_geo_target,
) -> int:
    try:
        geohash = normalize_geo_target(args.geohash)
        lat_bounds, lon_bounds = decode_bounds(geohash)
        center_lat = (lat_bounds[0] + lat_bounds[1]) / 2
        center_lon = (lon_bounds[0] + lon_bounds[1]) / 2
    except (RuntimeError, ValueError) as exc:
        print(str(exc))
        return 1
    payload = {
        "geohash": geohash,
        "center": {"lat": center_lat, "lon": center_lon},
        "bounds": {
            "lat_min": lat_bounds[0],
            "lat_max": lat_bounds[1],
            "lon_min": lon_bounds[0],
            "lon_max": lon_bounds[1],
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"geohash:  #{geohash}")
    print(f"center:   {center_lat:.6f}, {center_lon:.6f}")
    print(f"lat:      {lat_bounds[0]:.6f} .. {lat_bounds[1]:.6f}")
    print(f"lon:      {lon_bounds[0]:.6f} .. {lon_bounds[1]:.6f}")
    return 0


def run_geo_lookup(
    args: argparse.Namespace,
    *,
    state_store_factory=StateStore,
) -> int:
    state = state_store_factory().load()
    try:
        results = lookup_place(
            args.query,
            network_config=NetworkConfig.from_state(state),
            limit=int(args.limit),
        )
    except Exception as exc:
        print(str(exc))
        return 1
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    if not results:
        print("no place matches found")
        return 0
    print("network lookup; no query or coordinates were persisted")
    for index, row in enumerate(results, start=1):
        print(f"[{index}] {row['label']}")
        print(f"    {row['lat']:.6f}, {row['lon']:.6f}")
        levels = row.get("levels", {})
        if isinstance(levels, dict):
            rendered = ", ".join(f"{level}=#{value}" for level, value in levels.items())
            print(f"    {rendered}")
    return 0


def run_geo_who(
    args: argparse.Namespace,
    *,
    runtime_factory=RuntimePaths,
    state_store_factory=StateStore,
    now_factory=lambda: datetime.now(timezone.utc),
    normalize_geo_target=_normalize_geo_target,
) -> int:
    runtime = runtime_factory()
    try:
        geohash = normalize_geo_target(args.geohash)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    state = state_store_factory(runtime.state_path).load()
    participants = geohash_participants(
        read_public_records(runtime),
        geohash=geohash,
        self_pubkey=derive_nostr_identity(bytes.fromhex(state.nostr_device_seed_hex), geohash).public_key_hex,
        now=now_factory(),
    )
    social_index = {
        str(row.get("nostr_pubkey_hex") or row.get("peer_id") or "").lower(): row
        for row in social_store(runtime).read_all()
    }
    for row in participants:
        social_row = social_index.get(str(row.get("peer_id") or "").lower(), {})
        row["favorite"] = bool(social_row.get("favorite"))
        row["blocked"] = bool(social_row.get("blocked"))
        row["verified"] = bool(social_row.get("verified_at"))
    if args.json:
        print(json.dumps(participants, indent=2))
        return 0
    if not participants:
        print("no one else is around right now.")
        return 0
    print(f"#{geohash} participants")
    for row in participants:
        flags: list[str] = []
        if row.get("teleported"):
            flags.append("teleport")
        if row.get("favorite"):
            flags.append("favorite")
        if row.get("blocked"):
            flags.append("blocked")
        if row.get("verified"):
            flags.append("verified")
        suffix = f" [{' '.join(flags)}]" if flags else ""
        print(f"{row['sender']:20} {row['last_seen']}{suffix}")
    return 0


def run_dm_inbox(
    args: argparse.Namespace,
    *,
    runtime_factory=RuntimePaths,
) -> int:
    runtime = runtime_factory()
    peer_rows = PeerStore(runtime.peers_path).read_all()
    social_rows = social_store(runtime).read_all()
    peer_index = {row["peer_id"]: row for row in peer_rows}
    verified_index = {
        str(row.get("peer_id") or ""): bool(row.get("verified_at"))
        for row in social_rows
    }
    for row in social_rows:
        peer_index.setdefault(str(row["peer_id"]), row)
    conversations: dict[str, dict[str, object]] = {}
    for row in read_dm_records(runtime):
        conversation_id = row.get("conversation_id") or row.get("peer_id") or row.get("target_peer_id")
        if not conversation_id:
            continue
        summary = conversations.setdefault(
            str(conversation_id),
            {
                "conversation_id": str(conversation_id),
                "peer_id": str(conversation_id),
                "nickname": peer_index.get(str(conversation_id), {}).get("nickname", str(conversation_id)[:8]),
                "last_timestamp": "",
                "last_preview": "",
                "verified": verified_index.get(str(conversation_id), False),
            },
        )
        timestamp = str(row.get("timestamp") or "")
        if timestamp >= str(summary["last_timestamp"]):
            summary["last_timestamp"] = timestamp
            if row.get("message_kind") == "private_message":
                summary["last_preview"] = str(row.get("content") or "")
            elif row.get("message_kind") in {"public_file", "private_file"}:
                summary["last_preview"] = attachment_summary(row)
            else:
                summary["last_preview"] = str(row.get("content") or row.get("message_kind") or "")

    rows = sorted(
        conversations.values(),
        key=lambda row: str(row.get("last_timestamp") or ""),
    )
    if args.tail is not None:
        rows = rows[-args.tail :]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no direct-message conversations recorded")
        return 0
    for row in rows:
        suffix = " [verified]" if row.get("verified") else ""
        print(f"{row['nickname']:16} {row['peer_id']}  {row['last_timestamp']}  {row['last_preview']}{suffix}")
    return 0
