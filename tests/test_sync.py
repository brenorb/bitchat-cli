from bitchat_cli.constants import MessageType
from bitchat_cli.crypto import Identity
from bitchat_cli.protocol import Packet, make_public_message_packet
from bitchat_cli.sync import GCSFilter, RequestSyncPacket, SyncMessageType, SyncTypeFlags, packet_sync_id


def test_request_sync_packet_round_trip() -> None:
    packet = RequestSyncPacket(
        p=8,
        m=4096,
        data=b"\x01\x02",
        types=SyncTypeFlags.PUBLIC_MESSAGES,
        since_timestamp=1_700_000_000_000,
        fragment_id_filter="frag-1",
    )

    decoded = RequestSyncPacket.decode(packet.encode())

    assert decoded == packet


def test_gcs_filter_build_and_membership() -> None:
    identity = Identity.generate()
    packet = make_public_message_packet(identity, "hello mesh", timestamp_ms=1_700_000_000_000)
    packet_id = packet_sync_id(packet)

    p, m, data = GCSFilter.build_filter(ids=[packet_id], max_bytes=128, target_fpr=0.01)
    sorted_values = GCSFilter.decode_to_sorted_set(p=p, m=m, data=data)

    assert GCSFilter.contains(
        sorted_values=sorted_values,
        candidate=GCSFilter.bucket(packet_id, modulus=m),
    )


def test_sync_type_flags_round_trip() -> None:
    flags = SyncTypeFlags.from_message_types([MessageType.ANNOUNCE, MessageType.MESSAGE, MessageType.FRAGMENT])

    decoded = SyncTypeFlags.decode(flags.to_bytes() or b"")

    assert decoded == flags
    assert decoded is not None
    assert decoded.contains(SyncMessageType.ANNOUNCE)
    assert decoded.contains(SyncMessageType.MESSAGE)
    assert decoded.contains(SyncMessageType.FRAGMENT)


def test_packet_sync_id_excludes_ttl() -> None:
    identity = Identity.generate()
    packet = make_public_message_packet(identity, "relay me", timestamp_ms=1_700_000_000_000)
    relayed = Packet(
        packet_type=packet.packet_type,
        sender_id=packet.sender_id,
        recipient_id=packet.recipient_id,
        timestamp_ms=packet.timestamp_ms,
        payload=packet.payload,
        signature=packet.signature,
        ttl=1,
        version=packet.version,
    )

    assert packet_sync_id(packet) == packet_sync_id(relayed)
