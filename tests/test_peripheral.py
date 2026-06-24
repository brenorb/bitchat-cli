from __future__ import annotations

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

    monkeypatch.setattr(peripheral_module.libdispatch, "dispatch_queue_create", fake_dispatch_queue_create)

    queue = peripheral_module._create_dispatch_queue()

    assert queue is sentinel
    assert seen == {"label": b"bitchat.peripheral", "attr": None}


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
