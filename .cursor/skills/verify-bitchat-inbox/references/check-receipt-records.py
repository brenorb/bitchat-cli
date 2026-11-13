"""Replay two inbound receipt types through the real inbox persistence."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from bitchat_cli.state import MessageStore

with TemporaryDirectory() as directory:
    path = Path(directory) / "inbox.jsonl"
    store = MessageStore(path)
    timestamp = datetime.now(timezone.utc)
    for kind in ("delivered", "read_receipt"):
        store.append(
            direction="system", sender="fixture", content=kind, timestamp=timestamp,
            peer_id="peer", message_id="message", scope="dm", conversation_id="peer",
            message_kind="receipt", delivery_state=kind,
        )
    rows = MessageStore(path).read_all()
    assert [row["content"] for row in rows] == ["delivered"], rows
print("The inbox kept the delivery receipt and suppressed the read receipt.")
