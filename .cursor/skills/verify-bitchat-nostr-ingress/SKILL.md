---
name: verify-bitchat-nostr-ingress
description: Check Nostr relay frame, gift wrap decoding and callback deduplication when changing BitChat direct-message receive.
---

# Verify Nostr direct-message ingress

The listener accepts event frames, decrypts the gift wrap for a watched identity, and decodes the BitChat message or receipt. It remembers the event ID only after successful decoding and calls the application callback at most once per ID during the client lifetime.

```sh
skill=.cursor/skills/verify-bitchat-nostr-ingress
sh scripts/check-cli-state-machine.sh "$skill/Ingress.tla"
PYTHONPATH=src python "$skill/references/check-nostr-ingress.py"
```

Use Java 11 or later and set `TLA2TOOLS` to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The receive dispatcher is unchanged between these refs.

| Transition | Source in `src/bitchat_cli/nostr.py` |
| --- | --- |
| Parse a relay event and reject a previously dispatched ID | `NostrDMClient._handle_relay_message`, `NostrEvent.from_dict` |
| Verify outer event and seal, decrypt, and check the rumor sender | `decrypt_private_message` |
| Decode BitChat payload type and send a structured callback | `decode_bitchat_payload`, `NostrDMClient._handle_relay_message` |

The model bounds one event ID and separates parse failure, invalid envelope and valid message or receipt. It abstracts cryptographic operations, malformed frame handling and connection recovery. Application route and block policy are separate from the callback boundary.

TLC checked 16 states. In a disposable checkout, bypass the prior-event-ID guard in `_handle_relay_message`. The local fixture detects a second callback, matching the failed `Ingress-mutant.cfg` invariant. Both reviewed refs passed with a wrong recipient and a repeated local envelope. Restore and rerun before updating reviewed refs.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
