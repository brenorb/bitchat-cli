# bitchat-cli

`bitchat-cli` is a clean-room Python CLI for BitChat public chat over Bluetooth LE.

Current scope:

- Real BitChat mainnet BLE UUIDs
- Official announce packet format
- Official packet signing and peer ID derivation
- Public broadcast chat with persistent local identity
- Central-role BLE transport using `bleak`
- Inbound fragment reassembly and outbound packet fragmentation for oversized BLE payloads
- Official private-message TLV framing and Noise payload helpers for future DM transport
- Offline protocol self-tests

Not implemented in this first cut:

- Noise private messaging
- Noise private-message transport and CLI commands
- Multi-hop relay participation
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
uv run bitchat daemon stop
```

Daemon artifacts live under `~/.config/bitchat-cli/`:

- `daemon.log` - background listener log
- `messages.jsonl` - recorded message history
- `peers.json` - known peers learned from signed announce packets
- `daemon.pid` - running daemon pid
- `radio.pid` - active BLE session guard

Behavior notes:

- The daemon now follows the macOS app's basic scanning shape: duty-cycled
  background scanning instead of a tight `discover()` loop.
- `inbox` deduplicates repeated deliveries of the same signed BitChat packet.
- Only one CLI BLE session can own the radio at a time, so `chat` and `daemon`
  no longer run concurrently.

## Notes

This implementation is intentionally narrow. It aims to interoperate with the official apps for signed public chat without claiming full protocol coverage.
