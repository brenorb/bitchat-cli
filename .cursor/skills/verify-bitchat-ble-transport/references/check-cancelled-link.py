"""Cancel transport while a local BLE double is subscribing to notifications."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from bitchat_cli.crypto import Identity
from bitchat_cli.transport import BLEChatTransport


class FakeScanner:
    def __init__(self, **_kwargs):
        self.running = False

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False


async def check():
    subscribing = asyncio.Event()
    linked_clients = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            self.is_connected = False
            self.services = SimpleNamespace(get_characteristic=lambda _uuid: object())
            linked_clients.append(self)

        async def connect(self):
            self.is_connected = True

        async def start_notify(self, *_args):
            subscribing.set()
            await asyncio.Future()

        async def disconnect(self):
            self.is_connected = False

    transport = BLEChatTransport(
        identity=Identity.generate(),
        nickname="test",
        system_log=lambda *_args: None,
        message_log=lambda *_args: None,
    )
    with (
        patch("bitchat_cli.transport.BleakScanner", FakeScanner),
        patch("bitchat_cli.transport.BleakClient", FakeClient),
    ):
        await transport.start()
        try:
            transport._handle_discovered_device(SimpleNamespace(address="local-test"), None)
            await asyncio.wait_for(subscribing.wait(), timeout=1)
            assert not transport.clients, transport.clients
        finally:
            await transport.stop()

    assert not transport.connecting, transport.connecting
    assert len(linked_clients) == 1
    assert linked_clients[0].is_connected, "The transport disconnected the in-flight link"
    assert not transport._scanner.running
    print("The transport stopped with the connected client absent from its disconnect list.")


asyncio.run(check())
