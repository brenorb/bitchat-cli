from __future__ import annotations

import asyncio
import sys
from collections import deque
from typing import Callable, Protocol

from .constants import MAX_PACKET_SIZE
from .protocol import Packet

SystemLogger = Callable[[str], None]
WriteHandler = Callable[[str, bytes], None]
LifecycleHandler = Callable[[str], None]
_MAX_PENDING_UPDATES = 256
_DELEGATE_CLASS = None


class PeripheralEndpoint(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def subscriber_count(self) -> int: ...

    def subscriber_addresses(self) -> tuple[str, ...]: ...

    def recent_events(self) -> tuple[dict[str, object], ...]: ...

    def is_advertising(self) -> bool: ...

    def send(
        self,
        data: bytes,
        *,
        target_addresses: set[str] | None = None,
        exclude_addresses: set[str] | None = None,
    ) -> None: ...


class NullPeripheralEndpoint:
    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def subscriber_count(self) -> int:
        return 0

    def subscriber_addresses(self) -> tuple[str, ...]:
        return ()

    def recent_events(self) -> tuple[dict[str, object], ...]:
        return ()

    def is_advertising(self) -> bool:
        return False

    def send(
        self,
        data: bytes,
        *,
        target_addresses: set[str] | None = None,
        exclude_addresses: set[str] | None = None,
    ) -> None:
        return None


def peripheral_supported() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import CoreBluetooth  # noqa: F401
        import Foundation  # noqa: F401
        import libdispatch  # noqa: F401
        import objc  # noqa: F401
    except ImportError:
        return False
    return True


def create_peripheral_endpoint(
    *,
    service_uuid: str,
    characteristic_uuid: str,
    system_log: SystemLogger,
    on_write: WriteHandler,
    on_subscribe: LifecycleHandler,
    on_unsubscribe: LifecycleHandler,
    loop: asyncio.AbstractEventLoop | None = None,
) -> PeripheralEndpoint:
    if not peripheral_supported():
        return NullPeripheralEndpoint()
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return NullPeripheralEndpoint()
    return MacOSPeripheralEndpoint(
        service_uuid=service_uuid,
        characteristic_uuid=characteristic_uuid,
        system_log=system_log,
        on_write=on_write,
        on_subscribe=on_subscribe,
        on_unsubscribe=on_unsubscribe,
        loop=loop,
    )


def _objc_attr(obj: object, name: str) -> object | None:
    value = getattr(obj, name, None)
    if callable(value):
        return value()
    return value


def _uuid_string(value: object | None) -> str:
    if value is None:
        return ""
    uuid_string = getattr(value, "UUIDString", None)
    if callable(uuid_string):
        return str(uuid_string()).upper()
    return str(value).upper()


def _central_address(central: object) -> str:
    return f"peripheral:{_uuid_string(_objc_attr(central, 'identifier'))}"


def _delegate_class():
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is not None:
        return _DELEGATE_CLASS

    from Foundation import NSObject
    import objc

    class PeripheralManagerDelegate(NSObject):
        def initWithEndpoint_(self, endpoint):
            self = objc.super(PeripheralManagerDelegate, self).init()
            if self is None:
                return None
            self._endpoint = endpoint
            return self

        def peripheralManagerDidUpdateState_(self, manager) -> None:
            self._endpoint._handle_state_update(manager)

        def peripheralManager_didAddService_error_(self, manager, _service, error) -> None:
            self._endpoint._handle_service_added(manager, error)

        def peripheralManager_central_didSubscribeToCharacteristic_(self, _manager, central, _characteristic) -> None:
            self._endpoint._handle_subscribe(central)

        def peripheralManager_central_didUnsubscribeFromCharacteristic_(self, _manager, central, _characteristic) -> None:
            self._endpoint._handle_unsubscribe(central)

        def peripheralManager_didReceiveWriteRequests_(self, manager, requests) -> None:
            self._endpoint._handle_write_requests(manager, requests)

        def peripheralManagerIsReadyToUpdateSubscribers_(self, _manager) -> None:
            self._endpoint._flush_pending_updates()

    _DELEGATE_CLASS = PeripheralManagerDelegate
    return PeripheralManagerDelegate


def _create_dispatch_queue():
    import libdispatch

    return libdispatch.dispatch_queue_create(b"bitchat.peripheral", None)


def _create_peripheral_manager(core_bluetooth, delegate, queue):
    return core_bluetooth.CBPeripheralManager.alloc().initWithDelegate_queue_options_(
        delegate,
        queue,
        None,
    )


def _characteristic_spec(core_bluetooth) -> tuple[int, int]:
    properties = (
        core_bluetooth.CBCharacteristicPropertyNotify
        | core_bluetooth.CBCharacteristicPropertyRead
        | core_bluetooth.CBCharacteristicPropertyWrite
        | core_bluetooth.CBCharacteristicPropertyWriteWithoutResponse
    )
    permissions = (
        core_bluetooth.CBAttributePermissionsReadable
        | core_bluetooth.CBAttributePermissionsWriteable
    )
    return properties, permissions


_RECENT_EVENT_LIMIT = 8
_PENDING_WRITE_CAP_BYTES = MAX_PACKET_SIZE


class MacOSPeripheralEndpoint:
    def __init__(
        self,
        *,
        service_uuid: str,
        characteristic_uuid: str,
        system_log: SystemLogger,
        on_write: WriteHandler,
        on_subscribe: LifecycleHandler,
        on_unsubscribe: LifecycleHandler,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        import CoreBluetooth
        from Foundation import NSData

        self._CoreBluetooth = CoreBluetooth
        self._NSData = NSData
        self._service_uuid = service_uuid.upper()
        self._characteristic_uuid = characteristic_uuid.upper()
        self._system_log = system_log
        self._on_write = on_write
        self._on_subscribe = on_subscribe
        self._on_unsubscribe = on_unsubscribe
        self._loop = loop
        self._started = False
        self._service_ready = False
        self._is_advertising = False
        self._subscribed_centrals: dict[str, object] = {}
        self._pending_updates: deque[tuple[bytes, tuple[object, ...] | None]] = deque()
        self._pending_write_buffers: dict[str, bytes] = {}
        self._recent_events: deque[dict[str, object]] = deque(maxlen=_RECENT_EVENT_LIMIT)
        self._characteristic = None
        self._service = None
        self._dispatch_queue = _create_dispatch_queue()
        delegate_class = _delegate_class()
        self._delegate = delegate_class.alloc().initWithEndpoint_(self)
        self._manager = _create_peripheral_manager(CoreBluetooth, self._delegate, self._dispatch_queue)

    def start(self) -> None:
        self._started = True
        self._configure_if_ready()

    def stop(self) -> None:
        self._started = False
        self._subscribed_centrals.clear()
        self._pending_updates.clear()
        self._pending_write_buffers.clear()
        self._service_ready = False
        self._is_advertising = False
        try:
            self._manager.stopAdvertising()
        except Exception:
            pass
        remove_all_services = getattr(self._manager, "removeAllServices", None)
        if callable(remove_all_services):
            try:
                remove_all_services()
            except Exception:
                pass
        self._characteristic = None
        self._service = None

    def subscriber_count(self) -> int:
        return len(self._subscribed_centrals)

    def subscriber_addresses(self) -> tuple[str, ...]:
        return tuple(sorted(self._subscribed_centrals))

    def recent_events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in self._recent_events)

    def is_advertising(self) -> bool:
        return self._is_advertising

    def _record_recent_event(self, event: str, address: str, **details: object) -> None:
        item: dict[str, object] = {
            "event": event,
            "address": address,
        }
        item.update(details)
        self._recent_events.append(item)

    def _append_pending_write_chunks(
        self,
        address: str,
        chunks: list[tuple[int, bytes]],
    ) -> bytes | None:
        combined = bytearray(self._pending_write_buffers.get(address, b""))
        appended_bytes = 0
        for offset, data in chunks:
            if not data:
                continue
            appended_bytes += len(data)
            end = offset + len(data)
            if len(combined) < end:
                combined.extend(b"\x00" * (end - len(combined)))
            combined[offset:end] = data
        encoded = bytes(combined)
        if len(encoded) > _PENDING_WRITE_CAP_BYTES:
            self._pending_write_buffers.pop(address, None)
            self._record_recent_event(
                "write_oversized",
                address,
                bytes=appended_bytes,
                total_bytes=len(encoded),
            )
            self._system_log(
                f"Dropped oversized pending peripheral write buffer from {address}: {len(encoded)} bytes."
            )
            return None
        try:
            Packet.decode(encoded)
        except ValueError:
            self._pending_write_buffers[address] = encoded
            self._record_recent_event(
                "write_buffered",
                address,
                bytes=appended_bytes,
                total_bytes=len(encoded),
            )
            return None
        self._pending_write_buffers.pop(address, None)
        self._record_recent_event(
            "write_decoded",
            address,
            bytes=appended_bytes,
            total_bytes=len(encoded),
        )
        return encoded

    def send(
        self,
        data: bytes,
        *,
        target_addresses: set[str] | None = None,
        exclude_addresses: set[str] | None = None,
    ) -> None:
        if not self._started or self._characteristic is None:
            return
        centrals = self._resolve_centrals(
            target_addresses=target_addresses,
            exclude_addresses=exclude_addresses,
        )
        if centrals == ():
            return
        if len(self._pending_updates) >= _MAX_PENDING_UPDATES:
            self._pending_updates.popleft()
            self._system_log("Dropped oldest pending peripheral update: subscriber backpressure is full.")
        self._pending_updates.append((data, centrals))
        self._flush_pending_updates()

    def _configure_if_ready(self) -> None:
        state = _objc_attr(self._manager, "state")
        if not self._started or state != self._CoreBluetooth.CBManagerStatePoweredOn:
            return
        if self._characteristic is None or self._service is None:
            self._install_service()
            return
        if not self._is_advertising:
            self._start_advertising()

    def _install_service(self) -> None:
        service_uuid = self._CoreBluetooth.CBUUID.UUIDWithString_(self._service_uuid)
        characteristic_uuid = self._CoreBluetooth.CBUUID.UUIDWithString_(self._characteristic_uuid)
        properties, permissions = _characteristic_spec(self._CoreBluetooth)
        characteristic = self._CoreBluetooth.CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            characteristic_uuid,
            properties,
            None,
            permissions,
        )
        service = self._CoreBluetooth.CBMutableService.alloc().initWithType_primary_(service_uuid, True)
        service.setCharacteristics_([characteristic])
        remove_all_services = getattr(self._manager, "removeAllServices", None)
        if callable(remove_all_services):
            remove_all_services()
        self._characteristic = characteristic
        self._service = service
        self._service_ready = False
        self._manager.addService_(service)

    def _start_advertising(self) -> None:
        if not self._started or self._service is None or self._is_advertising:
            return
        advertisement = {
            self._CoreBluetooth.CBAdvertisementDataLocalNameKey: "BitChat",
            self._CoreBluetooth.CBAdvertisementDataServiceUUIDsKey: [self._service.UUID()],
        }
        self._manager.startAdvertising_(advertisement)
        self._is_advertising = True
        self._system_log("Advertising as a BitChat BLE peripheral.")

    def _flush_pending_updates(self) -> None:
        if not self._started or self._characteristic is None or not self._service_ready:
            return
        while self._pending_updates:
            data, centrals = self._pending_updates[0]
            payload = self._NSData.dataWithBytes_length_(data, len(data))
            target_centrals = list(centrals) if centrals is not None else None
            accepted = self._manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                payload,
                self._characteristic,
                target_centrals,
            )
            if not accepted:
                break
            self._pending_updates.popleft()

    def _resolve_centrals(
        self,
        *,
        target_addresses: set[str] | None,
        exclude_addresses: set[str] | None,
    ) -> tuple[object, ...] | None:
        if target_addresses is not None:
            selected = [
                central
                for address, central in self._subscribed_centrals.items()
                if address in target_addresses
            ]
            return tuple(selected)
        excluded = exclude_addresses or set()
        if not excluded:
            return None
        selected = [
            central
            for address, central in self._subscribed_centrals.items()
            if address not in excluded
        ]
        return tuple(selected)

    def _handle_state_update(self, manager) -> None:
        state = _objc_attr(manager, "state")
        if state != self._CoreBluetooth.CBManagerStatePoweredOn:
            self._service_ready = False
            self._is_advertising = False
            self._pending_write_buffers.clear()
            return
        self._configure_if_ready()

    def _handle_service_added(self, _manager, error) -> None:
        if error is not None:
            self._system_log(f"Failed to register peripheral service: {error}")
            return
        self._service_ready = True
        self._start_advertising()
        self._flush_pending_updates()

    def _handle_subscribe(self, central: object) -> None:
        address = _central_address(central)
        self._subscribed_centrals[address] = central
        self._record_recent_event("subscribe", address)
        self._loop.call_soon_threadsafe(self._on_subscribe, address)

    def _handle_unsubscribe(self, central: object) -> None:
        address = _central_address(central)
        self._subscribed_centrals.pop(address, None)
        self._pending_write_buffers.pop(address, None)
        self._record_recent_event("unsubscribe", address)
        self._loop.call_soon_threadsafe(self._on_unsubscribe, address)

    def _handle_write_requests(self, manager, requests) -> None:
        grouped: dict[str, list[tuple[object, int, bytes]]] = {}
        for request in requests:
            central = _objc_attr(request, "central")
            characteristic = _objc_attr(request, "characteristic")
            offset = int(_objc_attr(request, "offset") or 0)
            if (
                central is None
                or characteristic is None
                or self._characteristic is None
            ):
                manager.respondToRequest_withResult_(
                    request,
                    self._CoreBluetooth.CBATTErrorInvalidOffset,
                )
                continue
            characteristic_uuid = _uuid_string(_objc_attr(characteristic, "UUID"))
            if characteristic_uuid != self._characteristic_uuid:
                manager.respondToRequest_withResult_(
                    request,
                    self._CoreBluetooth.CBATTErrorAttributeNotFound,
                )
                continue
            value = _objc_attr(request, "value")
            data = bytes(value) if value is not None else b""
            address = _central_address(central)
            manager.respondToRequest_withResult_(request, self._CoreBluetooth.CBATTErrorSuccess)
            grouped.setdefault(address, []).append((central, offset, data))
        for address, entries in grouped.items():
            chunks = [(offset, data) for _central, offset, data in sorted(entries, key=lambda item: item[1])]
            combined = self._append_pending_write_chunks(address, chunks)
            if combined is not None:
                self._loop.call_soon_threadsafe(self._on_write, address, combined)
