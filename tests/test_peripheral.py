from __future__ import annotations

import bitchat_cli.peripheral as peripheral_module


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
