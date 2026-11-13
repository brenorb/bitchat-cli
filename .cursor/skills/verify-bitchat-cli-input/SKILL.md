---
name: verify-bitchat-cli-input
description: Check BitChat command preflight for help, piped messages, password sources and interactive terminal requirements.
---

# Verify command input preflight

The command parser resolves help before normal dispatch. Passwords can come from a direct value, a file or piped stdin. File and pipe input require one nonempty password line. A piped message must be nonempty. Interactive chat requires terminal input and output.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-cli-input
sh scripts/check-cli-state-machine.sh "$skill/Input.tla"
PYTHONPATH=src python -m pytest -q tests/test_cli.py tests/test_password_files.py
```

## Coverage

Reviewed `src/bitchat_cli/cli.py` at main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`. The tag parses arguments and directly invokes the selected handler. It lacks the modeled file and pipe branches and explicit terminal gate.

| Transition | Source |
| --- | --- |
| Build the parser, select help and parse numeric and positional arguments | `build_parser`, `_help_parser`, `_main` |
| Reject competing password sources, read and validate a single line | `_main`, password preflight |
| Read a message pipe, require terminal chat, and invoke sync or async handler | `_main`, message and dispatch branches |
| Map interruption and broken stdout to exit status | `main` |

The model bounds one input operation and distinguishes terminal from redirected I/O, validity and source count. It abstracts the input bytes and flags, and omits actions after dispatch. Source tests exercise invalid encoding, missing files, redacted errors and unchanged state after failure.

TLC checked 96 states. `Input-mutant.cfg` skips password-file validity before dispatch. In a disposable checkout, bypass the nonempty single-line check in `_main`. Three of the invalid-file tests then fail. All 30 parser and password tests passed on the restored source. Rerun and update the reviewed refs after preflight changes.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
