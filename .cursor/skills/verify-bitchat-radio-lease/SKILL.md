---
name: verify-bitchat-radio-lease
description: Model-check exclusive BitChat radio ownership when changing ActiveSessionLease, foreground startup, or daemon startup and cleanup.
---

# Verify BitChat radio lease acquisition

Only one BitChat session should acquire the local radio lease at a time. Two successful acquisitions allow foreground and daemon startup to proceed concurrently against the same Bluetooth adapter.

Run with Java 11 or later and `TLA2TOOLS` set to the absolute path of the official [TLA+ 1.7.4 release JAR](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
sh scripts/check-cli-state-machine.sh .cursor/skills/verify-bitchat-radio-lease/RadioLease.tla
```

## Coverage

Reviewed at `06e5ff8b5690de72361d9f17d167b5242a59671d`. The `0.1.0` tag at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b` has the same `ActiveSessionLease` methods.

| Model | Source |
| --- | --- |
| Read the current owner, reject a different live owner, or write this process's PID. | `src/bitchat_cli/state.py`, `ActiveSessionLease.acquire` and `read_owner_pid` |
| Observe ownership before unlinking the lease file. | `ActiveSessionLease.release` |
| Both launch paths use the same radio PID path and proceed on a successful acquisition. | `src/bitchat_cli/app.py`, foreground BLE startup and `run_daemon_foreground`, and `RuntimePaths.radio_pid_path` |

The model bounds one lease file and two live contenders starting from an absent or stale owner. It treats reading, writing and unlinking as separate operations, assumes each file operation succeeds, and abstracts PID reuse and the Bluetooth adapter's response after startup.

## Known finding

`OneSessionAcquiresLease` fails. Both processes can read an absent owner before either writes, and then both acquisitions return success. pstack's `/statechart` reached 91 states and found the four-step path. `NoDeadlock` held.

Replay against the actual lease class in the project's Python environment. The fixture uses a temporary file, two fake PIDs, and a barrier after the real owner reads.

```sh
PYTHONPATH=src python .cursor/skills/verify-bitchat-radio-lease/references/radio-lease-trace.py
```

The reviewed source returned success for both acquisitions. The existing sequential lease test also passed.

## Verify a change

1. Compare the covered methods with the reviewed commit and update the model at every synchronization boundary that changes.
2. Run the checker and interpret counterexamples using pstack's `/statechart`. Replay changes in the local fixture. When a fix is intentional, update the fixture to assert that at most one acquisition succeeds and match the synchronization in the model.
3. Advance the reviewed commit after confirming the model describes the code.
