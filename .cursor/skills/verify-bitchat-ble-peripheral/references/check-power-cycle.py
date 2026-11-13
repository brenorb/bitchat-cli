"""Replay the endpoint's state callbacks on a local CoreBluetooth double."""
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

from bitchat_cli.peripheral import MacOSPeripheralEndpoint

endpoint = MacOSPeripheralEndpoint.__new__(MacOSPeripheralEndpoint)
endpoint._CoreBluetooth = SimpleNamespace(
    CBManagerStatePoweredOn=1,
    CBAdvertisementDataLocalNameKey="name",
    CBAdvertisementDataServiceUUIDsKey="services",
)
endpoint._system_log = lambda _message: None
endpoint._started = False
endpoint._service_ready = False
endpoint._is_advertising = False
endpoint._service = None
endpoint._characteristic = None
endpoint._pending_updates = deque()
endpoint._pending_write_buffers = {}
manager = Mock()
manager.state = 0
endpoint._manager = manager


def install_service():
    endpoint._service = SimpleNamespace(UUID=lambda: "fixture-service")
    endpoint._characteristic = object()


# Replace ObjC allocation only. The power and advertising decisions are production methods.
endpoint._install_service = install_service
endpoint.start()
manager.state = 1
endpoint._handle_state_update(manager)
endpoint._handle_service_added(manager, None)
assert endpoint._service_ready and endpoint.is_advertising()
assert manager.startAdvertising_.call_count == 1

manager.state = 0
endpoint._handle_state_update(manager)
assert not endpoint._service_ready and not endpoint.is_advertising()
manager.state = 1
endpoint._handle_state_update(manager)
assert endpoint.is_advertising() and not endpoint._service_ready
assert manager.startAdvertising_.call_count == 2
print("Power restoration requested advertising before service readiness was restored.")
