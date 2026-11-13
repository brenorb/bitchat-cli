---
name: verify-bitchat-interactive-chat
description: Check radio lease, stdin setup, interactive message dispatch and cleanup when changing the BitChat chat command.
---

# Verify interactive chat startup and exit

Interactive chat acquires the radio lease, loads state, starts transport, and opens input. Blank and local slash commands stay in the prompt loop. Text and direct-message commands use different transport calls. EOF or quit normally closes input, stops the transport and releases the lease.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-interactive-chat
sh scripts/check-cli-state-machine.sh "$skill/ChatSession.tla" "$skill/ChatSession-cleanup.cfg"
PYTHONPATH=src python "$skill/references/check-chat-load.py"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The initial state load sits outside the cleanup block at both refs.

| Transition | Source |
| --- | --- |
| Acquire radio, load configuration and seed peers | `app.py`, `run_chat`; `state.py`, `ActiveSessionLease`, `StateStore` |
| Start transport and install stdin reader | `app.py`, `run_chat`, `_open_stdin_reader` |
| Interpret slash commands, persist a nickname, or dispatch public or private text | `app.py`, `run_chat`, `_resolve_peer_target` |
| Return on EOF, quit or cancellation and dispose of resources | `app.py`, `run_chat` |

The model covers one session, startup and a repeated prompt. It abstracts routing to public send, private send, a local action or exit. It assumes cleanup calls succeed and omits BLE work behind send. The local fixture uses the real lease file and startup with malformed state in a temporary directory. It does not start Bluetooth.

```sh
sh scripts/check-cli-state-machine.sh "$skill/ChatSession.tla"
```

The current config fails `EndedWithoutResources`. Acquiring the lease and then failing to load state leaves the lease file pointing at the process in both reviewed versions. A subsequent process can reclaim it after the original PID exits. The cleanup alternative moves release into the load failure path and passes in 11 states. Rerun the model and fixture when moving initialization or cleanup boundaries.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
