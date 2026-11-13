"""Exercise local watch task reconciliation with sleeping tasks, without a relay."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bitchat_cli import geochat, nostr
from bitchat_cli.network import NetworkConfig


async def sleep_watch(*_args):
    await asyncio.Future()


async def check():
    with TemporaryDirectory() as directory:
        geo = geochat.GeoChatDaemon(
            relay_cache_path=Path(directory) / "relays.csv", seed_hex="55" * 32,
            nickname="fixture", on_message=lambda _event: None,
        )
        geo._relay_entries = [geochat.GeoRelayEntry("relay.invalid", 0.0, 0.0)]
        author = nostr.NostrIdentity.from_private_key_hex("14" * 32)
        extra = nostr.NostrIdentity.from_private_key_hex("15" * 32)
        client = nostr.NostrDMClient(
            identity=author, relay_urls=["wss://relay.invalid"], on_message=lambda _event: None,
            network_config=NetworkConfig(proxy_mode="disabled", proxy_url=None, proxy_policy="prefer"),
        )
        with (
            patch.object(geo, "_watch_loop", sleep_watch),
            patch.object(client, "_watch_loop", sleep_watch),
        ):
            try:
                await geo.set_watches(["u4pruy", "#U4PRUY"])
                await client.set_extra_watch_identities([extra, extra])
                assert len(geo._watch_tasks) == 1
                assert len(client._watch_tasks) == 2
                obsolete = [*geo._watch_tasks.values(), client._watch_tasks[("wss://relay.invalid", extra.public_key_hex)]]
                await geo.set_watches([])
                await client.set_extra_watch_identities([])
                assert not geo._watch_tasks
                assert len(client._watch_tasks) == 1
                assert all(task.cancelled() for task in obsolete)
            finally:
                await geo.stop()
                await client.stop()
        assert not geo._watch_tasks and not client._watch_tasks
    print("Both watch sets canceled obsolete tasks and cleared all tasks on stop.")


asyncio.run(check())
