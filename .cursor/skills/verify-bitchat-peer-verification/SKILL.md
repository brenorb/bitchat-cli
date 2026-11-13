---
name: verify-bitchat-peer-verification
description: Check proof parsing, signature validation, peer binding and local verification flags when changing BitChat social verification.
---

# Verify a selected peer

The CLI resolves a peer or social contact. A supplied verification payload must validate and match the available keys of the selected peer. An explicit manual verification can set the same local flag without a payload. Unverify clears it.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-peer-verification
sh scripts/check-cli-state-machine.sh "$skill/PeerProof.tla"
PYTHONPATH=src python "$skill/references/check-peer-proof.py"
```

## Coverage

Reviewed main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source |
| --- | --- |
| Build and parse the verification payload | `verification.py`, `VerificationPayload.to_url`, `from_url`, `build_verification_payload` |
| Resolve target, check signature and compare known key values | `app.py`, `run_social_verify`; `verification.py`, `verify_verification_payload` |
| Update or clear the local verification flag | `app.py`, `run_social_verify`, `run_social_unverify`; `social.py`, `SocialStore.upsert` |

The model bounds one verification attempt. Key matching means all comparisons required by the available metadata pass. It abstracts proof parsing and signature verification, and omits concurrent updates and remote identity changes. Its proof rule applies only when the user supplies a payload.

TLC checked `ProofMustMatchPeer` in 41 states. The source fixture uses a temporary peer record and ephemeral keys. Both refs reject a signed payload for a different peer, accept the matching payload, and clear the local flag on unverify. In a disposable checkout, bypass both BLE key comparisons in `run_social_verify`. The fixture detects the mismatched proof reaching the local verified state. The matching mutant config fails the model rule. Restore and rerun before updating reviewed refs.
