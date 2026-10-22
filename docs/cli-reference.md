# CLI reference

This reference mirrors the command tree in `src/bitchat_cli/cli.py`. The
authoritative runtime help is always available from the installed command:

```bash
bitchat
bitchat --version
bitchat --help
bitchat help geo
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
bitchat topic join CHANNEL [--password PASSWORD | --password-stdin] [--retain]
bitchat topic leave CHANNEL
bitchat topic list [--json]
bitchat topic password CHANNEL [PASSWORD | --password-stdin]
bitchat topic retain CHANNEL on|off
```

Topic state is local metadata modeled after the Android UI. These commands do
not add a second public transport or cause the daemon to join a remote topic.

## Output and state conventions

Results and explicit help use stdout. Errors, geohash privacy warnings, network
lookup notices, and interactive chat system logs use stderr. Read commands
that list `--json` produce a JSON document on stdout, including empty results.
Topic JSON omits the stored password and retains `password_set` metadata.

Exit statuses are 0 for success, help, and version; 1 for operational errors;
2 for invalid arguments; and 130 when a foreground command is interrupted.
A downstream program closing a pipe ends output quietly with status 0.
`network proxy test` returns 1 when the configured proxy is unhealthy while
still emitting its requested status result. `daemon status` returns 1 for a
missing/stale daemon and prints the status result. A quiet command overview
or group invocation, such as `bitchat geo channel`, prints help and returns 0.
Missing operands on a leaf command print usage and return 2.

Long flag names must be written in full. `--tail` and `--limit` accept positive
integers; scan intervals must be positive finite seconds; proof-of-work bits
must be nonnegative. Opposing bookmark/selection/teleport flags and
`--level`/`--all-levels` are mutually exclusive. Use `--` before a positional
argument starting with `-`, such as `bitchat mesh send -- '-hello'`.

Every message or note body accepts `-` to read piped UTF-8 text from stdin.
One final newline is removed, embedded newlines are retained, and an empty
message is rejected. Literal `-` is reserved for stdin. File transfers require
a local file path. `--password-stdin` reads a single nonempty password line
from a pipe or redirect; combine it with neither `--password` nor a positional
password. Omitting the password from `topic password CHANNEL` still clears it.
Legacy password arguments can be exposed through shell history/process listings.

`chat` requires terminal stdin and stdout and supports `/quit` and Ctrl-C.
Use `daemon start`, `send`, and `inbox` for automation. Short-lived commands do
not prompt. Socket operations time out after 30 seconds without progress; a
send might already have been accepted when the client times out. Inspect
`bitchat daemon status` and the inbox before deciding whether to retry.

With an absolute `XDG_CONFIG_HOME`, all state/runtime files live under
`$XDG_CONFIG_HOME/bitchat/`. Otherwise use `~/.config/bitchat/`, with existing
`~/.config/bitchat-cli/` reused when appropriate. Empty or relative XDG values
fall back to the default. Explicit XDG configuration does not migrate an old
identity or daemon. `doctor` reports the effective paths. Newly created app
directories use mode 0700; state saves replace the file atomically with mode
0600 on POSIX. Existing directories keep their permissions. State contains
private keys and local topic passwords; protect backups too. Malformed JSON or
invalid identity fields produce an error naming the file and leave it untouched.
Restore a known-good backup or correct the file while preserving private keys;
deleting state creates a different identity. Malformed daemon acknowledgements
produce a concise error; inspect daemon status before retrying an uncertain send.

`--help` describes the installed build. Documentation and support links are
included in the top-level help. No analytics or update checks are added.
