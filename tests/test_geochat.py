from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from bitchat_cli import geochat
from websockets.asyncio.server import serve


def test_normalize_geohash_accepts_hash_prefix_and_case() -> None:
    assert geochat.normalize_geohash("#U4PrUy") == "u4pruy"


def test_normalize_geohash_rejects_invalid_characters() -> None:
    try:
        geochat.normalize_geohash("bad!")
    except ValueError as exc:
        assert "invalid geohash" in str(exc)
    else:
        raise AssertionError("expected normalize_geohash to fail")


def test_derive_geohash_identity_is_deterministic() -> None:
    seed = bytes.fromhex("11" * 32)

    first = geochat.derive_nostr_identity(seed, "u4pruy")
    second = geochat.derive_nostr_identity(seed, "u4pruy")
    other = geochat.derive_nostr_identity(seed, "u4pruv")

    assert first.public_key_hex == second.public_key_hex
    assert first.public_key_hex != other.public_key_hex


def test_create_ephemeral_geohash_event_round_trips_signature() -> None:
    identity = geochat.derive_nostr_identity(bytes.fromhex("22" * 32), "u4pruy")

    event = geochat.create_ephemeral_geohash_event(
        content="hello geo",
        geohash="u4pruy",
        identity=identity,
        nickname="bubbles",
    )

    assert event["kind"] == 20000
    assert ["g", "u4pruy"] in event["tags"]
    assert ["n", "bubbles"] in event["tags"]
    assert geochat.verify_event_signature(event) is True


def test_create_ephemeral_geohash_event_can_mine_pow(monkeypatch) -> None:
    identity = geochat.derive_nostr_identity(bytes.fromhex("77" * 32), "u4pruy")
    monkeypatch.setattr(geochat.secrets, "randbelow", lambda _limit: 0)

    event = geochat.create_ephemeral_geohash_event(
        content="hello pow",
        geohash="u4pruy",
        identity=identity,
        nickname="bubbles",
        created_at=1_719_188_400,
        pow_difficulty=8,
    )

    nonce_tags = [tag for tag in event["tags"] if tag[0] == "nonce"]
    assert len(nonce_tags) == 1
    assert nonce_tags[0][2] == "8"
    assert geochat.event_pow_difficulty(str(event["id"])) >= 8
    assert geochat.verify_event_signature(event) is True


def test_identity_derivation_matches_upstream_hmac_shape() -> None:
    seed = bytes.fromhex("33" * 32)
    geohash = "u4pruy"
    expected_candidate = hmac.new(
        seed,
        geohash.encode("utf-8") + (0).to_bytes(4, "big"),
        hashlib.sha256,
    ).digest()

    assert geochat._candidate_geohash_private_key(seed, geohash, 0) == expected_candidate


def test_geochat_daemon_loads_relays_lazily() -> None:
    async def exercise() -> None:
        loads: list[Path] = []
        original_loader = geochat.load_relay_entries

        def fake_loader(path: Path) -> list[geochat.GeoRelayEntry]:
            loads.append(path)
            return [geochat.GeoRelayEntry(host="relay.example.com", lat=57.64911, lon=10.40744)]

        daemon = geochat.GeoChatDaemon(
            relay_cache_path=Path("/tmp/georelays.csv"),
            seed_hex="66" * 32,
            nickname="local",
            on_message=lambda _event: None,
        )
        geochat.load_relay_entries = fake_loader  # type: ignore[assignment]
        try:
            await daemon.start([])
            assert loads == []

            await daemon.set_watches(["u4pruy"])
            assert loads == [Path("/tmp/georelays.csv")]
            await daemon.stop()
        finally:
            geochat.load_relay_entries = original_loader  # type: ignore[assignment]

    asyncio.run(exercise())


def test_geochat_daemon_subscribes_and_publishes_against_local_relay() -> None:
    async def exercise() -> None:
        subscribed = asyncio.Event()
        published: list[dict[str, object]] = []
        received: list[dict[str, object]] = []
        incoming_identity = geochat.derive_nostr_identity(bytes.fromhex("44" * 32), "u4pruy")

        async def handler(websocket) -> None:
            raw = await websocket.recv()
            message = json.loads(raw)
            if message[0] == "REQ":
                subscribed.set()
                event = geochat.create_ephemeral_geohash_event(
                    content="from relay",
                    geohash="u4pruy",
                    identity=incoming_identity,
                    nickname="relay-user",
                )
                await websocket.send(json.dumps(["EVENT", message[1], event]))
                await asyncio.sleep(0.1)
                return
            if message[0] == "EVENT":
                published.append(message[1])

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with TemporaryDirectory() as tmpdir:
                original_closest_relays = geochat.closest_relays
                daemon = geochat.GeoChatDaemon(
                    relay_cache_path=Path(tmpdir) / "georelays.csv",
                    seed_hex="55" * 32,
                    nickname="local",
                    on_message=received.append,
                )
                daemon._relay_entries = [geochat.GeoRelayEntry(host=f"127.0.0.1:{port}", lat=57.64911, lon=10.40744)]
                geochat.closest_relays = lambda _entries, _geohash, count=5: [f"ws://127.0.0.1:{port}"]  # type: ignore[assignment]
                try:
                    await daemon.set_watches(["u4pruy"])
                    await asyncio.wait_for(subscribed.wait(), timeout=2)
                    await asyncio.sleep(0.1)
                    assert received and received[0]["content"] == "from relay"

                    await daemon.publish("u4pruy", "outbound hello")
                    await asyncio.sleep(0.1)
                    await daemon.stop()
                finally:
                    geochat.closest_relays = original_closest_relays  # type: ignore[assignment]

        assert published and published[0]["content"] == "outbound hello"

    asyncio.run(exercise())


def test_geochat_daemon_deduplicates_same_event_from_multiple_relays() -> None:
    daemon = geochat.GeoChatDaemon(
        relay_cache_path=Path("/tmp/georelays.csv"),
        seed_hex="88" * 32,
        nickname="local",
        on_message=lambda event: received.append(event),
    )
    received: list[dict[str, object]] = []
    incoming_identity = geochat.derive_nostr_identity(bytes.fromhex("99" * 32), "u4pruy")
    event = geochat.create_ephemeral_geohash_event(
        content="from relay",
        geohash="u4pruy",
        identity=incoming_identity,
        nickname="relay-user",
    )
    payload = json.dumps(["EVENT", "geo-u4pruy", event])

    daemon._handle_relay_message("u4pruy", payload)
    daemon._handle_relay_message("u4pruy", payload)

    assert [item["id"] for item in received] == [event["id"]]
