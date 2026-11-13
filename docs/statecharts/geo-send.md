# Send a BitChat geohash message or add a watch

![Statechart of the geohash precision check and daemon request](geo-send.svg)

[Mermaid source](geo-send.mmd) · [Models and local checks](../../.cursor/skills/verify-bitchat-channel-policy/SKILL.md)

Pstack explored 78 states and checked `HighPrecisionRequiresForce` and `NoDeadlock`. Neighborhood precision warns. Block or building precision requires explicit force. The chart abstracts geohash parsing and ends at daemon acceptance or failure. It omits steps that stay in one chart state.

```sh
skill=.cursor/skills/verify-bitchat-channel-policy
statechart "$skill/GeoPrecision.tla" --view docs/statecharts/geo-send.view.mjs \
  --out /tmp/bitchat-geo-send
```
