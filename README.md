# bitchat-cli

`bitchat-cli` is a clean-room Python CLI for BitChat public chat over Bluetooth LE.

Current scope:

- Real BitChat mainnet BLE UUIDs
- Official announce packet format
- Official packet signing and peer ID derivation
- Public broadcast chat with persistent local identity
- Central-role BLE transport using `bleak`
- Inbound fragment reassembly and outbound packet fragmentation for oversized BLE payloads
- Direct BLE private messaging over Noise XX with lazy handshake
- Official Noise XX session primitives validated against the macOS app's test vectors
- Duplicate-safe multi-hop relay for public traffic and directed Noise packets
- Private-message history capture plus `dm inbox` / `dm history`
- Offline protocol self-tests

Still not implemented:

- BLE peripheral advertising
- File transfer
- Nostr geohash chat

## Quick start

```bash
uv sync
uv run bitchat doctor
uv run bitchat chat --nickname breno
```

Inside chat:

- `/help`
- `/name <nickname>`
- `/who`
- `/msg <@nickname|peer_id> <message>`
- `/clear`
- `/quit`

Any non-slash input is sent as a public BitChat message.

## Background listener

Run the receiver in the background so inbound messages are recorded even when no
interactive chat is open:

```bash
uv run bitchat daemon start --nickname breno
uv run bitchat daemon status
uv run bitchat peers
uv run bitchat inbox --tail 20
uv run bitchat dm send @bubbles "hello from daemon"
uv run bitchat dm inbox
uv run bitchat dm history @bubbles
uv run bitchat daemon stop
```

Daemon artifacts live under `~/.config/bitchat-cli/`:

- `daemon.log` - background listener log
- `daemon.sock` - local control socket for `dm send` and read-receipt commands
- `messages.jsonl` - recorded message history
- `peers.json` - known peers learned from signed announce packets, keeping the
  latest observed address set per peer instead of a growing historical archive
- `daemon.pid` - running daemon pid
- `radio.pid` - active BLE session guard

Behavior notes:

- The daemon now follows the macOS app's basic scanning shape: duty-cycled
  background scanning instead of a tight `discover()` loop.
- `inbox` deduplicates repeated deliveries of the same signed BitChat packet.
- The transport relays signed public packets, oversized packet fragments, and
  directed Noise handshakes/encrypted payloads across connected peers with TTL
  decrement and loop suppression.
- Direct DM traffic uses one active BLE link per peer instead of writing the
  same packet to every historical address we have seen for that peer.
- The daemon now processes inbound BLE notifications through a bounded worker
  queue instead of spawning an unbounded task per packet.
- Long-lived daemon state is pruned periodically so stale reconnect timers,
  dead peer links, and orphaned DM session state do not accumulate forever.
- Stale public broadcasts / announces and obviously future-skewed packets are
  dropped early instead of consuming relay or session work.
- Private messages are persisted separately from the public inbox and exposed
  through `bitchat dm inbox` and `bitchat dm history`.
- Only one CLI BLE session can own the radio at a time, so `chat` and `daemon`
  no longer run concurrently.

## Notes

This implementation is intentionally narrow. It aims to interoperate with the official apps for signed public chat without claiming full protocol coverage.
