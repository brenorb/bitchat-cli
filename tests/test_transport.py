from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from bitchat_cli.constants import MessageType
from bitchat_cli.crypto import Identity
from bitchat_cli.protocol import Packet
from bitchat_cli.transport import BLEChatTransport, PeerRecord


@dataclass
class CapturedPacket:
    peer_id: str
    packet: Packet


def _make_transport(
    *,
    identity: Identity,
    nickname: str,
    private_messages: list[tuple[str, str, datetime, str, str]],
    receipts: list[tuple[str, str, str, datetime]],
) -> BLEChatTransport:
    return BLEChatTransport(
        identity=identity,
        nickname=nickname,
        system_log=lambda _message: None,
        message_log=lambda *_args: None,
        private_message_log=lambda sender, content, timestamp, peer_id, message_id: private_messages.append(
            (sender, content, timestamp, peer_id, message_id)
        ),
        receipt_log=lambda peer_id, receipt_type, message_id, timestamp: receipts.append(
            (peer_id, receipt_type, message_id, timestamp)
        ),
    )


def _register_known_peer(
    transport: BLEChatTransport,
    *,
    peer_identity: Identity,
    nickname: str,
    address: str,
) -> None:
    transport.known_peers[peer_identity.peer_id] = PeerRecord(
        peer_id=peer_identity.peer_id,
        nickname=nickname,
        noise_public_key=peer_identity.noise_public_key,
        signing_public_key=peer_identity.signing_public_key,
        addresses={address},
    )


def test_private_message_without_session_queues_payload_and_starts_handshake() -> None:
    async def case() -> None:
        alice = Identity.generate()
        bob = Identity.generate()
        private_messages: list[tuple[str, str, datetime, str, str]] = []
        receipts: list[tuple[str, str, str, datetime]] = []
        transport = _make_transport(
            identity=alice,
            nickname="alice",
            private_messages=private_messages,
            receipts=receipts,
        )
        _register_known_peer(transport, peer_identity=bob, nickname="bob", address="B1")

        captured: list[CapturedPacket] = []

        async def fake_send(peer_id: str, packet: Packet) -> None:
            captured.append(CapturedPacket(peer_id=peer_id, packet=packet))

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        message_id = await transport.send_private_message(bob.peer_id, "hello bob", message_id="m-1")

        assert message_id == "m-1"
        assert len(captured) == 1
        assert captured[0].peer_id == bob.peer_id
        assert captured[0].packet.packet_type == MessageType.NOISE_HANDSHAKE
        assert list(transport._pending_noise_payloads[bob.peer_id]) != []
        assert private_messages == []
        assert receipts == []

    asyncio.run(case())


def test_private_message_handshake_flow_delivers_message_and_receipt() -> None:
    async def case() -> None:
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        alice_private_messages: list[tuple[str, str, datetime, str, str]] = []
        alice_receipts: list[tuple[str, str, str, datetime]] = []
        bob_private_messages: list[tuple[str, str, datetime, str, str]] = []
        bob_receipts: list[tuple[str, str, str, datetime]] = []
        alice = _make_transport(
            identity=alice_identity,
            nickname="alice",
            private_messages=alice_private_messages,
            receipts=alice_receipts,
        )
        bob = _make_transport(
            identity=bob_identity,
            nickname="bob",
            private_messages=bob_private_messages,
            receipts=bob_receipts,
        )
        _register_known_peer(alice, peer_identity=bob_identity, nickname="bob", address="B1")
        _register_known_peer(bob, peer_identity=alice_identity, nickname="alice", address="A1")

        alice_outbox: list[CapturedPacket] = []
        bob_outbox: list[CapturedPacket] = []

        async def alice_send(peer_id: str, packet: Packet) -> None:
            alice_outbox.append(CapturedPacket(peer_id=peer_id, packet=packet))

        async def bob_send(peer_id: str, packet: Packet) -> None:
            bob_outbox.append(CapturedPacket(peer_id=peer_id, packet=packet))

        alice._send_packet_to_peer = alice_send  # type: ignore[method-assign]
        bob._send_packet_to_peer = bob_send  # type: ignore[method-assign]

        await alice.send_private_message(bob_identity.peer_id, "secret hello", message_id="m-1")
        assert [item.packet.packet_type for item in alice_outbox] == [MessageType.NOISE_HANDSHAKE]

        handshake_1 = alice_outbox.pop(0).packet
        await bob._handle_decoded_packet("A1", handshake_1)
        assert [item.packet.packet_type for item in bob_outbox] == [MessageType.NOISE_HANDSHAKE]

        handshake_2 = bob_outbox.pop(0).packet
        await alice._handle_decoded_packet("B1", handshake_2)
        assert [item.packet.packet_type for item in alice_outbox] == [
            MessageType.NOISE_HANDSHAKE,
            MessageType.NOISE_ENCRYPTED,
        ]

        handshake_3 = alice_outbox.pop(0).packet
        encrypted_message = alice_outbox.pop(0).packet
        await bob._handle_decoded_packet("A1", handshake_3)
        await bob._handle_decoded_packet("A1", encrypted_message)

        assert len(bob_private_messages) == 1
        assert bob_private_messages[0][0] == "alice"
        assert bob_private_messages[0][1] == "secret hello"
        assert bob_private_messages[0][3] == alice_identity.peer_id
        assert bob_private_messages[0][4] == "m-1"
        assert [item.packet.packet_type for item in bob_outbox] == [MessageType.NOISE_ENCRYPTED]

        delivered_receipt = bob_outbox.pop(0).packet
        await alice._handle_decoded_packet("B1", delivered_receipt)

        assert alice_receipts == [(bob_identity.peer_id, "delivered", "m-1", alice_receipts[0][3])]
        assert alice._noise_sessions[bob_identity.peer_id].is_established() is True
        assert bob._noise_sessions[alice_identity.peer_id].is_established() is True

    asyncio.run(case())


def test_decrypt_failure_clears_session_and_restarts_handshake() -> None:
    async def case() -> None:
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        alice = _make_transport(
            identity=alice_identity,
            nickname="alice",
            private_messages=[],
            receipts=[],
        )
        bob_private_messages: list[tuple[str, str, datetime, str, str]] = []
        bob = _make_transport(
            identity=bob_identity,
            nickname="bob",
            private_messages=bob_private_messages,
            receipts=[],
        )
        _register_known_peer(alice, peer_identity=bob_identity, nickname="bob", address="B1")
        _register_known_peer(bob, peer_identity=alice_identity, nickname="alice", address="A1")

        alice_outbox: list[CapturedPacket] = []
        bob_outbox: list[CapturedPacket] = []

        async def alice_send(peer_id: str, packet: Packet) -> None:
            alice_outbox.append(CapturedPacket(peer_id=peer_id, packet=packet))

        async def bob_send(peer_id: str, packet: Packet) -> None:
            bob_outbox.append(CapturedPacket(peer_id=peer_id, packet=packet))

        alice._send_packet_to_peer = alice_send  # type: ignore[method-assign]
        bob._send_packet_to_peer = bob_send  # type: ignore[method-assign]

        await alice.send_private_message(bob_identity.peer_id, "first", message_id="m-1")
        await bob._handle_decoded_packet("A1", alice_outbox.pop(0).packet)
        await alice._handle_decoded_packet("B1", bob_outbox.pop(0).packet)
        await bob._handle_decoded_packet("A1", alice_outbox.pop(0).packet)
        alice_outbox.clear()
        bob_outbox.clear()

        await alice.send_private_message(bob_identity.peer_id, "second", message_id="m-2")
        encrypted_packet = alice_outbox.pop(0).packet
        tampered = Packet(
            packet_type=encrypted_packet.packet_type,
            sender_id=encrypted_packet.sender_id,
            recipient_id=encrypted_packet.recipient_id,
            timestamp_ms=encrypted_packet.timestamp_ms,
            payload=encrypted_packet.payload[:-1] + bytes([encrypted_packet.payload[-1] ^ 0x01]),
            signature=encrypted_packet.signature,
            ttl=encrypted_packet.ttl,
            version=encrypted_packet.version,
        )

        await bob._handle_decoded_packet("A1", tampered)

        assert bob_private_messages == []
        assert len(bob_outbox) == 1
        assert bob_outbox[0].packet.packet_type == MessageType.NOISE_HANDSHAKE

    asyncio.run(case())


def test_fresh_inbound_handshake_replaces_established_session() -> None:
    async def case() -> None:
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        alice = _make_transport(
            identity=alice_identity,
            nickname="alice",
            private_messages=[],
            receipts=[],
        )
        bob = _make_transport(
            identity=bob_identity,
            nickname="bob",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(alice, peer_identity=bob_identity, nickname="bob", address="B1")
        _register_known_peer(bob, peer_identity=alice_identity, nickname="alice", address="A1")

        alice_outbox: list[CapturedPacket] = []
        bob_outbox: list[CapturedPacket] = []

        async def alice_send(peer_id: str, packet: Packet) -> None:
            alice_outbox.append(CapturedPacket(peer_id=peer_id, packet=packet))

        async def bob_send(peer_id: str, packet: Packet) -> None:
            bob_outbox.append(CapturedPacket(peer_id=peer_id, packet=packet))

        alice._send_packet_to_peer = alice_send  # type: ignore[method-assign]
        bob._send_packet_to_peer = bob_send  # type: ignore[method-assign]

        await alice.send_private_message(bob_identity.peer_id, "first", message_id="m-1")
        await bob._handle_decoded_packet("A1", alice_outbox.pop(0).packet)
        await alice._handle_decoded_packet("B1", bob_outbox.pop(0).packet)
        await bob._handle_decoded_packet("A1", alice_outbox.pop(0).packet)

        assert alice._noise_sessions[bob_identity.peer_id].is_established() is True
        assert bob._noise_sessions[alice_identity.peer_id].is_established() is True

        alice._noise_sessions.pop(bob_identity.peer_id)
        alice_outbox.clear()
        bob_outbox.clear()

        await alice.send_private_message(bob_identity.peer_id, "second", message_id="m-2")
        inbound_fresh_handshake = alice_outbox.pop(0).packet
        await bob._handle_decoded_packet("A1", inbound_fresh_handshake)

        assert len(bob_outbox) == 1
        assert bob_outbox[0].packet.packet_type == MessageType.NOISE_HANDSHAKE

    asyncio.run(case())
