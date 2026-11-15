# slo-burn-alert

Reads a request log (timestamp, status, latency) and computes error-budget burn rate for an availability SLO and a latency SLO, applies two multiwindow burn-rate alert rules (a fast tier and a slow tier, chosen for this kit), and prints Prometheus alerting rules as plain YAML text. For a reader who wants to see burn-rate alerting worked through on data.

## What it proves

- The healthy log exits 0 with `OK`, and the fast burn and slow burn logs give the expected PAGE or TICKET outcome. The ended outage log (TICKET, not PAGE) is checked by `test_kit.py`, not by `run.py`.
- Exit codes carry the verdict: 3 is PAGE, 2 is TICKET, 1 is bad input with a named cause.
- `run.py` regenerates `alerts.rules.yml` with 4 alert rules.

## Quick start

Run from this folder.

```
python3 run.py
```

Last line: `slo-burn-alert RESULT 4/4`. It writes `alerts.rules.yml` here.

```
python3 test_kit.py
```

Prints `Ran 49 tests` and ends with `OK`.

CI runs the same command on Python 3.9 and 3.12.

## GREEN and RED

- GREEN: the healthy log exits 0 with `OK`, the scenario logs give the expected PAGE or TICKET, and `run.py` ends with `slo-burn-alert RESULT 4/4`.
- RED: exit 3 is PAGE, exit 2 is TICKET, exit 1 is bad input (missing file, empty file, malformed row, duplicate row, timestamp without timezone) with a named cause on stderr and no traceback.

## How it works

- `gen_data.py` writes the synthetic CSV logs in `data/` from fixed scenarios.
- `burn.py` computes burn rate per window and applies the alert rules.
- `alerts.rules.yml` is the generated Prometheus rule text; `RUNBOOK.md` is the response page for the PAGE alert.
- Rules: availability SLO 99.9 percent, latency SLO 99 percent under 300 ms. PAGE when burn rate is above 14.4 on both the 1 hour and 5 minute windows. TICKET when burn rate is above 6 on both the 6 hour and 30 minute windows. The 14.4 and 6 multipliers are common multiwindow values; sending the 6x tier to a ticket rather than a page is a design choice of this kit.

## Limitations

- The CSV logs are synthetic. No real traffic.
- The metric names in the generated rules are placeholders and the rules were not validated with `promtool`.
- Budget left is projected from the log's own request rate over 30 days, not from real traffic.
- Printed values are rounded for reading: burn rates to 2 decimals, the SLI to 3, the span to whole seconds. The state is decided on the unrounded value, so a burn of 14.405 prints as `14.40` and still pages, and one failure in 250,000 requests prints as `sli=100.000%`.
- Windows are anchored on the last row of the log, not on a clock. A log that goes quiet after an outage has almost nothing in its last 5 minutes, so the burn rate there reads low. When the last 5 minutes hold fewer than 10 requests the report adds a `NOTE thin data` line; the exit code does not change. A row stamped in the future moves the anchor with it.

## Honesty statement

Everything here runs on synthetic data written for this kit. It has not been run on real systems or real data, and it is not a claim of client or production use. The counts printed above come from the commands shown.

## Requirements

Python 3 standard library only. No installs.
