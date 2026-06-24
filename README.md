# bitchat-cli

`bitchat-cli` is a clean-room Python CLI for BitChat public chat over Bluetooth LE.

Current scope:

- `#mesh` as the CLI's explicit public conversation surface
- Real BitChat mainnet BLE UUIDs
- Official announce packet format
- Announce packets include current direct-neighbor hints from live connected peers
- Official packet signing and peer ID derivation
- Public broadcast chat with persistent local identity
- Central-role BLE transport using `bleak`
- macOS BLE peripheral advertising and subscriber writes for app-to-CLI reachability
- Inbound fragment reassembly and outbound packet fragmentation for oversized BLE payloads
- Direct BLE private messaging over Noise XX with lazy handshake
- Official Noise XX session primitives validated against the macOS app's test vectors
- Duplicate-safe multi-hop relay for public traffic and directed Noise packets
- Local public-message and fragment sync over `REQUEST_SYNC` with official-style `RSR` responses
- Private-message history capture plus `dm inbox` / `dm history`
- Offline protocol self-tests

Still not implemented:

- Private channels beyond the mesh public conversation
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
uv run bitchat send "hello from daemon"
uv run bitchat mesh send "hello #mesh"
uv run bitchat peers
uv run bitchat inbox --tail 20
uv run bitchat mesh inbox --tail 20
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
  background scanning instead of a tight `discover()` loop, with longer idle
  and connected off-windows to reduce background Bluetooth churn.
- When the daemon has no live peers yet, it now keeps scanning continuously for
  a short discovery grace window before falling back to longer off-windows, and
  active peripheral subscribers count as live peers for that backoff policy.
- `messages.jsonl` now suppresses repeated writes for the same stable
  `message_id`, so sync replay after reconnect or restart does not re-persist
  already recorded public packets, and `inbox` stays deduplicated too.
- `daemon stop` now waits briefly for a clean shutdown and lease cleanup, so an
  immediate `daemon start` is less likely to trip over stale `daemon.pid` or
  `radio.pid` state.
- `daemon status` now asks the live daemon for runtime details when the control
  socket is available, including whether peripheral advertising is active, how
  many peripheral subscribers are attached, and which subscriber BLE addresses
  are currently attached. It also shows a short bounded history of recent
  peripheral subscribe/unsubscribe/write events to make phone-to-daemon testing
  debuggable without catching the exact moment live.
- `inbox` is the `#mesh` view for the BLE public conversation, and `mesh send`
  / `mesh inbox` expose that same scope explicitly in the CLI.
- The macOS peripheral endpoint now exposes the same BLE characteristic surface
  the official app does for inbound writes: notify, read, write, and
  write-without-response.
- Inbound peripheral writes are now reassembled across offset-based CoreBluetooth
  write requests before packet decode, which matches the official app's long
  write behavior without growing an unbounded buffer.
- `send` queues a public channel message through the running daemon, so you can
  test outbound delivery without stopping the background listener to open
  interactive `chat`.
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
- BLE discovery now feeds a bounded connect queue instead of spawning an
  unbounded connect task per discovered device.
- Failed or short-lived BLE links now back off exponentially instead of
  retrying at a near-constant background cadence.
- Newly seen direct peers get a local-only public sync request, and the CLI can
  answer `REQUEST_SYNC` packets with cached announces, public messages, and
  oversized-packet fragments, marking solicited replay packets with the
  official `RSR` wire flag.
- Unsupported sync types such as file transfer are filtered out locally until
  the CLI grows the matching feature surface.
- The CLI validates `RSR` catch-up packets against the peer that answered the
  sync request, including replayed public messages originally authored by this
  device.
- The sync cache includes the CLI's own current announce, and reconnecting
  direct peers get a fresh public sync request.
- Private messages are persisted separately from the public inbox and exposed
  through `bitchat dm inbox` and `bitchat dm history`.
- Only one CLI BLE session can own the radio at a time, so `chat` and `daemon`
  no longer run concurrently.

## Notes

This implementation is intentionally narrow. It aims to interoperate with the official apps for signed public chat without claiming full protocol coverage.
