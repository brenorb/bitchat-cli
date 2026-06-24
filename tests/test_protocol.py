import pytest

from bitchat_cli.constants import MessageType
from bitchat_cli.crypto import Identity, peer_id_from_noise_public_key
from bitchat_cli.protocol import (
    Announcement,
    FilePacket,
    NoisePayload,
    NoisePayloadType,
    Packet,
    PrivateMessagePacket,
    make_announce_packet,
    make_delivered_payload,
    make_file_transfer_packet,
    make_noise_encrypted_packet,
    make_public_message_packet,
    make_read_receipt_payload,
)


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


def test_signature_survives_rsr_mutation() -> None:
    identity = Identity.generate()
    packet = make_public_message_packet(identity, "sync me", timestamp_ms=1_700_000_000_000)
    decoded = Packet.decode(packet.encode(padding=False))

    rsr = Packet(
        packet_type=decoded.packet_type,
        sender_id=decoded.sender_id,
        payload=decoded.payload,
        recipient_id=decoded.recipient_id,
        timestamp_ms=decoded.timestamp_ms,
        signature=decoded.signature,
        ttl=decoded.ttl,
        version=decoded.version,
        is_rsr=True,
    )

    assert rsr.verify_signature(identity.signing_public_key)


def test_packet_round_trip_preserves_rsr_flag() -> None:
    identity = Identity.generate()
    packet = Packet(
        packet_type=MessageType.MESSAGE,
        sender_id=bytes.fromhex(identity.peer_id),
        payload=b"hello",
        timestamp_ms=1_700_000_000_000,
        is_rsr=True,
    ).sign(identity)

    decoded = Packet.decode(packet.encode(padding=False))

    assert decoded.is_rsr is True
    assert decoded.verify_signature(identity.signing_public_key)


def test_announce_packet_signature_and_peer_binding() -> None:
    identity = Identity.generate()
    packet = make_announce_packet(
        identity,
        "alice",
        direct_neighbors=[bytes.fromhex("0011223344556677")],
        timestamp_ms=1_700_000_000_000,
    )
    decoded = Packet.decode(packet.encode(padding=False))
    announcement = Announcement.decode(decoded.payload)

    assert decoded.verify_signature(announcement.signing_public_key)
    assert peer_id_from_noise_public_key(announcement.noise_public_key) == identity.peer_id
    assert announcement.direct_neighbors == [bytes.fromhex("0011223344556677")]


def test_private_message_packet_round_trip() -> None:
    packet = PrivateMessagePacket(message_id="12345678-1234-1234-1234-123456789abc", content="secret hello")

    encoded = packet.encode()
    decoded = PrivateMessagePacket.decode(encoded)

    assert decoded == packet


def test_private_message_packet_rejects_unknown_tlv_and_truncation() -> None:
    with pytest.raises(ValueError):
        PrivateMessagePacket.decode(bytes([0x09, 0x01, 0x61]))

    with pytest.raises(ValueError):
        PrivateMessagePacket.decode(bytes([0x00, 0x05, 0x61, 0x62]))


def test_file_packet_round_trip() -> None:
    packet = FilePacket(
        file_name="photo.jpg",
        file_size=5,
        mime_type="image/jpeg",
        content=b"12345",
    )

    decoded = FilePacket.decode(packet.encode())

    assert decoded == packet


def test_file_transfer_packet_uses_protocol_v2_and_verifies_signature() -> None:
    identity = Identity.generate()
    file_packet = FilePacket(
        file_name="note.txt",
        file_size=11,
        mime_type="text/plain",
        content=b"hello world",
    )

    packet = make_file_transfer_packet(identity, file_packet, timestamp_ms=1_700_000_000_000)
    decoded = Packet.decode(packet.encode(padding=False))

    assert decoded.version == 2
    assert decoded.packet_type == MessageType.FILE_TRANSFER
    assert FilePacket.decode(decoded.payload) == file_packet
    assert decoded.verify_signature(identity.signing_public_key)


def test_noise_encrypted_packet_uses_protocol_v2_for_large_payloads() -> None:
    identity = Identity.generate()

    packet = make_noise_encrypted_packet(
        identity,
        recipient_peer_id=Identity.generate().peer_id,
        payload=b"x" * 70000,
        timestamp_ms=1_700_000_000_000,
    )
    decoded = Packet.decode(packet.encode(padding=False))

    assert packet.version == 2
    assert decoded.version == 2
    assert decoded.payload == b"x" * 70000


def test_noise_payload_round_trip_and_receipt_helpers() -> None:
    private_payload = NoisePayload(
        payload_type=NoisePayloadType.PRIVATE_MESSAGE,
        payload=PrivateMessagePacket(message_id="m-1", content="hi").encode(),
    )

    decoded_private = NoisePayload.decode(private_payload.encode())
    decoded_delivered = NoisePayload.decode(make_delivered_payload("m-1"))
    decoded_read = NoisePayload.decode(make_read_receipt_payload("m-1"))

    assert decoded_private == private_payload
    assert decoded_delivered.payload_type == NoisePayloadType.DELIVERED
    assert decoded_delivered.payload == b"m-1"
    assert decoded_read.payload_type == NoisePayloadType.READ_RECEIPT
    assert decoded_read.payload == b"m-1"
