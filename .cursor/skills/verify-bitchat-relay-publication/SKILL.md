---
name: verify-bitchat-relay-publication
description: Check relay acknowledgement, timeouts and aggregate success for BitChat Nostr direct messages and geohash posts.
---

# Verify relay publication results

Current BitChat accepts a publish when at least one relay acknowledges the requested event. Unrelated messages are ignored while waiting. A negative acknowledgement, timeout or transport error fails that relay attempt.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-relay-publication
sh scripts/check-cli-state-machine.sh "$skill/PublishAck.tla"
PYTHONPATH=src python "$skill/references/check-relay-ack.py"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source |
| --- | --- |
| Encode a Nostr DM or receipt, wrap it, choose relays, and publish | `nostr.py`, `send_private_message`, `send_geohash_private_message`, `send_receipt`, `send_geohash_receipt`, `_publish_event` |
| Derive the geohash identity and sign a message, then gather relay outcomes | `geochat.py`, `publish`, `create_ephemeral_geohash_event` |
| Match acknowledgement to the event ID or fail on rejection or deadline | `nostr.py`, `_wait_for_publish_ack`; `geochat.py`, `_publish_to_relay` |

The model bounds two relay attempts and abstracts their scheduling to outcome order. Nostr attempts relays serially and current geohash posts attempt them concurrently. The model covers text posts and encrypted DM and receipt publication. Presence uses an independent gather that reports errors when a relay fails. It shares the per-relay acknowledgement parser. The model abstracts event signing and timeout duration.

TLC checked `SuccessRequiresAck` in 13 states. `PublishAck-legacy.cfg` shows send completion being reported as success without any acknowledgement. The source replay replaces only socket transport and uses a negative response for the actual event ID. Current code reports relay rejection for both sends. With `--legacy` the same fixture verifies that both tagged paths return success without reading the response. Rerun and update the source refs on changes.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
