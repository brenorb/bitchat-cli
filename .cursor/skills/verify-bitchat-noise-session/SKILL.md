---
name: verify-bitchat-noise-session
description: Check private BLE payload buffering and Noise session transitions when changing handshake, direct send, reset or flush behavior.
---

# Verify private BLE payload buffering

A private BLE send without an established Noise session queues its payload before attempting the handshake. If the initial packet cannot be routed, the session resets and the payload stays queued. A successful handshake attempts to flush the queue. An encryption or link failure during flush puts the current item back at the head.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4). Run the source checks from the repository root with Python dependencies available.

```sh
skill=.cursor/skills/verify-bitchat-noise-session
sh scripts/check-cli-state-machine.sh "$skill/NoiseQueue.tla"
PYTHONPATH=src python -m pytest -q tests/test_noise.py tests/test_transport.py -k 'noise or private_message or private_file or receipt'
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and cited tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The modeled transport methods and Noise implementation are identical across those refs.

| Transition | Source |
| --- | --- |
| Send with an established session or enqueue and begin a handshake | `transport.py`, `_send_noise_payload`, `_queue_noise_payload`, `_initiate_noise_handshake` |
| Reset on missing link or transport cipher failure | `transport.py`, `_initiate_noise_handshake`, `_send_noise_payload`, `_handle_noise_encrypted` |
| Exchange the handshake messages and derive transport ciphers | `noise.py`, `NoiseSession.start_handshake`, `process_handshake_message`, `_finalize_handshake` |
| Flush pending sends on establishment, pause on error, or remove a completed queue | `transport.py`, `_handle_noise_handshake`, `_flush_pending_noise_payloads`, `_send_encrypted_payload_now` |
| Dispatch decrypted private messages, files and receipts | `transport.py`, `_handle_noise_encrypted`, `_handle_private_message_payload`, `_handle_private_file_payload`, `_handle_receipt_payload` |

The model bounds a single payload starting without a session, one peer, and a possible delivery receipt. It assumes a later send or handshake event can provide a retry trigger. It asserts neither automatic retry nor eventual delivery. It abstracts cipher internals, timestamps and the overflow policy that drops the oldest payload. Peer eviction can remove the pending queue.

TLC checked `QueueRetainsPending` and `SentAfterHandshake` in seven states. Supply `NoiseQueue-mutant.cfg` to see a handshake begin with the payload missing. In a disposable checkout, remove the queue call in `_send_noise_payload`. The test `test_private_message_without_session_queues_payload_and_starts_handshake` fails when looking up the queued message. After restoring source, eight selected tests passed, including the official Noise vector and local message and file flows. Rerun these checks and update the source refs when the queue or session transitions change.
