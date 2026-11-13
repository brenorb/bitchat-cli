---
name: verify-bitchat-channel-policy
description: Check geohash disclosure, selected channel metadata and local topic settings when changing BitChat geo and topic commands.
---

# Verify geohash disclosure and channel settings

Send and watch commands warn at neighborhood precision and require an explicit force flag at block or building precision. Selecting a geohash channel clears earlier selections. Topic commands maintain local joined, retention and password settings.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-channel-policy
sh scripts/check-cli-state-machine.sh "$skill/GeoPrecision.tla"
sh scripts/check-cli-state-machine.sh "$skill/Channels.tla"
PYTHONPATH=src python "$skill/references/check-channel-policy.py"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The precision and selection gates are present at both refs.

| Transition | Source in `src/bitchat_cli/app.py` |
| --- | --- |
| Resolve the target geohash and apply precision and send options | `_normalize_geo_target`, `_geohash_privacy_warning`, `run_geo_send`, `run_geo_watch_add` |
| Set or clear channel flags, name, notes and selection | `run_geo_channel_set`, `run_geo_channel_show`, `run_geo_channel_list` |
| Join, leave, list, change password or change retention | `run_topic_join`, `run_topic_leave`, `run_topic_list`, `run_topic_password_set`, `run_topic_retain` |

The precision model bounds one valid geohash, three precision bands, and a possible daemon failure. The settings model bounds two geohashes, one local topic and sequential edits from empty state. It abstracts malformed geohashes, exact metadata values and password file parsing. Concurrent persistence and remote relay delivery are separate.

TLC checked 78 precision states and 48 settings states. Mutants bypass the force check or omit clearing previous selections. The actual source fixture runs against both refs with a temporary state directory and stub daemon. In a disposable checkout, remove the high-precision return in `run_geo_send` or the reset in `run_geo_channel_set`. The replay rejects each change. Five existing geo and topic CLI tests passed. Rerun the checks before updating the source refs. [Geohash command statechart](../../../docs/statecharts/geo-send.md)
