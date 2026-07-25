# bitchat CLI

`bitchat` is a Python command-line client for the BitChat protocol. It combines
local Bluetooth LE mesh chat with location-scoped Nostr channels, while keeping
the two transports explicit in the command surface:

This project is a Python CLI port based on the public behavior and reference
implementations from Permissionless Tech's [BitChat for iOS/macOS](https://github.com/permissionlesstech/bitchat)
and [BitChat for Android](https://github.com/permissionlesstech/bitchat-android).
Those projects remain the primary references for platform interoperability.

- `#mesh` uses Bluetooth LE. It works without the internet, relays signed public
  packets through nearby peers, and supports Noise-encrypted DMs, receipts,
  files, and public sync after reconnect.
- Geohash channels use Nostr relays. They provide internet-backed location
  channels, coarse presence, optional PoW, teleport markers, location notes, and
  geohash-scoped DMs.

Private text DMs prefer a live BLE link. A linked and favorited peer may also
receive text DMs and receipts through the Nostr fallback when no live BLE route
exists. File fallback over Nostr is not implemented.

> **Work in progress:** this client has not received an independent security
> review. Do not use it for sensitive communications until its cryptography,
> protocol interoperability, and operational behavior have been reviewed for
> your use case.

## Install and first run

Requirements:

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- A Bluetooth LE adapter for mesh features
- macOS for the optional BLE peripheral endpoint used by iOS/Android clients

From a checkout:

```bash
uv sync
uv run bitchat doctor
uv run bitchat selftest
```

To install the checkout as a user tool:

```bash
uv tool install .
bitchat doctor
```

`doctor --json` is the fastest way to see the generated identity, state paths,
daemon status, proxy status, and which capabilities are available on the current
machine. The central BLE transport uses `bleak`; the macOS peripheral endpoint
is enabled only when CoreBluetooth, Foundation, and the PyObjC bridge are
available.

## Common workflows

Start the background listener once, then use the short-lived commands below:

```bash
uv run bitchat daemon start --nickname breno
uv run bitchat daemon status

# Public Bluetooth mesh chat
uv run bitchat mesh send "hello from #mesh"
uv run bitchat mesh inbox --tail 20
uv run bitchat peers

# Private text and file transfer
uv run bitchat dm send @bubbles "hello privately"
uv run bitchat dm history @bubbles
uv run bitchat dm send-file @bubbles ./photo.jpg

# Location channels: derive a geohash offline, then opt into a watch
uv run bitchat geo encode --lat 38.7223 --lon -9.1393 --all-levels
uv run bitchat geo watch add eycs2
uv run bitchat geo send eycs2 "hello from the city channel"
uv run bitchat geo inbox eycs2 --tail 20
uv run bitchat geo who eycs2

uv run bitchat daemon stop
```

The full command tree and flags are in [`docs/cli-reference.md`](docs/cli-reference.md).
Every visible command supports `--help` at its level, for example:

```bash
bitchat --help
bitchat geo --help
bitchat geo send --help
```

Read/status commands that expose `--json` are intended for scripts. State-changing
commands print a short confirmation and return a non-zero status when validation
or delivery setup fails.

## Transport and privacy model

### Bluetooth mesh

The `mesh` commands address the public `#mesh` conversation. The daemon acts as
a BLE central and, on supported macOS installations, also advertises the
BitChat peripheral service so a phone can connect to it. Public packets are
signed, relayed with a bounded TTL, deduplicated, and fragmented when they do
not fit in one BLE write. A reconnecting peer can request cached public packets
and public files; private packets remain live-only because they are inside the
Noise session.

Only one CLI BLE session can own the radio at a time. Stop `chat` before starting
`daemon`, or stop `daemon` before opening `chat`.

### Interactive chat

`bitchat chat --nickname NAME` opens the public `#mesh` client. The interactive
commands are:

```text
/help
/name <nickname>
/who
/msg <@nickname|peer_id> <message>
/clear
/quit
```

Non-slash input is sent as a public `#mesh` message. The interactive client and
the background daemon share the same persistent identity and state directory.

### Nostr geochat

Geohash operations other than `geo lookup` are driven by the daemon and Nostr
relays. The CLI derives the same hierarchy used by the official apps:

| Level | Precision | Typical scope |
| --- | ---: | --- |
| `region` | 2 | country / large region |
| `province` | 4 | state / province |
| `city` | 5 | city |
| `neighborhood` | 6 | neighborhood |
| `block` | 7 | city block |
| `building` | 8 | building-level area |

Use `geo levels`, `geo encode`, and `geo decode` for offline discovery. A
`geo watch add` subscription keeps the daemon listening for the channel and,
for precision 5 or less, emits coarse presence heartbeats. `geo send` publishes
kind `20000` events; `--pow N` mines the matching NIP-13 nonce tag and
`--teleport` adds the official teleport tag.

A geohash is location information. The CLI warns at neighborhood precision and
requires `--force` for block/building publication or background watching. The
`geo lookup` command uses OpenStreetMap Nominatim over the configured network
policy; it does not persist the query or returned coordinates.

### Proxy policy

Proxy configuration is shared by georelays, Nostr DMs, location notes, and place
lookup:

```bash
bitchat network proxy status --json
bitchat network proxy set system --policy prefer
bitchat network proxy set tor --policy require
bitchat network proxy set socks5h://127.0.0.1:9050 --policy require
bitchat network proxy test --json
bitchat network proxy disable
```

`prefer` allows a direct route when the selected proxy is unavailable. `require`
fails closed when the proxy mode is disabled or cannot be used. `tor` checks the
common local Tor ports (`39050`, `9050`, and `9150`) and stores the first live
one; a custom URL can be supplied directly. Proxy targets shown by status are
redacted to avoid printing credentials.

### Social trust and Nostr DM fallback

Nostr fallback is opt-in per peer. Link the peer to its Nostr identity, then
favorite it:

```bash
bitchat social link @bubbles <npub>
bitchat social favorite @bubbles
bitchat dm send @bubbles "BLE-independent text"
```

Blocking or unfavoriting disables fallback. `social fingerprint` prints the
peer's Noise/signing material and a verification payload; use `social verify
PEER --payload 'bitchat://verify?...'` when checking an out-of-band payload.
Without `--payload`, `social verify PEER` records a local verified state but does
not prove that a human checked the identity.

## Local state and artifacts

The CLI stores persistent state under `~/.config/bitchat/` by default. For
backward compatibility, an existing `~/.config/bitchat-cli/` directory is reused
when it contains prior BitChat artifacts and the preferred directory does not.
There is currently no environment-variable override for this path; use
`bitchat doctor --json` to see the active path.

Typical artifacts are:

| Path | Purpose |
| --- | --- |
| `state.json` | local identity, Nostr keys/relays, proxy policy, watches, geohash metadata, and topic metadata |
| `daemon.log` | background listener and transport diagnostics |
| `daemon.sock` | local control socket used by short-lived send/query commands |
| `daemon.pid` / `radio.pid` | daemon and BLE-session leases |
| `messages.jsonl` | deduplicated public, geohash, DM, receipt, and attachment history |
| `peers.json` | signed announce records and the latest observed addresses |
| `social.json` | explicit peer/Nostr links, favorites, blocks, and verification state |
| `georelays_cache.csv` | cached geohash relay directory |
| `files/incoming/` | received non-image files |
| `images/incoming/` | received image files |

The `topic` commands update only local Android-style channel metadata. They do
not create an additional public transport or join a remote topic on the mesh.

## Scope and known limitations

- The client targets the public `#mesh` protocol plus geohash/Nostr features; it
  does not claim full feature parity with either official app.
- Nostr fallback currently covers text DMs and delivery/read receipts, not file
  transfers.
- Cross-platform BLE interoperability is the best-tested private path. Nostr
  fallback additionally depends on the other client having the matching Nostr
  identity and DM subscription enabled; the Android 1.7.4 manual run did not
  surface a fallback DM even after mutual favoriting.
- Public sync covers announces, public messages, fragments, and public file
  transfers. Private messages and private files are not replayed by public sync.
- Location notes require an 8-character building geohash and publish kind `1`
  events to public relays. Treat both the geohash and note text as public data.
- `geo lookup` is the only command that sends a free-form place query to a
  third-party HTTP service. Use `network proxy ... --policy require` when the
  privacy boundary requires fail-closed behavior.

## Development and verification

```bash
uv sync
uv run pytest
uv run bitchat selftest
uv run bitchat --help
```

## References

This client follows the public protocol behavior of the official projects:

- [BitChat for iOS/macOS](https://github.com/permissionlesstech/bitchat)
- [BitChat for Android](https://github.com/permissionlesstech/bitchat-android)
- [BitChat georelay directory](https://github.com/permissionlesstech/georelays)

The implementation is intentionally narrow and aims to make its limits visible:
read the source and run `doctor`, `selftest`, and the relevant `--help` output
before relying on a feature.
