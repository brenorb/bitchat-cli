"""Replay delayed fragments on the local assembly buffer."""
from random import Random
from unittest.mock import patch

from bitchat_cli import fragments
from bitchat_cli.constants import MessageType
from bitchat_cli.protocol import Packet


packet = Packet(
    packet_type=MessageType.MESSAGE,
    sender_id=b"\x01" * 8,
    payload=Random(0).randbytes(1024),
    timestamp_ms=1700000000000,
)
parts = fragments.fragment_packet_sequence(packet, fragment_id=b"fixture1")
assert len(parts) > 1
buffer = fragments.FragmentAssemblyBuffer(expiry_seconds=30)
with patch.object(fragments.time, "monotonic", return_value=10.0):
    assert buffer.append(parts[0]) is None
with patch.object(fragments.time, "monotonic", return_value=41.0):
    for part in parts[1:]:
        assert buffer.append(part) is None, "an expired part completed the packet"
    reassembled = buffer.append(parts[0])
    assert reassembled is not None and reassembled.payload == packet.payload
print("Expired parts were discarded and a fresh part completed the packet.")
