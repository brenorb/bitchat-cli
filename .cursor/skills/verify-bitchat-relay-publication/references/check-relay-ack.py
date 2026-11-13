"""Compare a negative relay acknowledgement on both reviewed refs using local doubles."""
import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bitchat_cli import geochat, nostr
from bitchat_cli.network import NetworkConfig


class Relay:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def send(self, raw):
        message = json.loads(raw)
        assert message[0] == "EVENT"
        self.event_id = message[1]["id"]

    async def recv(self):
        return json.dumps(["OK", self.event_id, False, "fixture rejection"])


async def check(legacy):
    with TemporaryDirectory() as directory:
        geo = geochat.GeoChatDaemon(
            relay_cache_path=Path(directory) / "relays.csv", seed_hex="55" * 32,
            nickname="fixture", on_message=lambda _event: None,
        )
        geo._relay_entries = [geochat.GeoRelayEntry("relay.invalid", 0.0, 0.0)]
        author = nostr.NostrIdentity.from_private_key_hex("14" * 32)
        recipient = nostr.NostrIdentity.from_private_key_hex("15" * 32)
        client = nostr.NostrDMClient(
            identity=author, relay_urls=["wss://relay.invalid"], on_message=lambda _event: None,
            network_config=NetworkConfig(proxy_mode="disabled", proxy_url=None, proxy_policy="prefer"),
        )
        event = nostr.create_private_message_event(
            content="fixture", recipient_pubkey_hex=recipient.public_key_hex, sender_identity=author,
        )
        with (
            patch.object(geochat, "ws_connect", lambda *_a, **_k: Relay()),
            patch.object(nostr, "ws_connect", lambda *_a, **_k: Relay()),
        ):
            for send in (lambda: geo.publish("u4pruy", "fixture"), lambda: client._publish_event(event)):
                try:
                    await send()
                except RuntimeError as error:
                    assert not legacy and "fixture rejection" in str(error), error
                else:
                    assert legacy, "A negative acknowledgement was reported as success"
    print("Both transports " + ("accepted send without acknowledgment." if legacy else "reported relay rejection."))


parser = argparse.ArgumentParser()
parser.add_argument("--legacy", action="store_true")
asyncio.run(check(parser.parse_args().legacy))
