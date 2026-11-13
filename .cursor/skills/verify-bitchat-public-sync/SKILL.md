---
name: verify-bitchat-public-sync
description: Check request windows, eligible replay candidates, and bounded caches when changing BitChat public synchronization.
---

# Verify public mesh synchronization

A sync request remembers the peer and opens a response window. The responder sends cached packets of a requested type when the request filter does not report them already present. Responses are local replays with a zero hop limit.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4). Run the source checks from the repository root with Python dependencies available.

```sh
skill=.cursor/skills/verify-bitchat-public-sync
sh scripts/check-cli-state-machine.sh "$skill/SyncWindow.tla"
sh scripts/check-cli-state-machine.sh "$skill/SyncCache.tla"
config="$(mktemp -d)"
XDG_CONFIG_HOME="$config" PYTHONPATH=src python -m pytest -q tests/test_sync.py tests/test_dedupe.py tests/test_transport.py -k 'sync or dedupe or rsr'
rm -rf "$config"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The request window, cache, and replay checks are the same in both versions. Main adds periodic announcements that also enter the cache.

| Transition | Source |
| --- | --- |
| Build filter, remember peer and attempt request delivery | `transport.py`, `send_request_sync`; `sync.py`, `RequestSyncManager.register_request`, `GCSFilter.build_filter` |
| Expire windows and reject unrequested or unknown ingress | `sync.py`, `RequestSyncManager.cleanup`, `is_valid_response`; `transport.py`, `_handle_decoded_packet` |
| Store latest announcements and bound public packet caches | `transport.py`, `_remember_sync_packet`, `_sync_candidate_ids`, `_prune_runtime_state` |
| Validate request, choose requested packet types, suppress reported hits and send zero-hop replies | `transport.py`, `_handle_request_sync`, `_supported_sync_types`, `_sync_response_candidates` |
| Deduplicate accepted packet payloads and expire or trim IDs | `dedupe.py`, `packet_cache_key`, `ExpiringSet.mark_seen`, `_evict`, `_trim` |

The window model bounds one tracked request and compares a response peer to it. The request opens the window before delivery is attempted. Ordinary ingress processing and signature validation follow separate source guards. The cache model bounds two entries of one cache type, three packet IDs and a Boolean result from the membership filter. It abstracts filter false positives, clock arithmetic, individual cache capacities, and packet bodies. Its no-duplicate claim concerns packets that the supplied filter reports as present.

TLC checked `ResponseRequiresRecentRequest` in five states and the cache properties in 31. The mutant configs remove the time-and-peer check or the filter hit check. In a disposable checkout, bypass `is_valid_response` in `_handle_decoded_packet` to make `test_unsolicited_rsr_public_message_is_rejected` fail. Bypass `GCSFilter.contains` in `_handle_request_sync` to make all three cache-suppression transport tests fail. The isolated baseline passed 29 selected sync, deduplication and file tests. Restore and rerun the checks before updating the reviewed refs.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
