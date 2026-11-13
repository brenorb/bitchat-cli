---
name: verify-bitchat-dm-routing
description: Check live BLE, offline queue, authorized Nostr fallback, and read-receipt selection when changing the BitChat direct-message commands.
---

# Verify direct-message routing and read receipts

The daemon tries a live BLE link first. A known BLE peer without a live link uses the BLE outbox. A peer outside the BLE peer table can use Nostr only when favorited, unblocked and linked to an npub. A successful submission is recorded in local history before the daemon replies.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4). Run the source checks from the repository root with Python dependencies available.

```sh
skill=.cursor/skills/verify-bitchat-dm-routing
sh scripts/check-cli-state-machine.sh "$skill/Routes.tla"
sh scripts/check-cli-state-machine.sh "$skill/Receipts.tla"
PYTHONPATH=src python "$skill/references/check-dm-guards.py"
PYTHONPATH=src python -m pytest -q tests/test_app.py -k 'dm_send or dm_history'
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. Both have the same route precedence, fallback eligibility and read-receipt selection.

| Transition | Source |
| --- | --- |
| Resolve target and submit to daemon | `app.py`, `run_dm_send`; `queries.py`, `resolve_peer_or_social_target` |
| Choose live BLE, offline BLE, authorized Nostr, or error | `app.py`, `handle_daemon_client` for `dm_send`, `_peer_has_live_ble_link`, `_eligible_nostr_fallback_record` |
| Record accepted submission or inbound delivery receipt | `app.py`, `handle_daemon_client`, `daemon_receipt_log`, `daemon_nostr_message_log` |
| Select visible unread inbound messages in text history | `app.py`, `run_dm_history`; `queries.py`, `pending_read_receipt_ids` |
| Send a read receipt on a live BLE link or via authorized Nostr and record it | `app.py`, `handle_daemon_client` for `dm_mark_read` |

The route model bounds one invocation and fixed link and contact state until a route is chosen. It abstracts publication and local recording to an accepted or error outcome. An accepted message ID describes local submission. Peer delivery uses a separate receipt. The receipt model covers sequential views of one inbound message with its outbound receipt still in the inspected history. JSON exits before the mark-read call. The model omits concurrent invocations, tail windows that omit a previous receipt, and changes to route selection while sending.

TLC checked the routing rules in 137 states and the receipt rules in 14. The mutant configs bypass fallback eligibility or the already-sent filter. In a disposable checkout, bypass the favorite and blocked check in `_eligible_nostr_fallback_record` or remove the `message_id in sent` branch from `pending_read_receipt_ids`. The replay fails on each mutation. The baseline replay and eight direct-message CLI tests passed. Rerun the checks and update the reviewed refs if changing either path. [Routing statechart](../../../docs/statecharts/dm-route.md)

## History tail and overlapping reads

```sh
sh scripts/check-cli-state-machine.sh "$skill/HistoryReceipts.tla" "$skill/HistoryReceipts-tail.cfg"
sh scripts/check-cli-state-machine.sh "$skill/HistoryReceipts.tla" "$skill/HistoryReceipts-concurrent.cfg"
sh scripts/check-cli-state-machine.sh "$skill/HistoryReceipts.tla" "$skill/HistoryReceipts-recheck.cfg"
PYTHONPATH=src python "$skill/references/check-history-receipts.py"
```

The tail config starts with a prior receipt and a visible inbound message. In `run_dm_history`, limiting rows happens before `pending_read_receipt_ids`, so a receipt outside that limit cannot suppress a new request. The overlapping-read config starts with two clients reading the same message before either submission records a result. The daemon's `dm_mark_read` loop attempts each supplied ID before appending a local receipt, without an existing-receipt lookup. Both configs fail `NoRepeatedReadReceipt`. The local fixture checks both client requests with a stub daemon at the reviewed refs. This establishes repeated submission, with wire delivery still dependent on the route. The 13-state recheck alternative covers an atomic duplicate check at dispatch.
