---
name: verify-bitchat-inbox
description: Check history append keys, restart indexing, and receipt collisions when changing BitChat inbox persistence and reads.
---

# Verify inbox append indexing and receipt persistence

The inbox appends JSONL records and remembers their composite keys. A new store instance reconstructs its index from existing rows. For incoming delivery and read receipts, both callbacks use the same direction, scope, message kind, peer and message ID. The append key omits receipt subtype.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-inbox
sh scripts/check-cli-state-machine.sh "$skill/AppendIndex.tla"
PYTHONPATH=src python -m pytest -q tests/test_state.py -k message_store
PYTHONPATH=src python "$skill/references/check-receipt-records.py"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The append and index code is identical at these refs.

| Transition | Source |
| --- | --- |
| Append record, skip duplicate key and rebuild index after restart | `state.py`, `MessageStore.append`, `_ensure_append_index`, `_append_key` |
| Read raw records or deduplicate view rows | `state.py`, `read_all`, `_deduplicate`; `queries.py`, `read_dm_records`, `read_public_records` |
| Record incoming BLE or Nostr receipts | `app.py`, `daemon_receipt_log`, `daemon_nostr_message_log` |
| Select viewed DM history and emit unsent read receipts | `app.py`, `run_dm_history`; `queries.py`, `pending_read_receipt_ids` |

`AppendIndex.tla` covers a stable key, sequential appends and process restarts. It assumes a complete log and one active writer. `ReceiptRecords.tla` covers the two incoming receipt subtypes for one peer and message. The models omit concurrent appends, log corruption, and user history tail windows.

`AppendIndex-mutant.cfg` skips rebuilding keys from disk. In a disposable checkout, bypass the existing-file loop in `_ensure_append_index`. The restart source test detects the duplicate.

```sh
sh scripts/check-cli-state-machine.sh "$skill/ReceiptRecords.tla"
sh scripts/check-cli-state-machine.sh "$skill/ReceiptRecords.tla" "$skill/ReceiptRecords-distinct.cfg"
```

The first receipt config fails `AllDistinctReceiptsPersist`. A delivered receipt is appended, then the read receipt with the same key is skipped. The fixture writes both rows using the daemon's receipt fields and verifies that only the first is present at both source refs. The distinct-key model passes in four states. Rerun the source and model checks when changing the receipt schema and update the reviewed refs and outcomes.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
