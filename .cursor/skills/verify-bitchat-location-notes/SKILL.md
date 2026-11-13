---
name: verify-bitchat-location-notes
description: Check building-level location notes, relay read completion, and merging of received notes when changing BitChat geo notes commands.
---

# Verify location-note reads

Location notes use an eight-character geohash. Each relay query collects candidate notes until the relay reports the end of stored events. The caller then merges duplicate event IDs, sorts by timestamp, and limits output.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4).

```sh
skill=.cursor/skills/verify-bitchat-location-notes
sh scripts/check-cli-state-machine.sh "$skill/NoteQuery.tla" "$skill/NoteQuery-partial.cfg"
PYTHONPATH=src python "$skill/references/check-note-timeout.py"
PYTHONPATH=src python -m pytest -q tests/test_app.py -k geo_notes
```

## Coverage

`src/bitchat_cli/location_notes.py` is identical at main `06e5ff8b5690de72361d9f17d167b5242a59671d` and tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source |
| --- | --- |
| Check building-level geohash, derive and sign a note, then send it to selected relays | `location_notes.py`, `_create_location_note_event`, `publish_location_note`, `_publish_to_relay` |
| Send a relay subscription and collect note events through end-of-stored-events or timeout | `location_notes.py`, `_fetch_from_relay` |
| Merge and limit relay results, then render them | `location_notes.py`, `fetch_location_notes`; `app.py`, `run_geo_notes_list` |

The model bounds a single relay and one received note. A timeout also represents an exception while collecting further rows. It abstracts websocket responses, validation, sorting and other relay results. The fixture supplies an ordinary note record followed by an end-of-stored-events message or timeout.

```sh
sh scripts/check-cli-state-machine.sh "$skill/NoteQuery.tla"
```

The current config fails `ReceivedNoteRetained`. The fixture confirms that the end marker returns the note and an otherwise identical read that times out returns an empty list, at both reviewed refs. The partial-result config is a model alternative that retains received rows when a relay read ends early. Rerun both configs and the fixture when the query or error handling changes.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
