---
name: verify-bitchat-fragments
description: Check BitChat BLE fragment accumulation, expiry, bounded capacity, and completion when changing packet reassembly.
---

# Verify fragment reassembly

A fragment retained past the assembly deadline must never complete a packet. Otherwise a late delivery can combine with data the receiver had already stopped waiting for.

Run with Java 11 or later and `TLA2TOOLS` pointing to the official [TLC 1.7.4 JAR](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
sh scripts/check-cli-state-machine.sh .cursor/skills/verify-bitchat-fragments/Fragments.tla
PYTHONPATH=src python .cursor/skills/verify-bitchat-fragments/references/check-expiry.py
python -m pytest tests/test_fragments.py
```

## Coverage

Reviewed at `06e5ff8b5690de72361d9f17d167b5242a59671d` and `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The buffer implementation is identical at these refs.

| Transition | Source |
| --- | --- |
| Send short packets directly or split large ones into an indexed sequence | `src/bitchat_cli/fragments.py`, `fragment_packet_sequence` |
| Validate the header, key by sender and fragment ID, prune expired groups and evict the oldest group when full | `FragmentHeader.from_packet`, `FragmentAssemblyBuffer.append`, `remove_expired`, `_ensure_capacity` |
| Discard metadata mismatches, replace repeated indexes, join complete groups in order and remove the completed group | `FragmentAssemblyBuffer.append` and `_discard` |
| Process a reconstructed packet | `src/bitchat_cli/transport.py`, `BLEChatTransport` receive path |

The model bounds one buffer slot, two group keys and two indexes per group. A timer bucket distinguishes fresh from expired, and repeated indexes are idempotent in the completeness calculation. It abstracts bytes, decoding, post-assembly validation and timer duration.

TLC checked `NeverReassembleExpired` in 13 states. Supply `Fragments-mutant.cfg` as the second argument to see a late fragment complete an expired group. Use pstack's `/statechart` to interpret the trace. In a disposable checkout, skip the `remove_expired` call in `append` and rerun the fixture. It detects stale completion with valid locally generated fragments. Restore and rerun before advancing the reviewed commit.
