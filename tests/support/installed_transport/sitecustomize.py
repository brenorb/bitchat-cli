"""Explicit E2E-only radio/relay boundary; production CLI and socket stay real.

Loaded only by subprocesses whose PYTHONPATH points at this directory. Never
sends radio or public relay traffic. Installed wheels need no testing switches.
"""
import os

if os.environ.get('BITCHAT_E2E_CONTROLLED') == '1':
    import json
    from datetime import datetime, timezone
    from pathlib import Path
    from bitchat_cli import app
    from bitchat_cli.crypto import Identity
    from bitchat_cli.protocol import Packet, make_public_message_packet
    from bitchat_cli.transport import BLEChatTransport

    class LocalTransport(BLEChatTransport):
        async def start(self):
            if os.environ.get('BITCHAT_E2E_FAIL_START') == '1':
                raise RuntimeError('controlled BLE startup failure')
            peer = Identity.generate()
            self.seed_known_peers([{
                'peer_id': peer.peer_id, 'nickname': 'controlled-local-peer',
                'noise_public_key': peer.noise_public_key.hex(),
                'signing_public_key': peer.signing_public_key.hex(),
                'addresses': [], 'last_seen': datetime.now(timezone.utc).isoformat(),
            }])
            self.peer_log(self.known_peers[peer.peer_id])
            packet = make_public_message_packet(peer, 'local fixture reply')
            await self._handle_message(Packet.decode(packet.encode(padding=False)))

        async def stop(self):
            return None

        async def _broadcast_packet(self, packet, **_kwargs):
            decoded = Packet.decode(packet.encode(padding=False))
            assert decoded.verify_signature(self.identity.signing_public_key)
            path = self.file_runtime_paths.root / 'controlled-wire.jsonl'
            with path.open('a') as output:
                output.write(json.dumps({'type': int(decoded.packet_type), 'payload_hex': decoded.payload.hex(), 'signature_valid': True}) + '\n')

    class LocalGeo:
        def __init__(self, **_kwargs): pass
        async def start(self, _geohashes): pass
        async def stop(self): pass
        def runtime_status(self): return {'watched_geohashes': [], 'relay_subscriptions': 0}

    class LocalNostr:
        def __init__(self, **_kwargs): self.relay_urls = []
        async def start(self): pass
        async def stop(self): pass
        async def set_extra_watch_identities(self, _identities): pass

    app._build_transport = lambda **kwargs: LocalTransport(**kwargs)
    app.GeoChatDaemon = LocalGeo
    app.NostrDMClient = LocalNostr
