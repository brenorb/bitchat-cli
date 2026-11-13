---
name: verify-bitchat-peer-directory
description: Check timestamp-ordered peer observations and independent social overlay flags when changing BitChat peer and contact persistence.
---

# Verify peer observations and contact overlays

The peer store retains details from the newest observation. The social store preserves unmentioned fields while setting a link, favorite, block or verification flag. Deleting the social record removes those overlays.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-peer-directory
sh scripts/check-cli-state-machine.sh "$skill/PeerDirectory.tla"
PYTHONPATH=src python -m pytest -q tests/test_state.py -k peer_store
PYTHONPATH=src python "$skill/references/check-social-overlay.py"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The two stores' update logic is identical at these refs.

| Transition | Source |
| --- | --- |
| Read prior observation, replace on equal or newer timestamp, retain older stored details otherwise | `state.py`, `PeerStore.upsert`, `read_all`, `_write_all` |
| Read by peer ID or Nostr pubkey, update independent fields, delete record | `social.py`, `SocialStore.get`, `get_by_pubkey`, `upsert`, `delete` |
| Resolve CLI target, apply link or overlay changes, and verify fingerprint | `app.py`, `run_social_link`, `run_social_unlink`, `run_social_verify`, favorite/block handlers; `queries.py`, `resolve_peer_or_social_target` |

The model bounds one peer, three timestamps and Boolean social overlays. It abstracts each file operation to a successful sequential update. It omits concurrent writers, partial file writes, key material and fingerprint calculation. Local source tests cover retained addresses and independent flag updates.

TLC checked 64 states. The stale-observation mutant accepts an older peer record. Bypassing the timestamp guard makes `test_peer_store_preserves_newer_addresses_when_older_observation_arrives` fail. The omitted-field mutant clears a favorite during another update. In a disposable checkout, replace the favorite fallback in `SocialStore.upsert` with false to reproduce the failure in the fixture. Restore, rerun, and update reviewed refs after changing either store.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
