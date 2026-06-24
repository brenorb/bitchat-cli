from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path

from .protocol import FilePacket
from .state import RuntimePaths

MAX_TRANSFER_SIZE_BYTES = 8 * 1024 * 1024


def load_outgoing_file(path: Path) -> FilePacket:
    source = path.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"file not found: {source}")
    if not source.is_file():
        raise ValueError(f"not a file: {source}")
    content = source.read_bytes()
    if len(content) > MAX_TRANSFER_SIZE_BYTES:
        raise ValueError(f"file exceeds {MAX_TRANSFER_SIZE_BYTES} byte transfer limit")
    return FilePacket(
        file_name=source.name,
        file_size=len(content),
        mime_type=guess_mime_type(source.name),
        content=content,
    )


def save_incoming_file(runtime: RuntimePaths, packet: FilePacket) -> Path:
    runtime.ensure()
    target_dir = runtime.incoming_images_dir if packet.mime_type.lower().startswith("image/") else runtime.incoming_files_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(packet.file_name)
    target = _unique_path(target_dir, safe_name)
    target.write_bytes(packet.content)
    return target


def transfer_id_for_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def guess_mime_type(file_name: str) -> str:
    mime_type, _ = mimetypes.guess_type(file_name)
    return mime_type or "application/octet-stream"


def format_file_size(size_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{int(size_bytes)} B"


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name.strip()) or "file"
    return cleaned[:128]


def _unique_path(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("failed to allocate unique incoming file path")
