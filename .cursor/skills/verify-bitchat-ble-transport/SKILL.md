---
name: verify-bitchat-ble-transport
description: Verify the central BLE discovery, notification registration, reconnect, and shutdown lifecycle when changing BitChat transport.
---

# Verify the Bluetooth central connection lifecycle

The central transport queues discoveries, opens a BLE link, subscribes to notifications, and then registers the client for use and shutdown. A transport stop can interrupt the subscription step.

## Run the checks

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4). Run from the checkout with the project's Python dependencies available.

```sh
skill=.cursor/skills/verify-bitchat-ble-transport
sh scripts/check-cli-state-machine.sh "$skill/Central.tla"
sh scripts/check-cli-state-machine.sh "$skill/Central.tla" "$skill/Central-cleanup.cfg"
PYTHONPATH=src python "$skill/references/check-cancelled-link.py"
PYTHONPATH=src python -m pytest -q tests/test_transport.py -k 'connect or scan or periodic_announce or peripheral_endpoint'
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and cited tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source in `src/bitchat_cli/transport.py` |
| --- | --- |
| Start scanner, bounded workers, and peripheral | `start`, `_scan_loop`, `_set_scanning` |
| Deduplicate discoveries and gate reconnects | `_handle_discovered_device`, `_enqueue_connect_candidate`, `_connect_worker`, `_can_attempt_reconnect` |
| Connect and register after notifications are enabled | `_connect_device` |
| Drop the active address and record backoff | `_on_disconnect`, `_record_reconnect_backoff` |
| Refresh presence with live links and shut down the periodic task | `_announce_loop`, `stop` |
| Cancel setup workers before disconnecting registered clients | `stop`, `_connect_worker`, `_connect_device` |

The model bounds one address and abstracts queue capacity, backoff time and scanner duty cycling. It assumes a link can remain open after its notification registration coroutine is cancelled, unless the BLE backend disconnects it. The cited release lacks periodic announcements. Both versions use the same worker and client registration sequence.

TLC checked `TrackedOnlyAfterNotify` in eight states. Running the checker with `Central-stop.cfg` finds `StopDisconnectsLink` failing. Discovery queues the address, a worker opens the link, and shutdown cancels the worker while notification setup is pending. The client is absent from the collection that shutdown disconnects. The checked-in fixture replays that sequence against both versions using local BLE doubles. It checks application cleanup calls and state. Actual link persistence depends on the BLE backend. Thirteen transport tests passed for central connection, scan, and lifecycle behavior.

The seven-state `Central-cleanup.cfg` represents explicit cleanup of an in-flight linked client as a model alternative. On changes, rerun these checks and [regenerate the chart](../../../docs/statecharts/ble-connection.md). Update the reviewed refs and expected replay outcome.
