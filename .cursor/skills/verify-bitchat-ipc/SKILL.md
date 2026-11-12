---
name: verify-bitchat-ipc
description: Check daemon command framing, timeout, and send identifiers when changing BitChat's UNIX socket client or daemon replies.
---

# Verify daemon command replies

An accepted reply must end with a newline. A send that returns an identifier must also carry its nonempty identifier. These checks let a caller distinguish a confirmed reply from an incomplete exchange where work may already be queued.

Run from the repository root with Java 11 or later and `TLA2TOOLS` pointing to the official [TLC 1.7.4 JAR](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
sh scripts/check-cli-state-machine.sh .cursor/skills/verify-bitchat-ipc/IPC.tla
PYTHONPATH=src python .cursor/skills/verify-bitchat-ipc/references/check-replies.py
python -m pytest tests/test_daemon_client.py
```

## Coverage

Reviewed at `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source |
| --- | --- |
| Discover the socket, connect, send one line, read to newline or EOF and close | `src/bitchat_cli/daemon_client.py`, `DaemonClient.send_command` |
| Handle the request and emit a reply independently of the caller's timeout | `src/bitchat_cli/app.py`, `handle_daemon_client` |
| Decode and reject invalid replies or server errors | `DaemonClient.send_command` |
| Require a message or transfer ID for applicable commands | `DaemonClient._send_with_identifier`, `send_dm`, `send_dm_file`, `send_public_file`, `send_geo_dm` |

The model has one request and one server operation. A partial frame stands for syntactically valid JSON missing the final newline. It models timeout per socket operation, which is not a total request deadline. A timeout can occur before or after the server queues a request. It abstracts segmentation, JSON contents and the work queued by the transport.

## Evidence

TLC reached 54 states and checked `AcceptanceNeedsFrame` and `IdentifiedAcceptanceNeedsId`. Supply `IPC-frame-mutant.cfg` or `IPC-id-mutant.cfg` as the second checker argument to see the corresponding property fail. The tagged client accepted both an unterminated JSON reply and a successful DM reply with no ID. `IPC-legacy.cfg` has the old policy. Use pstack's `/statechart` to read the paths, including both violations in the tagged model.

The local controlled-socket fixture passes on current code. The same fixture with `--expect-legacy` observed both acceptances on the tag. In disposable current source, remove the incomplete-response guard or bypass the guard in `_send_with_identifier`, then rerun the fixture without the legacy option. It detects either regression. Restore the guard and rerun the checks before advancing the reviewed commit.
