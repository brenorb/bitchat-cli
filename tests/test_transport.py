from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bitchat_cli.constants import MessageType
from bitchat_cli.crypto import Identity
from bitchat_cli.protocol import Announcement, Packet, make_noise_handshake_packet, make_public_message_packet
from bitchat_cli.transport import BLEChatTransport, PeerRecord
from bitchat_cli.fragments import fragment_packet_sequence


@dataclass
class CapturedPacket:
    peer_id: str
    packet: Packet


@dataclass
class FakeDevice:
    address: str


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


def test_public_message_relays_once_and_dedupes_looped_copy() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        charlie_identity = Identity.generate()
        public_messages: list[tuple[str, str, datetime, str | None, str | None]] = []
        transport = BLEChatTransport(
            identity=local_identity,
            nickname="local",
            system_log=lambda _message: None,
            message_log=lambda sender, content, timestamp, peer_id, message_id: public_messages.append(
                (sender, content, timestamp, peer_id, message_id)
            ),
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=charlie_identity, nickname="charlie", address="C1")
        transport.clients = {"A1": "A1", "C1": "C1"}  # type: ignore[assignment]

        relayed_packets: list[tuple[str, Packet]] = []

        async def fake_write(client, packet: Packet) -> None:
            relayed_packets.append((client, packet))

        transport._write_packet = fake_write  # type: ignore[method-assign]

        inbound = make_public_message_packet(alice_identity, "hello mesh")
        await transport._handle_decoded_packet("A1", inbound)

        assert len(public_messages) == 1
        assert public_messages[0][0] == "alice"
        assert public_messages[0][1] == "hello mesh"
        assert len(relayed_packets) == 1
        assert relayed_packets[0][0] == "C1"
        assert relayed_packets[0][1].ttl == inbound.ttl - 1

        looped_copy = Packet(
            packet_type=inbound.packet_type,
            sender_id=inbound.sender_id,
            recipient_id=inbound.recipient_id,
            timestamp_ms=inbound.timestamp_ms,
            payload=inbound.payload,
            signature=inbound.signature,
            ttl=inbound.ttl - 1,
            version=inbound.version,
        )
        await transport._handle_decoded_packet("C1", looped_copy)

        assert len(public_messages) == 1
        assert len(relayed_packets) == 1

    asyncio.run(case())


def test_directed_noise_handshake_relays_without_creating_local_session() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        charlie_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=charlie_identity, nickname="charlie", address="C1")
        transport.clients = {"A1": "A1", "C1": "C1"}  # type: ignore[assignment]

        relayed_packets: list[tuple[str, Packet]] = []

        async def fake_write(client, packet: Packet) -> None:
            relayed_packets.append((client, packet))

        transport._write_packet = fake_write  # type: ignore[method-assign]

        inbound = make_noise_handshake_packet(alice_identity, charlie_identity.peer_id, b"hs1", timestamp_ms=1234)
        await transport._handle_decoded_packet("A1", inbound)

        assert transport._noise_sessions == {}
        assert len(relayed_packets) == 1
        assert relayed_packets[0][0] == "C1"
        assert relayed_packets[0][1].packet_type == MessageType.NOISE_HANDSHAKE
        assert relayed_packets[0][1].ttl == inbound.ttl - 1
        assert relayed_packets[0][1].recipient_id == bytes.fromhex(charlie_identity.peer_id)

    asyncio.run(case())


def test_duplicate_handshake_packet_is_suppressed_after_session_establishes() -> None:
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

        await alice.send_private_message(bob_identity.peer_id, "hello", message_id="m-1")
        handshake_1 = alice_outbox.pop(0).packet
        await bob._handle_decoded_packet("A1", handshake_1)
        handshake_2 = bob_outbox.pop(0).packet
        await alice._handle_decoded_packet("B1", handshake_2)
        handshake_3 = alice_outbox.pop(0).packet
        await bob._handle_decoded_packet("A1", handshake_3)

        session = bob._noise_sessions[alice_identity.peer_id]
        assert session.is_established() is True

        await bob._handle_decoded_packet("A1", handshake_3)

        assert bob._noise_sessions[alice_identity.peer_id] is session
        assert bob_outbox == []

    asyncio.run(case())


def test_direct_send_prefers_one_active_address_per_peer() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        peer_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=peer_identity, nickname="peer", address="B1")
        transport.known_peers[peer_identity.peer_id].addresses.add("B2")
        transport.known_peers[peer_identity.peer_id].active_address = "B2"
        transport.clients = {"B1": "B1", "B2": "B2"}  # type: ignore[assignment]

        writes: list[tuple[str, Packet]] = []

        async def fake_write(client, packet: Packet) -> None:
            writes.append((client, packet))

        transport._write_packet = fake_write  # type: ignore[method-assign]

        sent = await transport._send_packet_to_peer(
            peer_identity.peer_id,
            make_noise_handshake_packet(local_identity, peer_identity.peer_id, b"hs1"),
        )

        assert sent is True
        assert writes == [("B2", writes[0][1])]

    asyncio.run(case())


def test_notification_worker_drains_enqueued_packets() -> None:
    async def case() -> None:
        transport = _make_transport(
            identity=Identity.generate(),
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        seen: list[tuple[str, bytes]] = []

        async def fake_handle(address: str, data: bytes) -> None:
            seen.append((address, data))

        transport._handle_notification = fake_handle  # type: ignore[method-assign]
        transport._notification_queue = asyncio.Queue(maxsize=2)
        worker = asyncio.create_task(transport._notification_worker())

        transport._enqueue_notification("A1", b"one")
        transport._enqueue_notification("A1", b"two")
        await asyncio.wait_for(transport._notification_queue.join(), timeout=1.0)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

        assert seen == [("A1", b"one"), ("A1", b"two")]

    asyncio.run(case())


def test_notification_enqueue_drops_when_queue_is_full() -> None:
    async def case() -> None:
        logs: list[str] = []
        transport = BLEChatTransport(
            identity=Identity.generate(),
            nickname="local",
            system_log=logs.append,
            message_log=lambda *_args: None,
        )
        transport._notification_queue = asyncio.Queue(maxsize=1)
        transport._enqueue_notification("A1", b"one")
        transport._enqueue_notification("A1", b"two")

        queued = await transport._notification_queue.get()
        transport._notification_queue.task_done()

        assert queued == ("A1", b"one")
        assert "Dropped inbound packet" in logs[0]

    asyncio.run(case())


def test_fragment_relays_before_full_reassembly() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        charlie_identity = Identity.generate()
        public_messages: list[tuple[str, str, datetime, str | None, str | None]] = []
        transport = BLEChatTransport(
            identity=local_identity,
            nickname="local",
            system_log=lambda _message: None,
            message_log=lambda sender, content, timestamp, peer_id, message_id: public_messages.append(
                (sender, content, timestamp, peer_id, message_id)
            ),
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=charlie_identity, nickname="charlie", address="C1")
        transport.clients = {"A1": "A1", "C1": "C1"}  # type: ignore[assignment]

        relayed_packets: list[tuple[str, Packet]] = []

        async def fake_write(client, packet: Packet) -> None:
            relayed_packets.append((client, packet))

        transport._write_packet = fake_write  # type: ignore[method-assign]

        oversized = Packet(
            packet_type=MessageType.MESSAGE,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            payload=bytes(range(256)) * 4,
        ).sign(alice_identity)
        fragments = fragment_packet_sequence(oversized)
        assert len(fragments) > 1

        await transport._handle_decoded_packet("A1", fragments[0])

        assert public_messages == []
        assert len(relayed_packets) == 1
        assert relayed_packets[0][0] == "C1"
        assert relayed_packets[0][1].packet_type == MessageType.FRAGMENT
        assert relayed_packets[0][1].ttl == fragments[0].ttl - 1

        for fragment in fragments[1:]:
            await transport._handle_decoded_packet("A1", fragment)

        assert len(public_messages) == 1
        assert public_messages[0][0] == "alice"
        assert len(relayed_packets) == len(fragments)

    asyncio.run(case())


def test_disconnect_clears_peer_active_address_and_disconnected_link() -> None:
    transport = _make_transport(
        identity=Identity.generate(),
        nickname="local",
        private_messages=[],
        receipts=[],
    )
    peer_identity = Identity.generate()
    _register_known_peer(transport, peer_identity=peer_identity, nickname="peer", address="B1")
    peer = transport.known_peers[peer_identity.peer_id]
    peer.addresses.add("B2")
    peer.active_address = "B2"
    transport.clients = {"B2": "B2"}  # type: ignore[assignment]

    transport._on_disconnect("B2")

    assert transport.clients == {}
    assert peer.addresses == {"B1"}
    assert peer.active_address is None


def test_prune_runtime_state_removes_stale_peer_and_backoff_entries() -> None:
    transport = _make_transport(
        identity=Identity.generate(),
        nickname="local",
        private_messages=[],
        receipts=[],
    )
    stale_identity = Identity.generate()
    fresh_identity = Identity.generate()
    stale_seen = datetime.now(timezone.utc) - timedelta(hours=2)
    fresh_seen = datetime.now(timezone.utc)
    transport.known_peers[stale_identity.peer_id] = PeerRecord(
        peer_id=stale_identity.peer_id,
        nickname="stale",
        noise_public_key=stale_identity.noise_public_key,
        signing_public_key=stale_identity.signing_public_key,
        addresses={"STALE"},
        active_address="STALE",
        last_seen=stale_seen,
    )
    transport.known_peers[fresh_identity.peer_id] = PeerRecord(
        peer_id=fresh_identity.peer_id,
        nickname="fresh",
        noise_public_key=fresh_identity.noise_public_key,
        signing_public_key=fresh_identity.signing_public_key,
        addresses={"FRESH"},
        active_address="FRESH",
        last_seen=fresh_seen,
    )
    transport.clients = {"FRESH": "FRESH"}  # type: ignore[assignment]
    transport._noise_sessions[stale_identity.peer_id] = object()  # type: ignore[assignment]
    transport._pending_noise_payloads[stale_identity.peer_id] = deque([b"pending"])
    transport._reconnect_ready_at["old-address"] = 10.0
    transport._reconnect_ready_at["fresh-address"] = 590.0

    transport._prune_runtime_state(now_monotonic=600.0, now_wall=datetime.now(timezone.utc), force=True)

    assert stale_identity.peer_id not in transport.known_peers
    assert stale_identity.peer_id not in transport._noise_sessions
    assert stale_identity.peer_id not in transport._pending_noise_payloads
    assert "old-address" not in transport._reconnect_ready_at
    assert "fresh-address" in transport._reconnect_ready_at

    fresh_peer = transport.known_peers[fresh_identity.peer_id]
    assert fresh_peer.addresses == {"FRESH"}
    assert fresh_peer.active_address == "FRESH"


def test_stale_public_message_is_ignored_before_delivery_or_relay() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        charlie_identity = Identity.generate()
        public_messages: list[tuple[str, str, datetime, str | None, str | None]] = []
        logs: list[str] = []
        transport = BLEChatTransport(
            identity=local_identity,
            nickname="local",
            system_log=logs.append,
            message_log=lambda sender, content, timestamp, peer_id, message_id: public_messages.append(
                (sender, content, timestamp, peer_id, message_id)
            ),
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=charlie_identity, nickname="charlie", address="C1")
        transport.clients = {"A1": "A1", "C1": "C1"}  # type: ignore[assignment]

        relayed_packets: list[tuple[str, Packet]] = []

        async def fake_write(client, packet: Packet) -> None:
            relayed_packets.append((client, packet))

        transport._write_packet = fake_write  # type: ignore[method-assign]

        stale_timestamp_ms = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000)
        packet = make_public_message_packet(alice_identity, "old news", timestamp_ms=stale_timestamp_ms)
        await transport._handle_decoded_packet("A1", packet)

        assert public_messages == []
        assert relayed_packets == []
        assert any("stale" in message for message in logs)

    asyncio.run(case())


def test_future_skewed_announce_is_ignored_before_peer_registration_or_relay() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        charlie_identity = Identity.generate()
        logs: list[str] = []
        transport = BLEChatTransport(
            identity=local_identity,
            nickname="local",
            system_log=logs.append,
            message_log=lambda *_args: None,
        )
        _register_known_peer(transport, peer_identity=charlie_identity, nickname="charlie", address="C1")
        transport.clients = {"C1": "C1"}  # type: ignore[assignment]

        relayed_packets: list[tuple[str, Packet]] = []

        async def fake_write(client, packet: Packet) -> None:
            relayed_packets.append((client, packet))

        transport._write_packet = fake_write  # type: ignore[method-assign]

        future_timestamp_ms = int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000)
        packet = make_noise_handshake_packet(
            alice_identity,
            local_identity.peer_id,
            b"hs1",
            timestamp_ms=future_timestamp_ms,
        )
        await transport._handle_decoded_packet("A1", packet)

        assert alice_identity.peer_id not in transport._noise_sessions
        assert relayed_packets == []
        assert any("timestamp skew" in message for message in logs)

    asyncio.run(case())


def test_connect_worker_drains_enqueued_candidates() -> None:
    async def case() -> None:
        transport = _make_transport(
            identity=Identity.generate(),
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        seen: list[str] = []

        async def fake_connect(device) -> None:
            seen.append(device.address)

        transport._connect_device = fake_connect  # type: ignore[method-assign]
        transport._connect_queue = asyncio.Queue(maxsize=2)
        worker = asyncio.create_task(transport._connect_worker())

        transport._enqueue_connect_candidate(FakeDevice("A1"))
        transport._enqueue_connect_candidate(FakeDevice("B2"))
        await asyncio.wait_for(transport._connect_queue.join(), timeout=1.0)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

        assert seen == ["A1", "B2"]

    asyncio.run(case())


def test_connect_enqueue_drops_when_queue_is_full() -> None:
    logs: list[str] = []
    transport = BLEChatTransport(
        identity=Identity.generate(),
        nickname="local",
        system_log=logs.append,
        message_log=lambda *_args: None,
    )
    transport._connect_queue = asyncio.Queue(maxsize=1)

    transport._enqueue_connect_candidate(FakeDevice("A1"))
    transport._enqueue_connect_candidate(FakeDevice("B2"))

    queued = transport._connect_queue.get_nowait()

    assert queued.address == "A1"
    assert any("Dropped connect candidate" in message for message in logs)


def test_announce_packet_includes_connected_direct_neighbors() -> None:
    transport = _make_transport(
        identity=Identity.generate(),
        nickname="local",
        private_messages=[],
        receipts=[],
    )
    alice = Identity.generate()
    bob = Identity.generate()
    _register_known_peer(transport, peer_identity=alice, nickname="alice", address="A1")
    _register_known_peer(transport, peer_identity=bob, nickname="bob", address="B1")
    transport.clients = {"A1": "A1", "B1": "B1"}  # type: ignore[assignment]
    transport.known_peers[alice.peer_id].active_address = "A1"
    transport.known_peers[bob.peer_id].active_address = "B1"

    packet = transport._make_announce_packet()
    announcement = Announcement.decode(packet.payload)

    assert set(announcement.direct_neighbors) == {
        bytes.fromhex(alice.peer_id),
        bytes.fromhex(bob.peer_id),
    }
