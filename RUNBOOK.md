# Runbook: AvailabilityBurnPage and LatencyBurnPage

This page is for the page-level alerts in `alerts.rules.yml`. The example service and
its numbers are synthetic.

## What fired

A page alert fires only when both windows are hot at once: the 1 hour window and the
5 minute window are each burning the error budget faster than 14.4 times the allowed
rate. At that speed a 30 day budget is gone in about 2 days. The short window makes
sure the problem is still happening now and is not an outage that already ended.

## First 5 minutes

1. Acknowledge the page. Write the start time in the incident channel.
2. Say which SLI fired: availability (5xx responses) or latency (responses slower than 300 ms).
3. Run the kit on a fresh export of the request log to see current numbers:
   `/usr/bin/python3 <abs path>/burn.py <log.csv>`. Exit code 3 means page level.
4. Check what changed in the last hour: deploys, config changes, dependency incidents.

## Decide

| Finding | Action |
|---|---|
| A deploy went out inside the window | Roll it back first, investigate second |
| One dependency is failing | Fail over or shed the dependent feature, tell its owner |
| Traffic spike with timeouts | Add capacity or rate limit the heaviest caller |
| No change found after 15 minutes | Escalate to the service owner |

## Confirm recovery

The 5 minute burn rate drops below 14.4 first, then the 1 hour rate decays over the
next hour. Recovery means the 5 minute rate is back under 1.0 for 15 minutes. Do not
close the incident on the 1 hour number alone.

## After

1. Note the error budget left (the kit prints it as `budget_left`).
2. File a follow-up ticket for the cause. If budget_left is below zero, freeze risky releases until the owner decides otherwise.
3. Write a short post-incident note: timeline, cause, what would have caught it earlier.
