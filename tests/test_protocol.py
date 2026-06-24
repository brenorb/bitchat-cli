from bitchat_cli.crypto import Identity, peer_id_from_noise_public_key
from bitchat_cli.protocol import Announcement, Packet, make_announce_packet, make_public_message_packet


def test_announcement_round_trip() -> None:
    identity = Identity.generate()
    announcement = Announcement(
        nickname="alice",
        noise_public_key=identity.noise_public_key,
        signing_public_key=identity.signing_public_key,
        direct_neighbors=[bytes.fromhex("0011223344556677"), bytes.fromhex("8899aabbccddeeff")],
    )

    decoded = Announcement.decode(announcement.encode())

    assert decoded.nickname == "alice"
    assert decoded.noise_public_key == identity.noise_public_key
    assert decoded.signing_public_key == identity.signing_public_key
    assert decoded.direct_neighbors == announcement.direct_neighbors


def test_public_message_packet_round_trip() -> None:
    identity = Identity.generate()
    packet = make_public_message_packet(identity, "hello mesh", timestamp_ms=1_700_000_000_000)

    decoded = Packet.decode(packet.encode(padding=False))

    assert decoded.packet_type == packet.packet_type
    assert decoded.sender_id == bytes.fromhex(identity.peer_id)
    assert decoded.payload == b"hello mesh"
    assert decoded.verify_signature(identity.signing_public_key)


def test_signature_survives_ttl_relay_mutation() -> None:
    identity = Identity.generate()
    packet = make_public_message_packet(identity, "relay me", timestamp_ms=1_700_000_000_000)
    decoded = Packet.decode(packet.encode(padding=False))

    relayed = Packet(
        packet_type=decoded.packet_type,
        sender_id=decoded.sender_id,
        payload=decoded.payload,
        recipient_id=decoded.recipient_id,
        timestamp_ms=decoded.timestamp_ms,
        signature=decoded.signature,
        ttl=1,
        version=decoded.version,
    )

    assert relayed.verify_signature(identity.signing_public_key)


def test_announce_packet_signature_and_peer_binding() -> None:
    identity = Identity.generate()
    packet = make_announce_packet(identity, "alice", timestamp_ms=1_700_000_000_000)
    decoded = Packet.decode(packet.encode(padding=False))
    announcement = Announcement.decode(decoded.payload)

    assert decoded.verify_signature(announcement.signing_public_key)
    assert peer_id_from_noise_public_key(announcement.noise_public_key) == identity.peer_id
