from __future__ import annotations

from bitchat_cli.nostr import (
    NostrIdentity,
    create_private_message_event,
    decode_bitchat_payload,
    decode_npub,
    decrypt_private_message,
    encode_bitchat_private_message,
    encode_bitchat_private_message_no_recipient,
)


def test_npub_round_trip_from_private_key() -> None:
    identity = NostrIdentity.from_private_key_hex("11" * 32)

    assert decode_npub(identity.npub) == identity.public_key_hex


def test_embedded_bitchat_private_message_round_trip() -> None:
    encoded = encode_bitchat_private_message(
        sender_peer_id="1122334455667788",
        recipient_peer_id="8877665544332211",
        message_id="m-1",
        content="hello fallback",
    )

    decoded = decode_bitchat_payload(encoded)

    assert decoded.sender_peer_id == "1122334455667788"
    assert decoded.peer_id == "8877665544332211"
    assert decoded.message_kind == "private_message"
    assert decoded.message_id == "m-1"
    assert decoded.content == "hello fallback"


def test_private_message_event_encrypts_and_decrypts() -> None:
    sender = NostrIdentity.from_private_key_hex("12" * 32)
    recipient = NostrIdentity.from_private_key_hex("13" * 32)
    embedded = encode_bitchat_private_message(
        sender_peer_id="0011223344556677",
        recipient_peer_id="7766554433221100",
        message_id="m-2",
        content="hello nostr",
    )

    event = create_private_message_event(
        content=embedded,
        recipient_pubkey_hex=recipient.public_key_hex,
        sender_identity=sender,
    )
    content, sender_pubkey_hex, timestamp = decrypt_private_message(
        gift_wrap=event,
        recipient_identity=recipient,
    )

    assert content == embedded
    assert sender_pubkey_hex == sender.public_key_hex
    assert timestamp > 0


def test_embedded_bitchat_private_message_without_recipient_round_trip() -> None:
    encoded = encode_bitchat_private_message_no_recipient(
        sender_peer_id="1122334455667788",
        message_id="m-geo-1",
        content="hello geo fallback",
    )

    decoded = decode_bitchat_payload(encoded)

    assert decoded.peer_id == ""
    assert decoded.sender_peer_id == "1122334455667788"
    assert decoded.message_kind == "private_message"
    assert decoded.message_id == "m-geo-1"
    assert decoded.content == "hello geo fallback"
