# Installed CLI end-to-end validation

`tests/test_installed_e2e.py` invokes installed `bitchat` console commands from
outside the checkout with a temporary, independent `XDG_CONFIG_HOME`. It starts
the real detached daemon, exchanges requests/replies over its actual UNIX
socket, and stops it through the installed command.

The process fixture replaces radio and public relay boundaries through an
explicit, test-only `sitecustomize.py`. Production command dispatch, daemon
startup/shutdown, socket handling, identity/state, message persistence, message
signing and packet encoding remain active. Inbound messages use a newly
generated local peer and a correctly signed packet. Outbound text and file
packets are encoded, decoded and signature checked locally; no public messages
are transmitted. The installed wheel has no test-mode switch.

The tests cover:

- Start, repeat start, status, socket readiness, and complete PID/socket/lease cleanup.
- A failed transport startup returning failure instead of a premature success.
- Piped message text reaching the real daemon and its persisted outbound log.
- A signed local peer reply appearing in the installed JSON inbox command.
- File bytes surviving the CLI, socket, file codec and signed packet path.
- Identity preservation, owner-only state permissions and topic password stdin.
- Empty stdin, invalid arguments, unknown socket commands and closed output pipes.

Run against the current installed environment:

```bash
uv run --frozen pytest tests/test_installed_e2e.py -q
```

For distribution validation, build and install into a fresh environment first:

```bash
uv build
uv venv /tmp/bitchat-e2e-env
uv pip install --python /tmp/bitchat-e2e-env/bin/python dist/bitchat4agents-0.1.0-py3-none-any.whl
BITCHAT_E2E_BIN=/tmp/bitchat-e2e-env/bin/bitchat uv run --frozen pytest tests/test_installed_e2e.py -q
```

Use a new temporary environment directory for each independent install. Test
identities and captured packets are removed on teardown. The fixture path must
be explicit in subprocess `PYTHONPATH`; normal installed commands do not load it.

## Hardware evidence and limits

During the 2026-10-22 validation on this macOS host, a real two-second Bleak scan
completed and saw nearby BLE devices without sending messages. No consenting
BitChat peer was selected. That proves scanning availability; it does not prove
BitChat peer interoperability, advertising, radio delivery, or public Nostr
relay delivery. Those need a separate controlled live-peer test. The local
transport tests prove the changed CLI lifecycle and signed artifact/socket paths.
