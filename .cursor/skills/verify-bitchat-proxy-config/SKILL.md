---
name: verify-bitchat-proxy-config
description: Check persisted proxy mode, configuration status and HTTP or websocket selection when changing BitChat network settings.
---

# Verify proxy configuration selection

The CLI persists proxy mode, target and policy. The HTTP constructor rejects disabled mode under a required policy. The websocket argument helper returns an explicit direct setting for disabled mode. The CLI can produce disabled mode with a previously selected required policy because disable clears mode and URL only.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-proxy-config
sh scripts/check-cli-state-machine.sh "$skill/ProxyConfig.tla" "$skill/ProxyConfig-consistent.cfg"
PYTHONPATH=src python "$skill/references/check-proxy-arguments.py"
```

## Coverage

`src/bitchat_cli/network.py` is identical at main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source |
| --- | --- |
| Resolve a target, save it, or disable the selected mode | `app.py`, `run_network_proxy_set`, `run_network_proxy_disable`; `network.py`, `resolve_proxy_target`, `first_live_tor_proxy` |
| Compute status and redact saved credentials | `network.py`, `proxy_status_payload`, `redacted_proxy_target`, `test_proxy_url`; `app.py`, status and test commands |
| Select transport arguments or reject invalid mode and policy combinations | `network.py`, `make_http_client`, `websocket_proxy_argument` |

The model bounds one client selection and abstracts three proxy modes, policy and transport. Custom mode represents a configured URL. It omits environment-specific proxy detection, connectivity and traffic.

```sh
sh scripts/check-cli-state-machine.sh "$skill/ProxyConfig.tla"
```

The current config fails `DisabledRequiredRejected` because the websocket path returns a direct setting in disabled/required mode. The local fixture reproduces the argument difference at both refs without opening any sockets. It also checks that proxy status drops username and password. The consistent policy alternative passes in 24 states. Rerun both configs and the source fixture when changing network settings.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
