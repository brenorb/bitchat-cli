# CLI reference

This reference mirrors the command tree in `src/bitchat_cli/cli.py`. The
authoritative runtime help is always available from the installed command:

```bash
bitchat --help
bitchat <command> --help
bitchat <command> <subcommand> --help
```

`--json` is a command-level option, not a global option. It is available only on
commands whose parser lists it below.

## Top-level commands

| Command | Purpose |
| --- | --- |
| `doctor` | Show identity, state paths, daemon status, network status, and capability scope. Add `--json` for structured output. |
| `chat` | Run the interactive public `#mesh` client. Options: `--nickname`, `--scan-interval SECONDS`. |
| `daemon start` | Start the background BLE/Nostr listener. Options: `--nickname`, `--scan-interval SECONDS`. |
| `daemon stop` | Stop the background listener and clean its leases. |
| `daemon status` | Show the listener, peer, peripheral, watch, relay, and proxy status. |
| `inbox` | Show recorded public inbound messages. Options: `--tail N`, `--json`. |
| `mesh` | Address the public `#mesh` conversation. See below. |
| `send MESSAGE` | Alias for `mesh send`. |
| `send-file PATH` | Alias for `mesh send-file`. |
| `geo` | Address geohash/Nostr channels. See below. |
| `peers` | Show known signed BLE peers. Option: `--json`. |
| `social` | Manage peer/Nostr links and trust state. See below. |
| `network proxy` | Configure and test outbound proxy behavior. See below. |
| `dm` | Inspect or send direct messages and files. See below. |
| `topic` | Manage local named-topic metadata. See below. |
| `selftest` | Run offline protocol checks. |

All commands accept `-h`/`--help` at their own level. `daemon-run` is an
internal child-process entry point and is intentionally hidden from help.

## Mesh commands

```text
bitchat mesh inbox [--json] [--tail N]
bitchat mesh send MESSAGE
bitchat mesh send-file PATH
```

`mesh inbox` is the explicit `#mesh` view. The top-level `inbox`, `send`, and
`send-file` commands are aliases for the same scope.

## Geohash commands

```text
bitchat geo send GEOHASH [--pow BITS] [--teleport] [--force] MESSAGE
bitchat geo inbox GEOHASH [--json] [--tail N]
bitchat geo who GEOHASH [--json]
bitchat geo levels GEOHASH [--json]
bitchat geo encode --lat LAT --lon LON [--level LEVEL | --all-levels] [--json]
bitchat geo decode GEOHASH [--json]
bitchat geo lookup QUERY [--limit N] [--json]
```

`LEVEL` is one of `region`, `province`, `city`, `neighborhood`, `block`, or
`building`. `geo encode`, `geo levels`, and `geo decode` are offline. `geo
lookup` calls Nominatim and follows the saved proxy policy.

### Saved channel metadata

```text
bitchat geo channel set GEOHASH [--name NAME] [--note NOTE]
                               [--bookmark | --unbookmark]
                               [--select | --unselect]
                               [--teleport | --no-teleport]
bitchat geo channel show GEOHASH [--json]
bitchat geo channel list [--bookmarked] [--json]
```

This metadata is local. `--select` controls the geohash identity used by
geohash-scoped background operations; it does not publish a channel-selection
event.

### Notes, DMs, participants, and watches

```text
bitchat geo notes add BUILDING_GEOHASH MESSAGE
bitchat geo notes list BUILDING_GEOHASH [--limit N] [--json]

bitchat geo dm send GEOHASH TARGET MESSAGE
bitchat geo dm history GEOHASH TARGET [--tail N] [--json]

bitchat geo participant show GEOHASH TARGET
bitchat geo participant block GEOHASH TARGET
bitchat geo participant unblock GEOHASH TARGET

bitchat geo watch add GEOHASH [--force]
bitchat geo watch remove GEOHASH
bitchat geo watch list [--json]
```

Notes require an 8-character building geohash. A participant target may be a
Nostr public key, `npub`, or `@nickname` when the CLI can resolve it through
local social/history state. Watches persist in `state.json`; the daemon must be
running for live background reception.

## Social and trust commands

```text
bitchat social list [--json]
bitchat social link TARGET NPUB
bitchat social unlink TARGET
bitchat social favorite TARGET
bitchat social unfavorite TARGET
bitchat social block TARGET
bitchat social unblock TARGET
bitchat social fingerprint TARGET
bitchat social verify TARGET [--payload URL]
bitchat social unverify TARGET
```

`TARGET` is normally a peer ID or `@nickname`. `favorite` authorizes Nostr
fallback only for an existing link; `block` takes precedence over favorite.
`verify` without `--payload` records local state, while `--payload` validates a
signed `bitchat://verify?...` payload before recording it.

## Proxy commands

```text
bitchat network proxy status [--json]
bitchat network proxy set TARGET [--policy prefer|require]
bitchat network proxy disable
bitchat network proxy test [--json]
```

`TARGET` is `tor`, `system`, or a proxy URL such as
`socks5h://127.0.0.1:9050`. `require` is fail-closed. `test` checks a custom
proxy's local reachability; system proxy health is reported as `unknown`.

## Direct-message commands

```text
bitchat dm inbox [--json] [--tail N]
bitchat dm history TARGET [--json] [--tail N]
bitchat dm send TARGET MESSAGE
bitchat dm send-file TARGET PATH
```

The daemon chooses the route. A live BLE link is preferred; known offline peers
can queue text DMs for later delivery; linked/favorited peers can use the Nostr
fallback for text and receipts. Files use BLE only.

## Local topic metadata

```text
bitchat topic join CHANNEL [--password PASSWORD] [--retain]
bitchat topic leave CHANNEL
bitchat topic list [--json]
bitchat topic password CHANNEL [PASSWORD]
bitchat topic retain CHANNEL on|off
```

Topic state is local metadata modeled after the Android UI. These commands do
not add a second public transport or cause the daemon to join a remote topic.

## Output and state conventions

- Human-readable output is the default.
- Where supported, `--json` prints structured data on stdout.
- Commands that need the daemon use its local Unix socket and return an error if
  it is unavailable.
- State lives under the active app directory reported by `bitchat doctor`; the
  default is `~/.config/bitchat/`, with legacy reuse of `~/.config/bitchat-cli/`.
- `--help` is the source of truth if this reference and the installed build ever
  diverge.
