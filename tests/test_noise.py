from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric import x25519

from bitchat_cli.noise import (
    MAX_HANDSHAKE_MESSAGE_SIZE,
    MAX_TRANSPORT_MESSAGE_SIZE,
    NoiseError,
    XXHandshake,
)


VECTOR_1 = {
    "prologue": "4a6f686e2047616c74",
    "init_static": "e61ef9919cde45dd5f82166404bd08e38bceb5dfdfded0a34c8df7ed542214d1",
    "init_ephemeral": "893e28b9dc6ca8d611ab664754b8ceb7bac5117349a4439a6b0569da977c464a",
    "resp_static": "4a3acbfdb163dec651dfa3194dece676d437029c62a408b4c5ea9114246e4893",
    "resp_ephemeral": "bbdb4cdbd309f1a1f2e1456967fe288cadd6f712d65dc7b7793d5e63da6b375b",
    "handshake_hash": "c8e5f64e846193be2a834104c2a009868d6c9f3bd3c186299888b488b2f1f58e",
    "messages": [
        {
            "payload": "4c756477696720766f6e204d69736573",
            "ciphertext": "ca35def5ae56cec33dc2036731ab14896bc4c75dbb07a61f879f8e3afa4c79444c756477696720766f6e204d69736573",
        },
        {
            "payload": "4d757272617920526f746862617264",
            "ciphertext": "95ebc60d2b1fa672c1f46a8aa265ef51bfe38e7ccb39ec5be34069f14480884381cbad1f276e038c48378ffce2b65285e08d6b68aaa3629a5a8639392490e5b9bd5269c2f1e4f488ed8831161f19b7815528f8982ffe09be9b5c412f8a0db50f8814c7194e83f23dbd8d162c9326ad",
        },
        {
            "payload": "462e20412e20486179656b",
            "ciphertext": "c7195ffacac1307ff99046f219750fc47693e23c3cb08b89c2af808b444850a80ae475b9df0f169ae80a89be0865b57f58c9fea0d4ec82a286427402f113e4b6ae769a1d95941d49b25030",
        },
        {
            "payload": "4361726c204d656e676572",
            "ciphertext": "96763ed773f8e47bb3712f0e29b3060ffc956ffc146cee53d5e1df",
        },
        {
            "payload": "4a65616e2d426170746973746520536179",
            "ciphertext": "3e40f15f6f3a46ae446b253bf8b1d9ffb6ed9b174d272328ff91a7e2e5c79c07f5",
        },
        {
            "payload": "457567656e2042f6686d20766f6e2042617765726b",
            "ciphertext": "eb3f3515110702e047a6c9da4478b6ead94873c11c0f2d710ddb3f09fce024b3a58502ae3f",
        },
    ],
}


def _private_key(hex_value: str) -> x25519.X25519PrivateKey:
    return x25519.X25519PrivateKey.from_private_bytes(bytes.fromhex(hex_value))


def test_noise_xx_matches_official_vector() -> None:
    initiator = XXHandshake(
        role="initiator",
        local_static_private_key=_private_key(VECTOR_1["init_static"]),
        prologue=bytes.fromhex(VECTOR_1["prologue"]),
        predetermined_ephemeral_private_key=_private_key(VECTOR_1["init_ephemeral"]),
    )
    responder = XXHandshake(
        role="responder",
        local_static_private_key=_private_key(VECTOR_1["resp_static"]),
        prologue=bytes.fromhex(VECTOR_1["prologue"]),
        predetermined_ephemeral_private_key=_private_key(VECTOR_1["resp_ephemeral"]),
    )

    message_1 = initiator.write_message(bytes.fromhex(VECTOR_1["messages"][0]["payload"]))
    payload_1 = responder.read_message(message_1)
    message_2 = responder.write_message(bytes.fromhex(VECTOR_1["messages"][1]["payload"]))
    payload_2 = initiator.read_message(message_2)
    message_3 = initiator.write_message(bytes.fromhex(VECTOR_1["messages"][2]["payload"]))
    payload_3 = responder.read_message(message_3)

    assert message_1.hex() == VECTOR_1["messages"][0]["ciphertext"]
    assert message_2.hex() == VECTOR_1["messages"][1]["ciphertext"]
    assert message_3.hex() == VECTOR_1["messages"][2]["ciphertext"]
    assert payload_1.hex() == VECTOR_1["messages"][0]["payload"]
    assert payload_2.hex() == VECTOR_1["messages"][1]["payload"]
    assert payload_3.hex() == VECTOR_1["messages"][2]["payload"]
    assert initiator.handshake_hash.hex() == VECTOR_1["handshake_hash"]
    assert responder.handshake_hash.hex() == VECTOR_1["handshake_hash"]

    initiator_send, initiator_receive = initiator.transport_ciphers(use_extracted_nonce=False)
    responder_send, responder_receive = responder.transport_ciphers(use_extracted_nonce=False)

    transport_1 = responder_send.encrypt(bytes.fromhex(VECTOR_1["messages"][3]["payload"]))
    transport_2 = initiator_send.encrypt(bytes.fromhex(VECTOR_1["messages"][4]["payload"]))
    transport_3 = responder_send.encrypt(bytes.fromhex(VECTOR_1["messages"][5]["payload"]))

    assert transport_1.hex() == VECTOR_1["messages"][3]["ciphertext"]
    assert transport_2.hex() == VECTOR_1["messages"][4]["ciphertext"]
    assert transport_3.hex() == VECTOR_1["messages"][5]["ciphertext"]
    assert initiator_receive.decrypt(transport_1).hex() == VECTOR_1["messages"][3]["payload"]
    assert responder_receive.decrypt(transport_2).hex() == VECTOR_1["messages"][4]["payload"]
    assert initiator_receive.decrypt(transport_3).hex() == VECTOR_1["messages"][5]["payload"]


def test_noise_transport_round_trip_with_extracted_nonces() -> None:
    initiator = XXHandshake(role="initiator", local_static_private_key=x25519.X25519PrivateKey.generate())
    responder = XXHandshake(role="responder", local_static_private_key=x25519.X25519PrivateKey.generate())

    responder.read_message(initiator.write_message())
    initiator.read_message(responder.write_message())
    responder.read_message(initiator.write_message())

    initiator_send, initiator_receive = initiator.transport_ciphers(use_extracted_nonce=True)
    responder_send, responder_receive = responder.transport_ciphers(use_extracted_nonce=True)

    encrypted_1 = initiator_send.encrypt(b"hello")
    encrypted_2 = initiator_send.encrypt(b"again")
    encrypted_3 = responder_send.encrypt(b"world")

    assert encrypted_1[:4] == b"\x00\x00\x00\x00"
    assert encrypted_2[:4] == b"\x00\x00\x00\x01"
    assert encrypted_3[:4] == b"\x00\x00\x00\x00"
    assert responder_receive.decrypt(encrypted_1) == b"hello"
    assert responder_receive.decrypt(encrypted_2) == b"again"
    assert initiator_receive.decrypt(encrypted_3) == b"world"


def test_noise_enforces_message_limits() -> None:
    initiator = XXHandshake(role="initiator", local_static_private_key=x25519.X25519PrivateKey.generate())
    responder = XXHandshake(role="responder", local_static_private_key=x25519.X25519PrivateKey.generate())

    with_oversized_handshake_payload = b"x" * (MAX_HANDSHAKE_MESSAGE_SIZE + 1)
    with_oversized_transport_payload = b"x" * (MAX_TRANSPORT_MESSAGE_SIZE + 1)

    try:
        initiator.write_message(with_oversized_handshake_payload)
    except NoiseError as exc:
        assert "handshake" in str(exc)
    else:
        raise AssertionError("expected handshake payload limit failure")

    responder.read_message(initiator.write_message())
    initiator.read_message(responder.write_message())
    responder.read_message(initiator.write_message())
    initiator_send, _ = initiator.transport_ciphers(use_extracted_nonce=True)

    try:
        initiator_send.encrypt(with_oversized_transport_payload)
    except NoiseError as exc:
        assert "transport" in str(exc)
    else:
        raise AssertionError("expected transport payload limit failure")
