from bitchat_cli.constants import MAX_PACKET_SIZE, MessageType
from bitchat_cli.crypto import Identity
from bitchat_cli.protocol import Packet, make_public_message_packet


def test_large_packet_fragments_and_reassembles_out_of_order() -> None:
    from bitchat_cli.fragments import FragmentAssemblyBuffer, fragment_packet_sequence

    identity = Identity.generate()
    payload = bytes(range(256)) * 8
    packet = Packet(
        packet_type=MessageType.MESSAGE,
        sender_id=bytes.fromhex(identity.peer_id),
        payload=payload,
        timestamp_ms=1_700_000_000_000,
    ).sign(identity)

    fragments = fragment_packet_sequence(packet)

    assert len(fragments) > 1
    assert all(fragment.packet_type == MessageType.FRAGMENT for fragment in fragments)
    assert all(len(fragment.encode(padding=False)) <= MAX_PACKET_SIZE for fragment in fragments)

    assembler = FragmentAssemblyBuffer()
    reassembled = None
    for fragment in reversed(fragments):
        reassembled = assembler.append(fragment) or reassembled

    assert reassembled is not None
    assert reassembled.payload == payload
    assert reassembled.verify_signature(identity.signing_public_key)


def test_small_packet_is_not_fragmented() -> None:
    from bitchat_cli.fragments import fragment_packet_sequence

    identity = Identity.generate()
    packet = make_public_message_packet(identity, "hello", timestamp_ms=1_700_000_000_000)

    fragments = fragment_packet_sequence(packet)

    assert fragments == [packet]


def test_fragment_assembler_ignores_non_fragment_packets() -> None:
    from bitchat_cli.fragments import FragmentAssemblyBuffer

    identity = Identity.generate()
    packet = make_public_message_packet(identity, "hello", timestamp_ms=1_700_000_000_000)

    assembler = FragmentAssemblyBuffer()

    assert assembler.append(packet) is None
