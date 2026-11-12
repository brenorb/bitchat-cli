---
name: verify-bitchat-daemon
description: Check BitChat daemon readiness, repeat start, and shutdown when changing foreground initialization or the daemon CLI.
---

# Verify daemon readiness and shutdown

A successful daemon start needs a ready reply. A caller that gets success too early can try to send before the command socket or its services can handle the request.

Run from the repository root with Java 11 or later and `TLA2TOOLS` pointing to the official [TLC 1.7.4 JAR](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
sh scripts/check-cli-state-machine.sh .cursor/skills/verify-bitchat-daemon/Daemon.tla
PYTHONPATH=src python .cursor/skills/verify-bitchat-daemon/references/check-readiness.py
```

## Coverage

Reviewed at `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source in `src/bitchat_cli/app.py` |
| --- | --- |
| Check a live cached daemon, reject a busy radio, spawn a new child and publish its PID | `run_daemon_start` |
| Open the command socket, start BLE and relay services, then expose readiness | `run_daemon_foreground` and its `runtime_status` handler |
| Observe readiness and child liveness, or time out and tear down the spawned child | `_wait_for_daemon_ready`, `run_daemon_start`, `_clear_daemon_runtime_files` |
| Request termination, wait for exit, or report that shutdown is still in progress | `run_daemon_stop`, `_wait_for_daemon_exit` and foreground cleanup |

The model bounds one spawned child, one owner that can prevent spawning, repeat start observations and one stop. It models a readiness observation at the instant a start may report success. A later exit can still follow a successful start. It groups service failures and deadline outcomes, treats the cached PID and its socket as belonging to the same modeled daemon, and leaves the radio lease race to [the radio lease skill](../verify-bitchat-radio-lease/SKILL.md). Status queries observe PID and socket data and do not change daemon state.

## Evidence

TLC reached 20 states and checked `SuccessRequiresReadyReply`. Run the same checker with `Daemon-legacy.cfg` or `Daemon-cached-mutant.cfg` as the second argument to see the property fail. Use pstack's `/statechart` to read the shortest paths.

- At the cited tag, a fresh start can return success immediately after spawning. A repeat start can also return success from a live recorded PID before the child acquires the radio or reports readiness. The source fixture with `--expect-legacy` observed both results on the tagged source.
- On current source, the fixture reports failures for both starts with a controlled `ready: false` reply. In a disposable checkout, replace the cached branch's `if existing_status.get("ready") is True:` with `if True:` and rerun the fixture. It catches a repeat start falsely reporting success.

Keep the installed lifecycle and repeat-start tests in `tests/test_installed_e2e.py` as integration checks. Update the reviewed commit only after comparing the model with the revised code and rerunning both model and implementation checks.
