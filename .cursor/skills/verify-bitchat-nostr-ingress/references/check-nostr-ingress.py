"""Exercise Nostr callback dispatch with locally generated messages."""
import json

from bitchat_cli import nostr
from bitchat_cli.network import NetworkConfig

sender = nostr.NostrIdentity.from_private_key_hex("14" * 32)
recipient = nostr.NostrIdentity.from_private_key_hex("15" * 32)
other = nostr.NostrIdentity.from_private_key_hex("16" * 32)
received = []
client = nostr.NostrDMClient(
    identity=recipient, relay_urls=[], on_message=received.append,
    network_config=NetworkConfig(proxy_mode="disabled", proxy_url=None, proxy_policy="prefer"),
)
content = nostr.encode_bitchat_private_message(
    sender_peer_id="0123456789abcdef", recipient_peer_id="abcdef0123456789",
    message_id="fixture", content="fixture message",
)
event = nostr.create_private_message_event(
    content=content, recipient_pubkey_hex=recipient.public_key_hex, sender_identity=sender,
)
frame = json.dumps(["EVENT", "fixture", event.to_dict()])
client._handle_relay_message(frame, recipient_identity=other)
assert not received
for _ in range(2):
    client._handle_relay_message(frame, recipient_identity=recipient)
assert len(received) == 1, received
assert received[0].content == "fixture message" and received[0].message_id == "fixture"
print("A local envelope decoded only for its recipient and was dispatched once.")
