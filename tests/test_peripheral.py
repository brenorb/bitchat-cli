from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import bitchat_cli.peripheral as peripheral_module
from bitchat_cli.crypto import Identity
from bitchat_cli.protocol import make_public_message_packet


def test_create_dispatch_queue_uses_bitchat_label(monkeypatch) -> None:
    seen: dict[str, object] = {}
    sentinel = object()

    def fake_dispatch_queue_create(label, attr):
        seen["label"] = label
        seen["attr"] = attr
        return sentinel

    monkeypatch.setitem(
        sys.modules,
        "libdispatch",
        SimpleNamespace(dispatch_queue_create=fake_dispatch_queue_create),
    )

    queue = peripheral_module._create_dispatch_queue()

    assert queue is sentinel
    assert seen == {"label": b"bitchat.peripheral", "attr": None}


def test_peripheral_import_and_fallback_without_macos_modules() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
sys.platform = "linux"
sys.modules["libdispatch"] = None
from bitchat_cli.peripheral import (
    NullPeripheralEndpoint, create_peripheral_endpoint, peripheral_supported,
)
assert not peripheral_supported()
endpoint = create_peripheral_endpoint(
    service_uuid="service", characteristic_uuid="characteristic",
    system_log=lambda *_: None, on_write=lambda *_: None,
    on_subscribe=lambda *_: None, on_unsubscribe=lambda *_: None,
)
assert isinstance(endpoint, NullPeripheralEndpoint)
""",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_peripheral_support_requires_dispatch_queue_module(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    for name in ("CoreBluetooth", "Foundation", "objc"):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace())
    monkeypatch.setitem(sys.modules, "libdispatch", None)

    assert peripheral_module.peripheral_supported() is False


def test_create_peripheral_manager_passes_dispatch_queue() -> None:
    captured: dict[str, object] = {}

    class FakePeripheralManagerClass:
        def alloc(self):
            return self

        def initWithDelegate_queue_options_(self, delegate, queue, options):
            captured["delegate"] = delegate
            captured["queue"] = queue
            captured["options"] = options
            return "manager"

    class FakeCoreBluetooth:
        CBPeripheralManager = FakePeripheralManagerClass()

    delegate = object()
    queue = object()

    manager = peripheral_module._create_peripheral_manager(FakeCoreBluetooth, delegate, queue)

    assert manager == "manager"
    assert captured == {
        "delegate": delegate,
        "queue": queue,
        "options": None,
    }


def test_characteristic_spec_matches_official_app_surface() -> None:
    class FakeCoreBluetooth:
        CBCharacteristicPropertyNotify = 0x01
        CBCharacteristicPropertyRead = 0x02
        CBCharacteristicPropertyWrite = 0x04
        CBCharacteristicPropertyWriteWithoutResponse = 0x08
        CBAttributePermissionsReadable = 0x10
        CBAttributePermissionsWriteable = 0x20

    properties, permissions = peripheral_module._characteristic_spec(FakeCoreBluetooth)

    assert properties == (
        FakeCoreBluetooth.CBCharacteristicPropertyNotify
        | FakeCoreBluetooth.CBCharacteristicPropertyRead
        | FakeCoreBluetooth.CBCharacteristicPropertyWrite
        | FakeCoreBluetooth.CBCharacteristicPropertyWriteWithoutResponse
    )
    assert permissions == (
        FakeCoreBluetooth.CBAttributePermissionsReadable
        | FakeCoreBluetooth.CBAttributePermissionsWriteable
    )


def test_null_peripheral_endpoint_has_no_subscriber_addresses() -> None:
    endpoint = peripheral_module.NullPeripheralEndpoint()

    assert endpoint.subscriber_addresses() == ()
    assert endpoint.recent_events() == ()


def test_macos_peripheral_endpoint_reports_sorted_subscriber_addresses() -> None:
    endpoint = peripheral_module.MacOSPeripheralEndpoint.__new__(peripheral_module.MacOSPeripheralEndpoint)
    endpoint._subscribed_centrals = {
        "peripheral:C1": object(),
        "peripheral:A2": object(),
        "peripheral:B7": object(),
    }

    assert endpoint.subscriber_addresses() == (
        "peripheral:A2",
        "peripheral:B7",
        "peripheral:C1",
    )


def test_macos_peripheral_endpoint_tracks_bounded_recent_events() -> None:
    endpoint = peripheral_module.MacOSPeripheralEndpoint.__new__(peripheral_module.MacOSPeripheralEndpoint)
    endpoint._recent_events = peripheral_module.deque(maxlen=3)

    endpoint._record_recent_event("subscribe", "peripheral:A1")
    endpoint._record_recent_event("write", "peripheral:A1", bytes=42)
    endpoint._record_recent_event("unsubscribe", "peripheral:A1")
    endpoint._record_recent_event("subscribe", "peripheral:B2")

    assert endpoint.recent_events() == (
        {
            "event": "write",
            "address": "peripheral:A1",
            "bytes": 42,
        },
        {
            "event": "unsubscribe",
            "address": "peripheral:A1",
        },
        {
            "event": "subscribe",
            "address": "peripheral:B2",
        },
    )


def test_pending_write_buffer_reassembles_offset_chunks_until_packet_decodes() -> None:
    identity = Identity.generate()
    packet = make_public_message_packet(identity, "hello from split write")
    encoded = packet.encode(padding=False)
    first = encoded[:15]
    second = encoded[15:]
    endpoint = peripheral_module.MacOSPeripheralEndpoint.__new__(peripheral_module.MacOSPeripheralEndpoint)
    endpoint._pending_write_buffers = {}
    endpoint._recent_events = peripheral_module.deque(maxlen=8)

    assert endpoint._append_pending_write_chunks("peripheral:A1", [(0, first)]) is None
    assert endpoint._pending_write_buffers["peripheral:A1"] == first
    assert endpoint.recent_events()[-1] == {
        "event": "write_buffered",
        "address": "peripheral:A1",
        "bytes": 15,
        "total_bytes": 15,
    }

    reassembled = endpoint._append_pending_write_chunks("peripheral:A1", [(15, second)])

    assert reassembled == encoded
    assert "peripheral:A1" not in endpoint._pending_write_buffers
    assert endpoint.recent_events()[-1] == {
        "event": "write_decoded",
        "address": "peripheral:A1",
        "bytes": len(second),
        "total_bytes": len(encoded),
    }


def test_pending_write_buffer_drops_oversized_accumulation() -> None:
    endpoint = peripheral_module.MacOSPeripheralEndpoint.__new__(peripheral_module.MacOSPeripheralEndpoint)
    endpoint._pending_write_buffers = {}
    endpoint._system_log = lambda _message: None
    endpoint._recent_events = peripheral_module.deque(maxlen=8)

    reassembled = endpoint._append_pending_write_chunks(
        "peripheral:A1",
        [(0, b"\x01" * (peripheral_module._PENDING_WRITE_CAP_BYTES + 1))],
    )

    assert reassembled is None
    assert "peripheral:A1" not in endpoint._pending_write_buffers
    assert endpoint.recent_events()[-1] == {
        "event": "write_oversized",
        "address": "peripheral:A1",
        "bytes": peripheral_module._PENDING_WRITE_CAP_BYTES + 1,
        "total_bytes": peripheral_module._PENDING_WRITE_CAP_BYTES + 1,
    }


def test_handle_write_requests_decodes_grouped_offset_requests_once() -> None:
    identity = Identity.generate()
    packet = make_public_message_packet(identity, "delegate path split write")
    encoded = packet.encode(padding=False)
    first = encoded[:17]
    second = encoded[17:]
    writes: list[tuple[str, bytes]] = []
    responses: list[tuple[object, int]] = []

    class FakeUUID:
        def UUIDString(self):
            return "ABCD"

    class FakeCharacteristic:
        def UUID(self):
            return FakeUUID()

    class FakeCentralIdentifier:
        def UUIDString(self):
            return "CENTRAL-1"

    class FakeCentral:
        def identifier(self):
            return FakeCentralIdentifier()

    class FakeRequest:
        def __init__(self, offset: int, value: bytes) -> None:
            self._central = FakeCentral()
            self._characteristic = FakeCharacteristic()
            self._offset = offset
            self._value = value

        def central(self):
            return self._central

        def characteristic(self):
            return self._characteristic

        def offset(self):
            return self._offset

        def value(self):
            return self._value

    class FakeManager:
        def respondToRequest_withResult_(self, request, result) -> None:
            responses.append((request, result))

    class FakeLoop:
        def call_soon_threadsafe(self, callback, *args) -> None:
            callback(*args)

    class FakeCoreBluetooth:
        CBATTErrorSuccess = 0
        CBATTErrorInvalidOffset = 7
        CBATTErrorAttributeNotFound = 10

    endpoint = peripheral_module.MacOSPeripheralEndpoint.__new__(peripheral_module.MacOSPeripheralEndpoint)
    endpoint._CoreBluetooth = FakeCoreBluetooth
    endpoint._characteristic = object()
    endpoint._characteristic_uuid = "ABCD"
    endpoint._pending_write_buffers = {}
    endpoint._recent_events = peripheral_module.deque(maxlen=8)
    endpoint._subscribed_centrals = {}
    endpoint._loop = FakeLoop()
    endpoint._on_write = lambda address, data: writes.append((address, data))
    endpoint._system_log = lambda _message: None

    endpoint._handle_write_requests(
        FakeManager(),
        [FakeRequest(0, first), FakeRequest(17, second)],
    )

    assert responses == [
        (responses[0][0], FakeCoreBluetooth.CBATTErrorSuccess),
        (responses[1][0], FakeCoreBluetooth.CBATTErrorSuccess),
    ]
    assert writes == [("peripheral:CENTRAL-1", encoded)]
    assert endpoint._pending_write_buffers == {}
    assert endpoint.recent_events()[-1] == {
        "event": "write_decoded",
        "address": "peripheral:CENTRAL-1",
        "bytes": len(encoded),
        "total_bytes": len(encoded),
    }


def test_handle_unsubscribe_clears_pending_write_buffer() -> None:
    class FakeCentralIdentifier:
        def UUIDString(self):
            return "CENTRAL-2"

    class FakeCentral:
        def identifier(self):
            return FakeCentralIdentifier()

    class FakeLoop:
        def call_soon_threadsafe(self, callback, *args) -> None:
            callback(*args)

    seen: list[str] = []
    endpoint = peripheral_module.MacOSPeripheralEndpoint.__new__(peripheral_module.MacOSPeripheralEndpoint)
    endpoint._subscribed_centrals = {"peripheral:CENTRAL-2": object()}
    endpoint._pending_write_buffers = {"peripheral:CENTRAL-2": b"partial"}
    endpoint._recent_events = peripheral_module.deque(maxlen=8)
    endpoint._loop = FakeLoop()
    endpoint._on_unsubscribe = lambda address: seen.append(address)

    endpoint._handle_unsubscribe(FakeCentral())

    assert "peripheral:CENTRAL-2" not in endpoint._subscribed_centrals
    assert "peripheral:CENTRAL-2" not in endpoint._pending_write_buffers
    assert seen == ["peripheral:CENTRAL-2"]
