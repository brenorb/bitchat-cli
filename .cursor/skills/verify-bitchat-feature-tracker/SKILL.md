---
name: verify-bitchat-feature-tracker
description: Check feature rows, worksheet validation and summary ranges when changing BitChat's feature-tracker generator.
---

# Verify feature-tracker row coverage

The generator appends a fixed feature list, adds validation and formatting through the last row, and saves a workbook with feature, summary and error sheets. Summary counts use fixed row ranges.

Use Java 11 or later with `TLA2TOOLS` pointing to [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4). The local workbook replay needs Python and openpyxl.

```sh
skill=.cursor/skills/verify-bitchat-feature-tracker
sh scripts/check-cli-state-machine.sh "$skill/Tracker.tla"
python3 "$skill/references/check-tracker-ranges.py"
```

## Coverage

Reviewed `tools/create_feature_tracker.py` at main `06e5ff8b5690de72361d9f17d167b5242a59671d`. The generator was added after the cited tag `v0.1.0` at `2bd44e628ba3a0bcd7be9564cac72c8c9e9a8c2b`.

| Transition | Source in `build` |
| --- | --- |
| Populate the feature sheet | Feature headers and `ROWS` loop |
| Bind validation and formatting to the populated row range | `DataValidation.add`, conditional formatting ranges |
| Add formulas and supporting sheets | Summary `COUNTA` and `COUNTIF` ranges, Errors sheet |
| Save workbook and print path | `OUTPUT.parent.mkdir`, `wb.save` |

The model uses two rows to represent the existing 58 and a third for a future added row. It abstracts worksheet content and formulas to row coverage. It does not evaluate spreadsheet formula results or model file save failures.

TLC checked the existing range in six states. Supply `Tracker-growth.cfg` to see `SummaryCoversRows` fail when the source list grows. The fixture runs the production builder with a temporary workbook and one extra list row in memory. The workbook validation extends to the new row, while the summary formula still stops at row 59. Rerun these checks if the feature list or summary formulas change and update the reviewed ref.

Use pstack’s `/statechart` to inspect the shortest path for a failing rule or visualize the checked flow. When the source changes, update the reviewed revision after rechecking the model and source fixture.
