---
name: verify-bitchat-file-transfer
description: Verify validation, deduplication, local save, and delivery receipts when changing public or private BitChat file transfers.
---

# Verify incoming file persistence

BitChat reports an incoming file after saving it. The private path then attempts a delivery receipt. Both receive handlers remember the transfer ID before the file is written, so the write and the deduplication record can disagree after an error.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4). Run the source checks from the repository root with Python dependencies available.

```sh
skill=.cursor/skills/verify-bitchat-file-transfer
sh scripts/check-cli-state-machine.sh "$skill/Incoming.tla"
PYTHONPATH=src python "$skill/references/check-save-retry.py"
config="$(mktemp -d)"
XDG_CONFIG_HOME="$config" PYTHONPATH=src python -m pytest -q tests/test_transport.py -k 'public_file or private_file'
rm -rf "$config"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The file handlers and `files.py` use the same sequence at both refs.

| Transition | Source |
| --- | --- |
| Read a regular file, enforce the byte limit, and derive its type | `files.py`, `load_outgoing_file`, `guess_mime_type` |
| Form and broadcast a public transfer or queue a private payload | `transport.py`, `send_public_file`, `send_private_file` |
| Check sender signature or decrypted private payload and mark the transfer ID | `transport.py`, `_handle_file_transfer`, `_handle_private_file_payload` |
| Choose image or file directory, sanitize the basename, allocate a unique path, and save | `files.py`, `save_incoming_file`, `sanitize_filename`, `_unique_path` |
| Record a saved file, cache public packets for sync, and attempt a private delivery receipt | `transport.py`, `_handle_file_transfer`, `_handle_private_file_payload` |

The model bounds one transfer ID and a possible retry within the deduplication window. It abstracts data and cryptographic validation to an eligibility flag. It omits collisions between separate saves, packet-level deduplication, partial writes and actual receipt delivery. The fixture exercises the transfer handler directly with a signed local packet and an injected failure before any bytes are written.

TLC checked `OnlySavedFilesReported` in 20 states. `Incoming-retry.cfg` also checks whether a suppressed duplicate implies the file was saved. Pstack finds a four-step failure ending with a retry suppressed after the first write failed. The source fixture reproduces it at both refs. `Incoming-after-save.cfg` checks a 26-state model alternative that remembers an ID after successful save. Rerun both baseline and retry checks on changes, then update the reviewed refs and expected outcomes. [File receive statechart](../../../docs/statecharts/file-receive.md)
