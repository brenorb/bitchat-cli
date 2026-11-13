"""Compare an ended and interrupted location note read with local relay doubles."""
import asyncio
import json
from unittest.mock import patch

from bitchat_cli import location_notes as module
from bitchat_cli.network import NetworkConfig


class Relay:
    def __init__(self, ends):
        self.ends = ends
        self.receives = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def send(self, raw):
        command, self.subscription, _filter = json.loads(raw)
        assert command == "REQ"

    async def recv(self):
        self.receives += 1
        if self.receives == 1:
            return json.dumps(["EVENT", self.subscription, {
                "id": "fixture", "pubkey": "fixture", "kind": 1,
                "created_at": 100, "content": "note",
            }])
        if self.ends:
            return json.dumps(["EOSE", self.subscription])
        raise asyncio.TimeoutError


async def check():
    config = NetworkConfig(proxy_mode="disabled", proxy_url=None, proxy_policy="prefer")
    for ends in (True, False):
        with patch.object(module, "ws_connect", lambda *_args, **_kwargs: Relay(ends)):
            rows = await module._fetch_from_relay(
                "wss://relay.invalid", geohash="u4pruydq", limit=10, network_config=config,
            )
        assert [note.event_id for note in rows] == (["fixture"] if ends else [])
    print("EOSE returned the received note. A timeout discarded it.")


asyncio.run(check())
