from bitchat_cli.crypto import Identity
from bitchat_cli.dedupe import ExpiringSet, packet_cache_key
from bitchat_cli.protocol import Packet, make_public_message_packet


def test_packet_cache_key_is_stable_across_ttl_mutations() -> None:
    identity = Identity.generate()
    original = make_public_message_packet(identity, "hello mesh", timestamp_ms=1_700_000_000_000)
    relayed = Packet(
        packet_type=original.packet_type,
        sender_id=original.sender_id,
        recipient_id=original.recipient_id,
        timestamp_ms=original.timestamp_ms,
        payload=original.payload,
        signature=original.signature,
        ttl=1,
        version=original.version,
    )

    assert packet_cache_key(original) == packet_cache_key(relayed)


def test_expiring_set_reports_duplicates_until_entry_expires() -> None:
    now = [100.0]
    cache = ExpiringSet(ttl_seconds=10.0, max_entries=8, clock=lambda: now[0])

    assert cache.mark_seen("msg-1") is False
    assert cache.mark_seen("msg-1") is True

    now[0] += 11.0

    assert cache.mark_seen("msg-1") is False
