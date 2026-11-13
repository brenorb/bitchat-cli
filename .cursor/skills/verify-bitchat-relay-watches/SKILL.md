---
name: verify-bitchat-relay-watches
description: Check geohash and Nostr DM subscription reconciliation, cancellation and shutdown when changing BitChat relay watches.
---

# Verify relay watch reconciliation

Geohash watches derive a set of relay and geohash pairs. Direct-message watches derive relay and identity pairs from the main identity and extra watched geohash identities. Both reconcile the desired set against running tasks and cancel obsolete tasks.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-relay-watches
sh scripts/check-cli-state-machine.sh "$skill/Watches.tla"
PYTHONPATH=src python "$skill/references/check-watch-tasks.py"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The watch reconciliation and shutdown sequence is the same on both refs.

| Transition | Source |
| --- | --- |
| Normalize watch targets, choose relays and reconcile subscriptions | `geochat.py`, `GeoChatDaemon.start`, `set_watches`; `app.py`, `handle_daemon_client` geo watch branch |
| Derive DM identities and reconcile their relay tasks | `app.py`, `sync_geohash_dm_watchers`; `nostr.py`, `set_extra_watch_identities`, `_sync_watch_tasks` |
| Open subscriptions, receive, retry, and stop | `geochat.py` and `nostr.py`, `_watch_loop`, `stop` |
| Deduplicate and deliver valid received records, and periodically publish presence for short geohashes | `geochat.py`, `_handle_relay_message`, `_presence_loop`; `nostr.py`, `_handle_relay_message` |

The model bounds two subscription keys and one reconciliation at a time. The desired Nostr set represents the full target set after including the primary identity. It abstracts network and task scheduling and checks the installed set when reconciliation completes. It omits concurrent updates, receive timing, directory fetch, and presence cadence. The fixture uses actual task creation and cancellation with sleeping coroutine doubles.

TLC checked `TasksMatchWatches` in 21 states. The mutant leaves obsolete tasks installed. In a disposable checkout, skip the loop over `existing - desired` in `GeoChatDaemon.set_watches`; the fixture detects the retained task. Restore and rerun the checks when the watch key set or cancellation behavior changes.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
