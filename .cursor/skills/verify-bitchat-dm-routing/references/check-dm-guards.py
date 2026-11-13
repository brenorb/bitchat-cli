"""Check real route predicates and read-receipt selection with local records."""
from unittest.mock import Mock

from bitchat_cli.app import _eligible_nostr_fallback_record
from bitchat_cli.queries import pending_read_receipt_ids

store = Mock()
for favorite, blocked, linked in (
    (True, False, True),
    (False, False, True),
    (True, True, True),
    (True, False, False),
):
    store.get.return_value = {
        "favorite": favorite,
        "blocked": blocked,
        "nostr_npub": "fixture-key" if linked else "",
    }
    allowed = _eligible_nostr_fallback_record(store, "peer") is not None
    assert allowed == (favorite and not blocked and linked), store.get.return_value

incoming = {"message_kind": "private_message", "direction": "inbound",
            "peer_id": "peer", "message_id": "first"}
receipt = {**incoming, "message_kind": "receipt", "direction": "outbound",
           "content": "read_receipt"}
assert pending_read_receipt_ids([incoming], "peer") == ["first"]
assert pending_read_receipt_ids([incoming, receipt], "peer") == []
print("Only eligible contacts allow fallback. Recorded read receipts are excluded.")
