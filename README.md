# bitchat

`bitchat` is a clean-room Python CLI for BitChat public chat over Bluetooth LE.

Current scope:

- `#mesh` as the CLI's explicit public conversation surface
- Location geohash channels over Nostr, with deterministic per-geohash identities
- Real BitChat mainnet BLE UUIDs
- Official announce packet format
- Announce packets include current direct-neighbor hints from live connected peers
- Official packet signing and peer ID derivation
- Public broadcast chat with persistent local identity
- Central-role BLE transport using `bleak`
- macOS BLE peripheral advertising and subscriber writes for app-to-CLI reachability
- Inbound fragment reassembly and outbound packet fragmentation for oversized BLE payloads
- Direct BLE private messaging over Noise XX with lazy handshake
- Public and DM file/image transfer with inbound persistence under `~/.config/bitchat/{files,images}/incoming`
- Official Noise XX session primitives validated against the macOS app's test vectors
- Duplicate-safe multi-hop relay for public traffic and directed Noise packets
- Local public-message, fragment, and public file-transfer sync over `REQUEST_SYNC` with official-style `RSR` responses
- Private-message history capture plus `dm inbox` / `dm history`
- Linked-peer Nostr DM fallback for offline text/receipt delivery
- Geohash-scoped Nostr DMs for watched or selected geohash identities
- Persistent location notes over Nostr for building-level geohashes
- Local geohash channel metadata for names, notes, bookmarks, selected context, and teleport hints
- CLI-native social trust commands for linking, favoriting, blocking, fingerprint inspection, and verified state
- Offline DM queueing for known peers until the BLE route comes back
- Local Android-style named topic channel metadata for password / retention tracking
- Proxy-aware network policy for geohash relay traffic, lookup, and Nostr fallback
- Offline geohash discovery helpers for region/province/city/neighborhood/block/building derivation
- Offline protocol self-tests

Still not implemented:

- Nostr fallback for files

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
uv run bitchat network proxy set tor --policy require
uv run bitchat network proxy status
uv run bitchat send "hello from daemon"
uv run bitchat mesh send "hello #mesh"
uv run bitchat geo watch add u4pruy
uv run bitchat geo send u4pruy "hello #u4pruy"
uv run bitchat geo levels u4pruydqq
uv run bitchat geo encode --lat 37.7749 --lon -122.4194 --all-levels
uv run bitchat geo decode u4pruyd
uv run bitchat geo lookup "Union Square, San Francisco"
uv run bitchat geo send u4pruy --pow 12 "hello with PoW"
uv run bitchat geo send u4pruy --teleport "hello from a teleported channel"
uv run bitchat geo inbox u4pruy --tail 20
uv run bitchat geo who u4pruy
uv run bitchat geo channel set u4pruy --name "Home" --bookmark --select
uv run bitchat geo channel show u4pruy
uv run bitchat geo notes add u4pruydq "quiet after 9pm"
uv run bitchat geo notes list u4pruydq
uv run bitchat geo participant show u4pruy <pubkey>
uv run bitchat geo participant block u4pruy <pubkey>
uv run bitchat geo dm send u4pruy <pubkey> "hello from geo dm"
uv run bitchat geo dm history u4pruy <pubkey>
uv run bitchat peers
uv run bitchat social link @bubbles <npub>
uv run bitchat social favorite @bubbles
uv run bitchat social fingerprint @bubbles
uv run bitchat social verify @bubbles
uv run bitchat inbox --tail 20
uv run bitchat mesh inbox --tail 20
uv run bitchat dm send @bubbles "hello from daemon"
uv run bitchat topic join btc --password secret --retain
uv run bitchat topic list
uv run bitchat send-file ./photo.jpg
uv run bitchat mesh send-file ./photo.jpg
uv run bitchat dm send-file @bubbles ./photo.jpg
uv run bitchat dm inbox
uv run bitchat dm history @bubbles
uv run bitchat daemon stop
```

Daemon artifacts live under `~/.config/bitchat/`:

- `daemon.log` - background listener log
- `daemon.sock` - local control socket for `dm send` and read-receipt commands
- `messages.jsonl` - recorded message history
- `peers.json` - known peers learned from signed announce packets, keeping the
  latest observed address set per peer instead of a growing historical archive
- `social.json` - explicit Nostr/social mappings and trust flags
- `files/incoming` - inbound non-image transfers
- `images/incoming` - inbound image transfers
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
- `geo watch add/remove/list` manages the geohash channels the daemon keeps
  subscribed in the background, so location-channel messages can arrive while
  no interactive client is open.
- `geo channel set/show/list` stores local channel metadata the mobile sheets
  expose visually: friendly names, bookmarks, private notes, one selected
  context, and a saved teleport hint.
- `geo notes add/list` publishes and fetches persistent Nostr kind `1` location
  notes for building-level geohashes (8 characters), matching the app's
  location-note concept in a CLI command surface.
- `geo participant show/block/unblock` overlays trust state onto Nostr geohash
  participants, and `geo who` now annotates favorite / blocked / verified flags.
- `geo dm send/history` uses per-geohash Nostr identities and no-recipient
  embedded BitChat payloads, so the CLI can interoperate with the mobile apps'
  geohash-scoped direct messages.
- `network proxy ...` sets one privacy policy for every remote path in the CLI
  instead of sprinkling ad hoc proxy flags across commands. `--policy require`
  is the fail-closed mode when you care more about privacy than convenience.
- Watched geohashes with precision `<= 5` now emit periodic kind `20001`
  presence heartbeats in the background, matching the official app's
  coarse-location presence model.
- `geo levels`, `geo encode`, and `geo decode` are fully offline; `geo lookup`
  is the only place-name flow that may touch a third-party service, and it
  does not persist queries or returned coordinates.
- Upstream-compatible geohash naming in the CLI now maps:
  region=`2`, province=`4`, city=`5`, neighborhood=`6`, block=`7`,
  building=`8`.
- `geo send` and `geo watch add` now warn before publishing or following
  high-precision geohashes, and block/building precision requires `--force`.
- `geo send <geohash> ...` publishes a real kind `20000` geohash-scoped Nostr
  event, signed with the same deterministic HMAC-derived per-geohash identity
  shape the macOS app uses.
- `geo send <geohash> --pow <bits> ...` also mines the same NIP-13-style
  `["nonce", value, difficulty]` tag the Android app expects when geohash PoW
  is enabled. The CLI defaults to `--pow 0`, matching the official app's
  default of leaving PoW disabled.
- `geo send <geohash> --teleport ...` adds the same `["t","teleport"]` tag the
  official apps use for manually teleported geohash messages.
- The daemon caches the official `permissionlesstech/georelays` CSV locally and
  picks the closest relays to the target geohash center, matching the app's
  geohash relay selection model at a smaller implementation scope.
- Geohash inbound events are deduplicated by Nostr event ID before persistence,
  so the same relay fanout no longer appears repeated in `geo inbox`.
- `inbox` is the `#mesh` view for the BLE public conversation, and `mesh send`
  / `mesh inbox` expose that same scope explicitly in the CLI.
- `geo inbox <geohash>` is the inbound view for one location channel and now
  shows PoW / teleport markers when the event carries them.
- `geo who <geohash>` lists recently seen participants from geohash messages and
  presence heartbeats, excluding the CLI's own derived geohash identity.
- The macOS peripheral endpoint now exposes the same BLE characteristic surface
  the official app does for inbound writes: notify, read, write, and
  write-without-response.
- Inbound peripheral writes are now reassembled across offset-based CoreBluetooth
  write requests before packet decode, which matches the official app's long
  write behavior without growing an unbounded buffer.
- `send` queues a public channel message through the running daemon, so you can
  test outbound delivery without stopping the background listener to open
  interactive `chat`.
- `send-file`, `mesh send-file`, and `dm send-file` use the official file TLV
  shape and persist inbound transfers with attachment metadata so `inbox`,
  `dm inbox`, and `dm history` can render them as files instead of raw paths.
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
- Newly seen direct peers get a local-only sync request covering announces,
  public messages, oversized-packet fragments, and public file transfers, and
  the CLI can answer `REQUEST_SYNC` packets from those caches while marking
  solicited replay packets with the official `RSR` wire flag.
- This matches the Apple app's current scope for replayed public file
  transfers; private file transfers still remain live-only because they travel
  inside Noise-encrypted DM packets instead of the public gossip sync cache.
- The CLI validates `RSR` catch-up packets against the peer that answered the
  sync request, including replayed public messages originally authored by this
  device.
- The sync cache includes the CLI's own current announce, and reconnecting
  direct peers get a fresh public sync request.
- Private messages are persisted separately from the public inbox and exposed
  through `bitchat dm inbox` and `bitchat dm history`.
- `social link` + `social favorite` authorize Nostr DM fallback for one peer.
  BLE stays first; if the daemon has no live BitChat link for that peer, text
  DMs and read/delivery receipts can fall back to NIP-17 gift-wrapped Nostr
  messages using the linked `npub`.
- `social verify` marks a peer verified locally, and `social fingerprint` now
  prints a signed verification payload you can paste into another CLI for an
  out-of-band check before trusting that identity.
- `dm send` now queues text DMs for known-but-offline BLE peers instead of
  failing immediately, reusing the transport's pending Noise payload queue
  until the peer reconnects.
- `topic join/list/leave/password/retain` exposes the Android-only named
  channel metadata as a local CLI surface without expanding the public mesh
  transport model beyond `#mesh` plus geohashes.
- Only one CLI BLE session can own the radio at a time, so `chat` and `daemon`
  no longer run concurrently.

## Notes

This implementation is intentionally narrow. It aims to interoperate with the official apps for signed public chat across Bluetooth mesh and geohash/Nostr channels without claiming full protocol coverage.
