# CLI design and guideline checks

This CLI follows the applicable [Command Line Interface Guidelines](https://clig.dev/).
The table records decisions for this release; it does not claim that optional
features such as shell completions or man pages are implemented.

| Area | Behavior and evidence |
| --- | --- |
| Basics and arguments | `argparse` handles commands, full flag names, operands, `--`, numeric ranges, and conflicting flags. `tests/test_cli.py` covers command routing; `tests/test_cli_conventions.py` covers invalid syntax before dispatch. |
| Help and discovery | Bare root/groups display help. `-h`, `--help`, `help COMMAND...`, and `--version` work without creating state. Help takes precedence over unrelated argument validation. Root help includes examples, documentation, and issue links. Internal `daemon-run` is hidden from the overview. |
| Documentation | README gives installation and workflows; CLI reference documents command syntax, scripting, state, errors, and secret handling. Installed help remains available offline. Optional man pages/completions are future improvements. |
| Output | Human output is the default, with existing explicit JSON on read commands. Results use stdout; errors/warnings/system diagnostics use stderr. No styling is emitted by noninteractive commands, so color switches and `NO_COLOR` need no special handling. Interactive `/clear` is confined to terminal chat. |
| Errors and signals | Operational filesystem/value/runtime failures produce concise stderr messages and status 1; syntax errors use status 2. Foreground interruption uses 130, with chat cleanup in `finally`. Real subprocess tests cover closed pipes without interpreter tracebacks. Unexpected programming errors retain tracebacks for diagnosis. |
| Interactivity and composition | Terminal-only `chat` is explicit. Other commands do not prompt. Messages accept `-` for stdin; password stdin/files avoid process arguments. Password files leave stdin available to agents. JSON is opt-in and topic passwords are redacted. File transfer keeps an explicit local-path interface. |
| Robustness | Daemon socket operations use a 30-second inactivity timeout, explain ambiguous send outcomes, and reject malformed/incomplete responses, including missing send identifiers. Invalid saved-state schemas are reported with path and identity-preserving recovery guidance. State writes use a private same-directory temporary file plus atomic replace, preserving the old state if replace fails. Existing protocol tests cover fragmentation, deduplication and transport behavior. |
| Future compatibility | Existing command names, aliases, default human output and legacy config lookup remain. Full flag spelling prevents future flags from silently changing abbreviation meaning. Opposing flags are rejected. `-` is explicitly reserved for message stdin; JSON no longer exposes the password field. |
| Configuration/environment | Absolute `XDG_CONFIG_HOME` selects the app directory; default/legacy lookup remains when unset, empty or relative. Selected paths are visible in `doctor`. Identity state includes secrets and is written mode 0600; new app directories use 0700. |
| Naming/distribution | Existing `bitchat` and `bchat` console entry points, Python requirement, PyPI package and Homebrew instructions remain. Version comes from installed package metadata. |
| Analytics | No analytics collection is introduced. Network and Bluetooth operations remain tied to explicit commands; offline help/encoding/selftest do not start them. |

## Verification

Install the checked-in lock without changing resolution, then run the suite:

```bash
uv sync --frozen
uv run --frozen pytest
uv run --frozen bitchat selftest
uv run --frozen bchat --version
```

The regression tests first demonstrated failures before the corresponding
fixes. They include actual subprocess checks with an isolated XDG directory,
stream separation, malformed daemon responses, a real closed stdout pipe,
private state permissions, and failure during atomic replacement. Hardware,
BLE interoperability and public relay delivery still require the existing
manual feature matrix and a live peer; unit tests do not prove those behaviors.
