# Start, check, and stop the BitChat daemon

[Mermaid chart](daemon.mmd) · [Verification skill](../../.cursor/skills/verify-bitchat-daemon/SKILL.md)

![BitChat daemon lifecycle](daemon.svg)

pstack's `/statechart` checked 20 states. The command socket opens before the daemon finishes starting its services. A repeat start can report that the daemon is still getting ready. The chart hides repeat-start observations that leave the daemon in the same phase. A stop timeout leaves the daemon shutting down until the process exits.

To regenerate, copy `Daemon.tla` and `Daemon.cfg` from the skill and `daemon.view.mjs` from this directory to a scratch directory. Use pstack at `5866df557357d005b3c0c9d3da0d9e739b4bdde3` and the same TLC JAR as the skill.

```sh
"$PSTACK_STATECHART" Daemon.tla --view daemon.view.mjs --out daemon
```
