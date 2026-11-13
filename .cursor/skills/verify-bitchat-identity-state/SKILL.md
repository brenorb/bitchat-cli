---
name: verify-bitchat-identity-state
description: Check local identity creation, legacy migration, corrupt-state rejection, and atomic replacement when changing BitChat state loading.
---

# Verify BitChat identity loading and migration

The CLI creates identity state if the file is absent. It migrates missing optional fields in valid older files and preserves the original BLE keys. Current code rejects malformed fields before returning an identity and uses a staged replacement to save the file.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-identity-state
sh scripts/check-cli-state-machine.sh "$skill/IdentityState.tla"
PYTHONPATH=src python "$skill/references/check-optional-identity.py"
env -u XDG_CONFIG_HOME PYTHONPATH=src python -m pytest -q tests/test_state.py
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source in `src/bitchat_cli/state.py` |
| --- | --- |
| Choose absolute XDG config or the current or legacy path | `default_app_dir`, `default_state_path`, `RuntimePaths` |
| Generate fresh keys and defaults | `AppState.create`, `StateStore.load` |
| Validate existing fields and migrate missing data | `StateStore.load`, `_validate_data`, `AppState.to_json` |
| Stage, flush, replace and remove the temporary file | `StateStore.save` |

The model covers one load from an absent, valid legacy, valid current, or malformed file. The malformed case represents an invalid optional identity field in an otherwise valid record. Save failure represents a failure before replacement. The model abstracts individual keys, directory selection and filesystem permissions, and omits concurrent first loads.

TLC checked three rules in 12 states. `IdentityState-legacy.cfg` fails because an invalid optional Nostr key can produce a replacement in-memory identity. The same fixture under the tagged source with `--legacy` returns a replacement key and retains the BLE identity. Current source rejects the key. Both runs verify that the malformed file's bytes remain unchanged in this case. The tag uses direct writes. The current suite's replacement-failure test checks that the existing bytes remain and the temporary file is removed. Nineteen state tests passed. Rerun the checks when changing validation, migration or persistence and update the source refs.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
