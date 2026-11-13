---
name: verify-bitchat-ble-peripheral
description: Check CoreBluetooth service registration, advertising recovery, notification backpressure and incoming write buffers when changing BitChat's macOS peripheral endpoint.
---

# Verify the macOS Bluetooth peripheral

The endpoint advertises the local service and sends notifications to subscribers. It queues updates during backpressure and clears partial incoming packets when the associated central unsubscribes.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4). Run the source replay from the repository root with Python dependencies available on macOS.

```sh
skill=.cursor/skills/verify-bitchat-ble-peripheral
sh scripts/check-cli-state-machine.sh "$skill/Peripheral.tla"
sh scripts/check-cli-state-machine.sh "$skill/WriteBuffer.tla"
PYTHONPATH=src python "$skill/references/check-power-cycle.py"
PYTHONPATH=src python -m pytest -q tests/test_peripheral.py
```

## Coverage

`src/bitchat_cli/peripheral.py` is identical at main `06e5ff8b5690de72361d9f17d167b5242a59671d` and the cited tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source in `src/bitchat_cli/peripheral.py` |
| --- | --- |
| Choose the native or null endpoint, start and install the local service | `create_peripheral_endpoint`, `start`, `_configure_if_ready`, `_install_service` |
| Handle manager power changes and service registration callbacks | `_handle_state_update`, `_handle_service_added`, `_start_advertising` |
| Queue notifications, bound pending work, and flush after a ready callback | `send`, `_resolve_centrals`, `_flush_pending_updates` |
| Subscribe or unsubscribe a central and forward callbacks to the transport loop | `_handle_subscribe`, `_handle_unsubscribe` |
| Collect offset writes, dispatch complete packets and drop oversized buffers | `_handle_write_requests`, `_append_pending_write_chunks` |
| Clear subscriptions and queues and stop advertising | `stop` |

`Peripheral.tla` abstracts one service and a queue of two slots, representing the production queue of 256 slots. Each flush either sends an item or leaves it queued on backpressure. CoreBluetooth calls are abstracted as separate actions. Registration errors may leave the service pending. The model omits parallel callbacks from earlier service generations. `WriteBuffer.tla` covers one central across two or more subscriptions. It abstracts packet validation and byte limits to incomplete, decodable or oversized inputs.

The 15-state `Peripheral.cfg` checks queue bounds and clearing on stop. `Peripheral-ready.cfg` finds that a power cycle can trigger advertising while the service readiness flag is false. The local replay invokes the actual state handlers with a CoreBluetooth double and sees a second advertising request before another service-added callback. Hardware behavior depends on CoreBluetooth. The `Peripheral-guard.cfg` model alternative requires readiness and passes in 12 states. Ten source peripheral tests passed.

`WriteBuffer-mutant.cfg` skips clearing a partial write on unsubscribe. In a disposable checkout, remove the corresponding `_pending_write_buffers.pop` from `_handle_unsubscribe`. The source test `test_handle_unsubscribe_clears_pending_write_buffer` fails. On changes, restore and rerun the source tests, rerun the checks including the readiness case, and update the reviewed refs.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
