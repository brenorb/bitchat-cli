# BitChat CLI state machines

Reviewed main at `06e5ff8b5690de72361d9f17d167b5242a59671d` and the cited `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The Python package `bitchat4agents` exposes `bitchat` and `bchat` as aliases for `bitchat_cli.cli:main`. Both refs register the same 57 handler paths, including the internal `daemon-run`.

| Commands | State and verification |
| --- | --- |
| `doctor`, `selftest` | Read local state and health, or run offline protocol vectors and exit. Configuration initialization uses the [identity store](../.cursor/skills/verify-bitchat-identity-state/SKILL.md). |
| `chat` | Interactive input, local slash commands, mesh and DM dispatch, and lease cleanup. [Interactive session](../.cursor/skills/verify-bitchat-interactive-chat/SKILL.md) |
| `daemon start`, `daemon stop`, `daemon status`, `daemon-run` | Radio lease, socket readiness, child shutdown and request framing. [Daemon](../.cursor/skills/verify-bitchat-daemon/SKILL.md) · [Radio lease](../.cursor/skills/verify-bitchat-radio-lease/SKILL.md) · [IPC](../.cursor/skills/verify-bitchat-ipc/SKILL.md) · [Startup chart](statecharts/daemon.md) · [Radio lease chart](statecharts/radio-lease.md) |
| `send`, `mesh send`, `inbox`, `mesh inbox` | Daemon public message send and local history. [Public sync](../.cursor/skills/verify-bitchat-public-sync/SKILL.md) · [Inbox](../.cursor/skills/verify-bitchat-inbox/SKILL.md) |
| `send-file`, `mesh send-file`, `dm send-file` | Broadcast or queue outgoing attachments and persist received files. [File transfer](../.cursor/skills/verify-bitchat-file-transfer/SKILL.md) · [Receive chart](statecharts/file-receive.md) |
| `dm send`, `dm inbox`, `dm history` | Resolve target, select route, and query recorded messages and receipts. [DM routing](../.cursor/skills/verify-bitchat-dm-routing/SKILL.md) · [Inbox](../.cursor/skills/verify-bitchat-inbox/SKILL.md) · [DM chart](statecharts/dm-route.md) |
| `peers`, `social list`, `social link`, `social unlink`, `social favorite`, `social unfavorite`, `social block`, `social unblock` | Update or query timestamped observations and independent overlays. [Peer directory](../.cursor/skills/verify-bitchat-peer-directory/SKILL.md) |
| `social fingerprint`, `social verify`, `social unverify` | Build or validate verification payloads and change the local flag. [Peer verification](../.cursor/skills/verify-bitchat-peer-verification/SKILL.md) |
| `geo send`, `geo watch add`, `geo watch remove`, `geo watch list` | Validate precision, publish to relays or update live subscriptions. [Channel policy](../.cursor/skills/verify-bitchat-channel-policy/SKILL.md) · [Relay watches](../.cursor/skills/verify-bitchat-relay-watches/SKILL.md) · [Relay publication](../.cursor/skills/verify-bitchat-relay-publication/SKILL.md) · [Geohash chart](statecharts/geo-send.md) |
| `geo inbox`, `geo who`, `geo participant show`, `geo participant block`, `geo participant unblock` | Project stored geohash history and apply contact overlays. [Inbox](../.cursor/skills/verify-bitchat-inbox/SKILL.md) · [Peer directory](../.cursor/skills/verify-bitchat-peer-directory/SKILL.md) |
| `geo levels`, `geo encode`, `geo decode`, `geo lookup` | Pure coordinate and prefix conversions, with a one-shot provider lookup for places. [Proxy selection](../.cursor/skills/verify-bitchat-proxy-config/SKILL.md) |
| `geo channel set`, `geo channel show`, `geo channel list`, `topic join`, `topic leave`, `topic list`, `topic password`, `topic retain` | Local channel settings, selected geohash and topic metadata. [Channel policy](../.cursor/skills/verify-bitchat-channel-policy/SKILL.md) |
| `geo notes add`, `geo notes list` | Sign and publish building-level notes or collect relay results. [Location notes](../.cursor/skills/verify-bitchat-location-notes/SKILL.md) |
| `geo dm send`, `geo dm history` | Resolve geohash identity and peer, publish a wrapped event, and query that conversation. [Relay publication](../.cursor/skills/verify-bitchat-relay-publication/SKILL.md) · [Relay watches](../.cursor/skills/verify-bitchat-relay-watches/SKILL.md) · [Inbox](../.cursor/skills/verify-bitchat-inbox/SKILL.md) |
| `network proxy status`, `network proxy set`, `network proxy disable`, `network proxy test` | Save or read proxy configuration, check connectivity, and select transport arguments. [Proxy selection](../.cursor/skills/verify-bitchat-proxy-config/SKILL.md) |

All main commands use [CLI input preflight](../.cursor/skills/verify-bitchat-cli-input/SKILL.md).

| Shared source or entrypoint | State and verification |
| --- | --- |
| `app.py`, `cli.py`, `daemon_client.py`, `queries.py` | User commands, terminal and pipe preflight, request/reply and local views above. |
| `transport.py`, `peripheral.py` | [BLE central lifecycle](../.cursor/skills/verify-bitchat-ble-transport/SKILL.md), [macOS peripheral](../.cursor/skills/verify-bitchat-ble-peripheral/SKILL.md), [Bluetooth chart](statecharts/ble-connection.md). |
| `crypto.py`, `noise.py`, `protocol.py`, `verification.py` | Key and wire data conversion, [Noise session and pending sends](../.cursor/skills/verify-bitchat-noise-session/SKILL.md), proof verification. |
| `files.py`, `fragments.py`, `sync.py`, `dedupe.py` | File persistence, [fragment assembly and expiry](../.cursor/skills/verify-bitchat-fragments/SKILL.md), and request windows and packet deduplication. |
| `geochat.py`, `nostr.py`, `location_notes.py`, `network.py` | Watches, [ingress processing](../.cursor/skills/verify-bitchat-nostr-ingress/SKILL.md), [relay directory cache](../.cursor/skills/verify-bitchat-relay-directory/SKILL.md), publication and proxy selection. |
| `state.py`, `social.py` | Identity, history, peer and overlay storage, plus runtime paths and radio leases. |
| `__init__.py`, `constants.py` | Package initialization and constants. |
| `tools/create_feature_tracker.py` | Populate a feature audit workbook and bind summary ranges. [Tracker skill](../.cursor/skills/verify-bitchat-feature-tracker/SKILL.md) |
| `pyproject.toml`, `.github/workflows/release.yml` | Aliased Python console scripts and a version-tag distribution build followed by PyPI publishing. |

The models and replays bound local state and use isolated files, local relays or doubles for BLE and network components. Consult each skill for version differences and expected counterexamples.

The tagged PyPI build produces both console aliases in the wheel. [Package skill](../.cursor/skills/verify-bitchat-package/SKILL.md)
