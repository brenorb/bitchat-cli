from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bitchat_cli.transport as transport_module
from bitchat_cli.constants import MessageType
from bitchat_cli.crypto import Identity
from bitchat_cli.files import transfer_id_for_bytes
from bitchat_cli.fragments import fragment_packet_sequence
from bitchat_cli.peripheral import NullPeripheralEndpoint
from bitchat_cli.protocol import (
    Announcement,
    FilePacket,
    NoisePayload,
    NoisePayloadType,
    Packet,
    make_announce_packet,
    make_noise_handshake_packet,
    make_public_message_packet,
)
from bitchat_cli.state import RuntimePaths
from bitchat_cli.sync import GCSFilter, RequestSyncPacket, SyncTypeFlags, packet_sync_id
from bitchat_cli.transport import BLEChatTransport, PeerRecord


@dataclass
class CapturedPacket:
    peer_id: str
    packet: Packet


@dataclass
class FakeDevice:
    address: str


class FakePeripheralEndpoint(NullPeripheralEndpoint):
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.sent: list[tuple[set[str] | None, set[str], bytes]] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def send(
        self,
        data: bytes,
        *,
        target_addresses: set[str] | None = None,
        exclude_addresses: set[str] | None = None,
    ) -> None:
        self.sent.append((target_addresses, exclude_addresses or set(), data))

    def subscriber_count(self) -> int:
        return 0


def _make_transport(
    *,
    identity: Identity,
    nickname: str,
    private_messages: list[tuple[str, str, datetime, str, str]],
    receipts: list[tuple[str, str, str, datetime]],
    public_files: list[tuple[str, str, str, str, int, datetime, str | None, str]] | None = None,
    private_files: list[tuple[str, str, str, str, int, datetime, str, str]] | None = None,
    peripheral_endpoint: NullPeripheralEndpoint | None = None,
    file_runtime_paths: RuntimePaths | None = None,
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
        public_file_log=(
            None
            if public_files is None
            else lambda sender, saved_path, file_name, mime_type, file_size, timestamp, peer_id, transfer_id: public_files.append(
                (sender, saved_path, file_name, mime_type, file_size, timestamp, peer_id, transfer_id)
            )
        ),
        private_file_log=(
            None
            if private_files is None
            else lambda sender, saved_path, file_name, mime_type, file_size, timestamp, peer_id, transfer_id: private_files.append(
                (sender, saved_path, file_name, mime_type, file_size, timestamp, peer_id, transfer_id)
            )
        ),
        peripheral_endpoint=peripheral_endpoint,
        file_runtime_paths=file_runtime_paths,
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


def test_public_file_without_session_broadcasts_v2_packet(tmp_path: Path) -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        source = tmp_path / "photo.jpg"
        source.write_bytes(b"image-bytes")
        captured: list[Packet] = []

        async def fake_broadcast(packet: Packet, *, exclude_addresses=None) -> None:
            captured.append(packet)

        transport._broadcast_packet = fake_broadcast  # type: ignore[method-assign]

        transfer_id = await transport.send_public_file(source)

        assert len(captured) == 1
        assert captured[0].packet_type == MessageType.FILE_TRANSFER
        assert captured[0].version == 2
        file_packet = FilePacket.decode(captured[0].payload)
        assert file_packet.file_name == "photo.jpg"
        assert transfer_id == transfer_id_for_bytes(file_packet.encode())

    asyncio.run(case())


def test_private_file_without_session_queues_payload_and_starts_handshake(tmp_path: Path) -> None:
    async def case() -> None:
        alice = Identity.generate()
        bob = Identity.generate()
        transport = _make_transport(
            identity=alice,
            nickname="alice",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=bob, nickname="bob", address="B1")
        source = tmp_path / "note.txt"
        source.write_text("hello bob", encoding="utf-8")
        captured: list[CapturedPacket] = []

        async def fake_send(peer_id: str, packet: Packet) -> None:
            captured.append(CapturedPacket(peer_id=peer_id, packet=packet))

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        transfer_id = await transport.send_private_file(bob.peer_id, source)
        queued = list(transport._pending_noise_payloads[bob.peer_id])
        typed_payload = NoisePayload.decode(queued[0])
        file_packet = FilePacket.decode(typed_payload.payload)

        assert transfer_id == transfer_id_for_bytes(file_packet.encode())
        assert len(captured) == 1
        assert captured[0].packet.packet_type == MessageType.NOISE_HANDSHAKE
        assert len(queued) == 1
        assert typed_payload.payload_type == NoisePayloadType.FILE_TRANSFER

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


def test_public_file_receive_saves_file_and_logs_callback(tmp_path: Path) -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        public_files: list[tuple[str, str, str, str, int, datetime, str | None, str]] = []
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
            public_files=public_files,
            file_runtime_paths=RuntimePaths(tmp_path),
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        packet = Packet.decode(
            transport_module.make_file_transfer_packet(
                alice_identity,
                FilePacket(
                    file_name="photo.jpg",
                    file_size=5,
                    mime_type="image/jpeg",
                    content=b"12345",
                ),
                timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            ).encode(padding=False)
        )

        await transport._handle_decoded_packet("A1", packet)

        assert len(public_files) == 1
        assert public_files[0][0] == "alice"
        assert public_files[0][2] == "photo.jpg"
        assert public_files[0][3] == "image/jpeg"
        assert public_files[0][4] == 5
        assert Path(public_files[0][1]).read_bytes() == b"12345"

    asyncio.run(case())


def test_private_file_handshake_flow_saves_file_and_sends_delivered_receipt(tmp_path: Path) -> None:
    async def case() -> None:
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        alice_receipts: list[tuple[str, str, str, datetime]] = []
        bob_private_files: list[tuple[str, str, str, str, int, datetime, str, str]] = []
        alice = _make_transport(
            identity=alice_identity,
            nickname="alice",
            private_messages=[],
            receipts=alice_receipts,
        )
        bob = _make_transport(
            identity=bob_identity,
            nickname="bob",
            private_messages=[],
            receipts=[],
            private_files=bob_private_files,
            file_runtime_paths=RuntimePaths(tmp_path),
        )
        _register_known_peer(alice, peer_identity=bob_identity, nickname="bob", address="B1")
        _register_known_peer(bob, peer_identity=alice_identity, nickname="alice", address="A1")

        alice_outbox: list[CapturedPacket] = []
        bob_outbox: list[CapturedPacket] = []
        source = tmp_path / "send.txt"
        source.write_text("hello file", encoding="utf-8")

        async def alice_send(peer_id: str, packet: Packet) -> None:
            alice_outbox.append(CapturedPacket(peer_id=peer_id, packet=packet))

        async def bob_send(peer_id: str, packet: Packet) -> None:
            bob_outbox.append(CapturedPacket(peer_id=peer_id, packet=packet))

        alice._send_packet_to_peer = alice_send  # type: ignore[method-assign]
        bob._send_packet_to_peer = bob_send  # type: ignore[method-assign]

        transfer_id = await alice.send_private_file(bob_identity.peer_id, source)
        await bob._handle_decoded_packet("A1", alice_outbox.pop(0).packet)
        await alice._handle_decoded_packet("B1", bob_outbox.pop(0).packet)
        await bob._handle_decoded_packet("A1", alice_outbox.pop(0).packet)
        await bob._handle_decoded_packet("A1", alice_outbox.pop(0).packet)

        assert len(bob_private_files) == 1
        assert bob_private_files[0][0] == "alice"
        assert bob_private_files[0][2] == "send.txt"
        assert bob_private_files[0][4] == len("hello file")
        assert bob_private_files[0][7] == transfer_id
        assert Path(bob_private_files[0][1]).read_text(encoding="utf-8") == "hello file"
        assert [item.packet.packet_type for item in bob_outbox] == [MessageType.NOISE_ENCRYPTED]

        await alice._handle_decoded_packet("B1", bob_outbox.pop(0).packet)

        assert alice_receipts == [(bob_identity.peer_id, "delivered", transfer_id, alice_receipts[0][3])]

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


def test_transport_start_stop_controls_peripheral_endpoint() -> None:
    async def case() -> None:
        endpoint = FakePeripheralEndpoint()
        transport = _make_transport(
            identity=Identity.generate(),
            nickname="local",
            private_messages=[],
            receipts=[],
            peripheral_endpoint=endpoint,
        )

        await transport.start()
        await transport.stop()

        assert endpoint.started is True
        assert endpoint.stopped is True

    asyncio.run(case())


def test_public_broadcast_reaches_peripheral_subscribers() -> None:
    async def case() -> None:
        endpoint = FakePeripheralEndpoint()
        local_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
            peripheral_endpoint=endpoint,
        )

        await transport.send_public_message("hello app")

        assert len(endpoint.sent) == 1
        target_addresses, exclude_addresses, raw = endpoint.sent[0]
        assert target_addresses is None
        assert exclude_addresses == set()
        decoded = Packet.decode(raw)
        assert decoded.packet_type == MessageType.MESSAGE
        assert decoded.payload == b"hello app"

    asyncio.run(case())


def test_direct_send_to_peripheral_peer_targets_matching_subscriber() -> None:
    async def case() -> None:
        endpoint = FakePeripheralEndpoint()
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
            peripheral_endpoint=endpoint,
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="peripheral:A1")
        transport.known_peers[alice_identity.peer_id].active_address = "peripheral:A1"

        packet = make_noise_handshake_packet(local_identity, alice_identity.peer_id, b"hs1")

        assert await transport._send_packet_to_peer(alice_identity.peer_id, packet) is True
        assert len(endpoint.sent) == 1
        target_addresses, exclude_addresses, raw = endpoint.sent[0]
        assert target_addresses == {"peripheral:A1"}
        assert exclude_addresses == set()
        assert Packet.decode(raw).packet_type == MessageType.NOISE_HANDSHAKE

    asyncio.run(case())


def test_peripheral_subscribe_sends_targeted_announce() -> None:
    async def case() -> None:
        endpoint = FakePeripheralEndpoint()
        transport = _make_transport(
            identity=Identity.generate(),
            nickname="local",
            private_messages=[],
            receipts=[],
            peripheral_endpoint=endpoint,
        )

        await transport._handle_peripheral_subscribe("peripheral:A1")

        assert len(endpoint.sent) == 1
        target_addresses, exclude_addresses, raw = endpoint.sent[0]
        assert target_addresses == {"peripheral:A1"}
        assert exclude_addresses == set()
        assert Packet.decode(raw).packet_type == MessageType.ANNOUNCE

    asyncio.run(case())


def test_peripheral_unsubscribe_clears_active_address() -> None:
    transport = _make_transport(
        identity=Identity.generate(),
        nickname="local",
        private_messages=[],
        receipts=[],
    )
    alice_identity = Identity.generate()
    _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="peripheral:A1")
    transport.known_peers[alice_identity.peer_id].active_address = "peripheral:A1"

    transport._handle_peripheral_unsubscribe("peripheral:A1")

    assert "peripheral:A1" not in transport.known_peers[alice_identity.peer_id].addresses
    assert transport.known_peers[alice_identity.peer_id].active_address is None


def test_outbound_fragment_write_populates_fragment_sync_cache() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )

        class FakeClient:
            def __init__(self) -> None:
                self.writes: list[bytes] = []

            async def write_gatt_char(self, _characteristic, data: bytes, response: bool = False) -> None:
                self.writes.append(data)

        oversized = Packet(
            packet_type=MessageType.MESSAGE,
            sender_id=bytes.fromhex(local_identity.peer_id),
            payload=bytes(range(256)) * 4,
            timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        ).sign(local_identity)
        expected_fragments = fragment_packet_sequence(oversized)
        client = FakeClient()

        await transport._write_packet(client, oversized)

        assert len(client.writes) == len(expected_fragments)
        assert len(transport._sync_fragments) == len(expected_fragments)

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


def test_background_scan_off_duration_extends_when_idle() -> None:
    transport = BLEChatTransport(
        identity=Identity.generate(),
        nickname="local",
        system_log=lambda *_args: None,
        message_log=lambda *_args: None,
        background_mode=True,
        scan_interval=5.0,
    )
    transport._last_mesh_activity_at = 0.0

    on_duration, off_duration = transport._background_scan_durations(now_monotonic=90.0)

    assert on_duration == 5.0
    assert off_duration == 60.0


def test_background_scan_stays_continuous_during_discovery_grace() -> None:
    transport = BLEChatTransport(
        identity=Identity.generate(),
        nickname="local",
        system_log=lambda *_args: None,
        message_log=lambda *_args: None,
        background_mode=True,
        scan_interval=5.0,
    )
    transport._last_mesh_activity_at = 0.0

    on_duration, off_duration = transport._background_scan_durations(now_monotonic=15.0)

    assert on_duration == 5.0
    assert off_duration == 0.0


def test_background_scan_off_duration_extends_with_live_peers() -> None:
    transport = BLEChatTransport(
        identity=Identity.generate(),
        nickname="local",
        system_log=lambda *_args: None,
        message_log=lambda *_args: None,
        background_mode=True,
        scan_interval=5.0,
    )
    transport.clients = {"A1": "A1"}  # type: ignore[assignment]

    _on_duration, off_duration = transport._background_scan_durations(now_monotonic=10.0)

    assert off_duration == 30.0


def test_background_scan_off_duration_extends_with_peripheral_subscribers() -> None:
    endpoint = FakePeripheralEndpoint()
    transport = BLEChatTransport(
        identity=Identity.generate(),
        nickname="local",
        system_log=lambda *_args: None,
        message_log=lambda *_args: None,
        background_mode=True,
        scan_interval=5.0,
        peripheral_endpoint=endpoint,
    )

    endpoint.subscriber_count = lambda: 1  # type: ignore[method-assign]
    _on_duration, off_duration = transport._background_scan_durations(now_monotonic=10.0)

    assert off_duration == 30.0


def test_reconnect_backoff_grows_for_flaky_peer() -> None:
    transport = BLEChatTransport(
        identity=Identity.generate(),
        nickname="local",
        system_log=lambda *_args: None,
        message_log=lambda *_args: None,
        background_mode=True,
        scan_interval=5.0,
    )

    transport._record_reconnect_backoff("A1", multiplier=1.0, now_monotonic=100.0)
    first_ready = transport._reconnect_ready_at["A1"]
    transport._record_reconnect_backoff("A1", multiplier=1.0, now_monotonic=105.0)
    second_ready = transport._reconnect_ready_at["A1"]

    assert first_ready == 110.0
    assert second_ready == 125.0


def test_stable_connection_resets_reconnect_backoff() -> None:
    transport = BLEChatTransport(
        identity=Identity.generate(),
        nickname="local",
        system_log=lambda *_args: None,
        message_log=lambda *_args: None,
        background_mode=True,
        scan_interval=5.0,
    )
    transport._record_reconnect_backoff("A1", multiplier=1.0, now_monotonic=100.0)
    transport._record_reconnect_backoff("A1", multiplier=1.0, now_monotonic=105.0)
    transport.clients = {"A1": "A1"}  # type: ignore[assignment]
    transport._connected_at["A1"] = 120.0

    original_monotonic = transport_module.time.monotonic
    transport_module.time.monotonic = lambda: 190.0
    try:
        transport._on_disconnect("A1")
    finally:
        transport_module.time.monotonic = original_monotonic

    assert transport._reconnect_ready_at["A1"] == 200.0


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


def test_announce_packet_includes_peripheral_direct_neighbors() -> None:
    transport = _make_transport(
        identity=Identity.generate(),
        nickname="local",
        private_messages=[],
        receipts=[],
    )
    alice = Identity.generate()
    _register_known_peer(transport, peer_identity=alice, nickname="alice", address="peripheral:A1")
    transport.known_peers[alice.peer_id].active_address = "peripheral:A1"

    packet = transport._make_announce_packet()
    announcement = Announcement.decode(packet.payload)

    assert announcement.direct_neighbors == [bytes.fromhex(alice.peer_id)]


def test_new_peer_announce_triggers_public_request_sync() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        transport.clients = {"A1": "A1"}  # type: ignore[assignment]

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        announce = make_announce_packet(alice_identity, "alice", timestamp_ms=now_ms)
        await transport._handle_decoded_packet("A1", announce)

        assert sent_packets[0][0] == alice_identity.peer_id
        assert sent_packets[0][1].packet_type == MessageType.REQUEST_SYNC
        assert sent_packets[0][1].ttl == 0
        decoded = RequestSyncPacket.decode(sent_packets[0][1].payload)
        assert decoded is not None
        assert decoded.types == SyncTypeFlags.from_message_types(
            [MessageType.ANNOUNCE, MessageType.MESSAGE, MessageType.FRAGMENT, MessageType.FILE_TRANSFER]
        )

    asyncio.run(case())


def test_request_sync_returns_missing_public_packets() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=bob_identity, nickname="bob", address="B1")
        transport.clients = {"A1": "A1", "B1": "B1"}  # type: ignore[assignment]

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        announce = make_announce_packet(bob_identity, "bob", timestamp_ms=now_ms)
        message = make_public_message_packet(bob_identity, "hello mesh", timestamp_ms=now_ms + 1)
        await transport._handle_decoded_packet("B1", announce)
        sent_packets.clear()
        await transport._handle_decoded_packet("B1", message)

        request = RequestSyncPacket(
            p=4,
            m=1,
            data=b"",
            types=SyncTypeFlags.PUBLIC_MESSAGES,
        )
        request_packet = Packet(
            packet_type=MessageType.REQUEST_SYNC,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            recipient_id=bytes.fromhex(local_identity.peer_id),
            payload=request.encode(),
            ttl=0,
        ).sign(alice_identity)

        await transport._handle_decoded_packet("A1", request_packet)

        assert len(sent_packets) == 2
        assert all(peer_id == alice_identity.peer_id for peer_id, _packet in sent_packets)
        assert {packet.packet_type for _peer_id, packet in sent_packets} == {
            MessageType.ANNOUNCE,
            MessageType.MESSAGE,
        }
        assert all(packet.ttl == 0 for _peer_id, packet in sent_packets)
        assert all(packet.is_rsr for _peer_id, packet in sent_packets)

    asyncio.run(case())


def test_send_request_sync_includes_supported_file_transfer_type() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        transport.clients = {"A1": "A1"}  # type: ignore[assignment]

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        requested = SyncTypeFlags.from_message_types([MessageType.MESSAGE, MessageType.FILE_TRANSFER])
        assert await transport.send_request_sync(alice_identity.peer_id, types=requested) is True

        decoded = RequestSyncPacket.decode(sent_packets[0][1].payload)

        assert decoded is not None
        assert decoded.types == requested

    asyncio.run(case())


def test_request_sync_filter_suppresses_known_public_packets() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=bob_identity, nickname="bob", address="B1")
        transport.clients = {"A1": "A1", "B1": "B1"}  # type: ignore[assignment]

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        message = make_public_message_packet(bob_identity, "known message", timestamp_ms=now_ms)
        await transport._handle_decoded_packet("B1", message)
        sent_packets.clear()

        packet_id = packet_sync_id(message)
        p, m, data = GCSFilter.build_filter(ids=[packet_id], max_bytes=128, target_fpr=0.01)
        request = RequestSyncPacket(
            p=p,
            m=m,
            data=data,
            types=SyncTypeFlags.from_message_types([MessageType.MESSAGE]),
        )
        request_packet = Packet(
            packet_type=MessageType.REQUEST_SYNC,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            recipient_id=bytes.fromhex(local_identity.peer_id),
            payload=request.encode(),
            ttl=0,
        ).sign(alice_identity)

        await transport._handle_decoded_packet("A1", request_packet)

        assert sent_packets == []

    asyncio.run(case())


def test_request_sync_returns_missing_public_file_transfers() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=bob_identity, nickname="bob", address="B1")
        transport.clients = {"A1": "A1", "B1": "B1"}  # type: ignore[assignment]

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        announce = make_announce_packet(bob_identity, "bob", timestamp_ms=now_ms)
        file_transfer = transport_module.make_file_transfer_packet(
            bob_identity,
            FilePacket(
                file_name="photo.jpg",
                file_size=5,
                mime_type="image/jpeg",
                content=b"12345",
            ),
            timestamp_ms=now_ms + 1,
        )
        await transport._handle_decoded_packet("B1", announce)
        sent_packets.clear()
        await transport._handle_decoded_packet("B1", file_transfer)
        sent_packets.clear()

        request = RequestSyncPacket(
            p=4,
            m=1,
            data=b"",
            types=SyncTypeFlags.from_message_types([MessageType.FILE_TRANSFER]),
        )
        request_packet = Packet(
            packet_type=MessageType.REQUEST_SYNC,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            recipient_id=bytes.fromhex(local_identity.peer_id),
            payload=request.encode(),
            ttl=0,
        ).sign(alice_identity)

        await transport._handle_decoded_packet("A1", request_packet)

        assert len(sent_packets) == 1
        assert sent_packets[0][0] == alice_identity.peer_id
        assert sent_packets[0][1].packet_type == MessageType.FILE_TRANSFER
        assert sent_packets[0][1].ttl == 0
        assert sent_packets[0][1].is_rsr is True

    asyncio.run(case())


def test_request_sync_filter_suppresses_known_public_file_transfers() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=bob_identity, nickname="bob", address="B1")
        transport.clients = {"A1": "A1", "B1": "B1"}  # type: ignore[assignment]

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        file_transfer = transport_module.make_file_transfer_packet(
            bob_identity,
            FilePacket(
                file_name="photo.jpg",
                file_size=5,
                mime_type="image/jpeg",
                content=b"12345",
            ),
            timestamp_ms=now_ms,
        )
        await transport._handle_decoded_packet("B1", file_transfer)
        sent_packets.clear()

        packet_id = packet_sync_id(file_transfer)
        p, m, data = GCSFilter.build_filter(ids=[packet_id], max_bytes=128, target_fpr=0.01)
        request = RequestSyncPacket(
            p=p,
            m=m,
            data=data,
            types=SyncTypeFlags.from_message_types([MessageType.FILE_TRANSFER]),
        )
        request_packet = Packet(
            packet_type=MessageType.REQUEST_SYNC,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            recipient_id=bytes.fromhex(local_identity.peer_id),
            payload=request.encode(),
            ttl=0,
        ).sign(alice_identity)

        await transport._handle_decoded_packet("A1", request_packet)

        assert sent_packets == []

    asyncio.run(case())


def test_request_sync_returns_missing_fragments() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=bob_identity, nickname="bob", address="B1")
        transport.clients = {"A1": "A1", "B1": "B1"}  # type: ignore[assignment]

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        async def fake_write(_client, _packet: Packet) -> None:
            return None

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]
        transport._write_packet = fake_write  # type: ignore[method-assign]

        oversized = Packet(
            packet_type=MessageType.MESSAGE,
            sender_id=bytes.fromhex(bob_identity.peer_id),
            payload=bytes(range(256)) * 4,
            timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        ).sign(bob_identity)
        fragments = fragment_packet_sequence(oversized)
        for fragment in fragments:
            await transport._handle_decoded_packet("B1", fragment)
        sent_packets.clear()

        request = RequestSyncPacket(
            p=4,
            m=1,
            data=b"",
            types=SyncTypeFlags.from_message_types([MessageType.FRAGMENT]),
        )
        request_packet = Packet(
            packet_type=MessageType.REQUEST_SYNC,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            recipient_id=bytes.fromhex(local_identity.peer_id),
            payload=request.encode(),
            ttl=0,
        ).sign(alice_identity)

        await transport._handle_decoded_packet("A1", request_packet)

        assert len(sent_packets) == len(fragments)
        assert all(peer_id == alice_identity.peer_id for peer_id, _packet in sent_packets)
        assert all(packet.packet_type == MessageType.FRAGMENT for _peer_id, packet in sent_packets)
        assert all(packet.ttl == 0 and packet.is_rsr for _peer_id, packet in sent_packets)

    asyncio.run(case())


def test_request_sync_filter_suppresses_known_fragments() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=bob_identity, nickname="bob", address="B1")
        transport.clients = {"A1": "A1", "B1": "B1"}  # type: ignore[assignment]

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        async def fake_write(_client, _packet: Packet) -> None:
            return None

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]
        transport._write_packet = fake_write  # type: ignore[method-assign]

        oversized = Packet(
            packet_type=MessageType.MESSAGE,
            sender_id=bytes.fromhex(bob_identity.peer_id),
            payload=bytes(range(256)) * 4,
            timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        ).sign(bob_identity)
        fragments = fragment_packet_sequence(oversized)
        for fragment in fragments:
            await transport._handle_decoded_packet("B1", fragment)
        sent_packets.clear()

        fragment_ids = [packet_sync_id(fragment) for fragment in fragments]
        p, m, data = GCSFilter.build_filter(ids=fragment_ids, max_bytes=128, target_fpr=0.01)
        request = RequestSyncPacket(
            p=p,
            m=m,
            data=data,
            types=SyncTypeFlags.from_message_types([MessageType.FRAGMENT]),
        )
        request_packet = Packet(
            packet_type=MessageType.REQUEST_SYNC,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            recipient_id=bytes.fromhex(local_identity.peer_id),
            payload=request.encode(),
            ttl=0,
        ).sign(alice_identity)

        await transport._handle_decoded_packet("A1", request_packet)

        assert sent_packets == []

    asyncio.run(case())


def test_request_sync_returns_local_announce_when_cached() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        transport.clients = {"A1": "A1"}  # type: ignore[assignment]

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        await transport.set_nickname("clanker")
        sent_packets.clear()

        request = RequestSyncPacket(
            p=4,
            m=1,
            data=b"",
            types=SyncTypeFlags.from_message_types([MessageType.ANNOUNCE]),
        )
        request_packet = Packet(
            packet_type=MessageType.REQUEST_SYNC,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            recipient_id=bytes.fromhex(local_identity.peer_id),
            payload=request.encode(),
            ttl=0,
        ).sign(alice_identity)

        await transport._handle_decoded_packet("A1", request_packet)

        assert len(sent_packets) == 1
        assert sent_packets[0][0] == alice_identity.peer_id
        assert sent_packets[0][1].packet_type == MessageType.ANNOUNCE
        announcement = Announcement.decode(sent_packets[0][1].payload)
        assert announcement.nickname == "clanker"

    asyncio.run(case())


def test_reconnected_peer_announce_triggers_public_request_sync() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        transport = _make_transport(
            identity=local_identity,
            nickname="local",
            private_messages=[],
            receipts=[],
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        transport.clients = {"A1": "A1"}  # type: ignore[assignment]
        transport.known_peers[alice_identity.peer_id].active_address = "A1"
        transport._on_disconnect("A1")

        sent_packets: list[tuple[str, Packet]] = []

        async def fake_send(peer_id: str, packet: Packet) -> bool:
            sent_packets.append((peer_id, packet))
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        announce = make_announce_packet(alice_identity, "alice", timestamp_ms=now_ms)
        await transport._handle_decoded_packet("A1", announce)

        assert len(sent_packets) == 1
        assert sent_packets[0][0] == alice_identity.peer_id
        assert sent_packets[0][1].packet_type == MessageType.REQUEST_SYNC

    asyncio.run(case())


def test_unsolicited_rsr_public_message_is_rejected() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        received_messages: list[tuple[str, str]] = []
        transport = BLEChatTransport(
            identity=local_identity,
            nickname="local",
            system_log=lambda _message: None,
            message_log=lambda sender, content, _timestamp, _peer_id, _message_id: received_messages.append(
                (sender, content)
            ),
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")

        packet = Packet(
            packet_type=MessageType.MESSAGE,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            payload=b"late sync",
            timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            signature=None,
            ttl=0,
            is_rsr=True,
        ).sign(alice_identity)

        await transport._handle_decoded_packet("A1", packet)

        assert received_messages == []

    asyncio.run(case())


def test_solicited_rsr_replay_is_validated_against_responding_peer() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        bob_identity = Identity.generate()
        received_messages: list[tuple[str, str]] = []
        transport = BLEChatTransport(
            identity=local_identity,
            nickname="local",
            system_log=lambda _message: None,
            message_log=lambda sender, content, _timestamp, _peer_id, _message_id: received_messages.append(
                (sender, content)
            ),
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        _register_known_peer(transport, peer_identity=bob_identity, nickname="bob", address="B1")
        transport.clients = {"A1": "A1"}  # type: ignore[assignment]

        async def fake_send(_peer_id: str, _packet: Packet) -> bool:
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]
        assert await transport.send_request_sync(alice_identity.peer_id) is True

        packet = Packet(
            packet_type=MessageType.MESSAGE,
            sender_id=bytes.fromhex(bob_identity.peer_id),
            payload=b"via alice cache",
            timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            ttl=0,
            is_rsr=True,
        ).sign(bob_identity)

        await transport._handle_decoded_packet("A1", packet)

        assert received_messages == [("bob", "via alice cache")]

    asyncio.run(case())


def test_solicited_self_authored_rsr_replay_is_accepted_from_responding_peer() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        received_messages: list[tuple[str, str]] = []
        transport = BLEChatTransport(
            identity=local_identity,
            nickname="local",
            system_log=lambda _message: None,
            message_log=lambda sender, content, _timestamp, _peer_id, _message_id: received_messages.append(
                (sender, content)
            ),
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        transport.clients = {"A1": "A1"}  # type: ignore[assignment]

        async def fake_send(_peer_id: str, _packet: Packet) -> bool:
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]
        assert await transport.send_request_sync(alice_identity.peer_id) is True

        packet = Packet(
            packet_type=MessageType.MESSAGE,
            sender_id=bytes.fromhex(local_identity.peer_id),
            payload=b"my cached line",
            timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            ttl=0,
            is_rsr=True,
        ).sign(local_identity)

        await transport._handle_decoded_packet("A1", packet)

        assert received_messages == [("local", "my cached line")]

    asyncio.run(case())


def test_unsolicited_self_authored_rsr_replay_is_rejected() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        received_messages: list[tuple[str, str]] = []
        transport = BLEChatTransport(
            identity=local_identity,
            nickname="local",
            system_log=lambda _message: None,
            message_log=lambda sender, content, _timestamp, _peer_id, _message_id: received_messages.append(
                (sender, content)
            ),
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")

        packet = Packet(
            packet_type=MessageType.MESSAGE,
            sender_id=bytes.fromhex(local_identity.peer_id),
            payload=b"my cached line",
            timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            ttl=0,
            is_rsr=True,
        ).sign(local_identity)

        await transport._handle_decoded_packet("A1", packet)

        assert received_messages == []

    asyncio.run(case())


def test_solicited_rsr_public_message_bypasses_stale_age_guard() -> None:
    async def case() -> None:
        local_identity = Identity.generate()
        alice_identity = Identity.generate()
        received_messages: list[tuple[str, str]] = []
        transport = BLEChatTransport(
            identity=local_identity,
            nickname="local",
            system_log=lambda _message: None,
            message_log=lambda sender, content, _timestamp, _peer_id, _message_id: received_messages.append(
                (sender, content)
            ),
        )
        _register_known_peer(transport, peer_identity=alice_identity, nickname="alice", address="A1")
        transport.clients = {"A1": "A1"}  # type: ignore[assignment]

        async def fake_send(_peer_id: str, _packet: Packet) -> bool:
            return True

        transport._send_packet_to_peer = fake_send  # type: ignore[method-assign]
        assert await transport.send_request_sync(alice_identity.peer_id) is True

        stale_timestamp_ms = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000)
        packet = Packet(
            packet_type=MessageType.MESSAGE,
            sender_id=bytes.fromhex(alice_identity.peer_id),
            payload=b"caught up",
            timestamp_ms=stale_timestamp_ms,
            signature=None,
            ttl=0,
            is_rsr=True,
        ).sign(alice_identity)

        await transport._handle_decoded_packet("A1", packet)

        assert received_messages == [("alice", "caught up")]

    asyncio.run(case())
