---
name: verify-bitchat-relay-directory
description: Check relay directory cache loading, invalid-data refresh, and download failure when changing BitChat geohash relay discovery.
---

# Verify geohash relay directory loading

A cache hit uses parsed rows. Missing or empty data triggers a download. If parsing still yields no rows, the loader removes the cache and retries once before returning the second parse result or propagating a download error.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-relay-directory
sh scripts/check-cli-state-machine.sh "$skill/RelayDirectory.tla"
PYTHONPATH=src python "$skill/references/check-directory-cache.py"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The cache logic is the same at both refs.

| Transition | Source in `src/bitchat_cli/geochat.py` |
| --- | --- |
| Read cache, download on absence, and retry after a failed parse | `load_relay_entries`, `_download_relay_csv` |
| Skip malformed CSV rows and select nearby relays | `_parse_relay_csv`, `closest_relays` |
| Load on first watch or publish if the daemon has no directory | `GeoChatDaemon._load_relay_entries`, `set_watches`, `publish` |

The model bounds one load and a single refresh attempt. It abstracts parsed directory contents to usable or empty and download to success or error. It omits stale-but-valid rows, concurrent writers, interrupted cache writes, and distance calculations.

TLC checked 24 states. `RelayDirectory-mutant.cfg` returns an invalid result without refreshing. The fixture uses a temporary cache and stub CSV responses, and passes at both refs. In a disposable checkout, force the first parse result to be returned regardless of row count. The malformed-cache fixture catches it. Restore and rerun before updating the reviewed refs.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
