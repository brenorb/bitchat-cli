# Route a BitChat direct message

![Statechart for choosing a route and accepting or rejecting a direct message](dm-route.svg)

[Mermaid source](dm-route.mmd) · [Model and local checks](../../.cursor/skills/verify-bitchat-dm-routing/SKILL.md)

Pstack explored 137 states and checked `NostrRequiresEligiblePeer`, `AcceptedIsRecorded`, and `NoDeadlock`. The chart collapses equivalent choices and omits steps that stay in one chart state. It ends at local submission. Delivery and read receipts occur later.

From the repository root, with pstack's `statechart` command and TLC available, regenerate in a new directory.

```sh
skill=.cursor/skills/verify-bitchat-dm-routing
statechart "$skill/Routes.tla" --view docs/statecharts/dm-route.view.mjs \
  --out /tmp/bitchat-dm-route
```
