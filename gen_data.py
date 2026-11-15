"""Deterministic synthetic request-log generator (no randomness, no network).

One request every 10 seconds for 6 hours. Running this file writes data/*.csv.
"""
import datetime
import os
import sys

START = datetime.datetime(2026, 1, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)
STEP = 10
COUNT = 2161  # 0 .. 21600 s inclusive = 6 hours


def _row(i, status, slow):
    ts = (START + datetime.timedelta(seconds=i * STEP)).strftime("%Y-%m-%dT%H:%M:%SZ")
    latency = 450 if slow else 80 + (i * 37) % 120
    return (ts, status, latency)


def rows(scenario):
    out = []
    for i in range(COUNT):
        status = 200
        slow = (i % 400 == 399)  # 0.25 percent slow requests in every scenario
        minutes_from_end = (COUNT - 1 - i) * STEP / 60.0
        if scenario == "healthy":
            if i in (500, 1500):
                status = 503
        elif scenario == "fast_burn":
            if minutes_from_end < 60 and i % 20 == 0:
                status = 500  # 5 percent errors in the last hour
        elif scenario == "slow_burn":
            if i % 125 == 0:
                status = 503  # 0.8 percent errors all the time
        elif scenario == "ended_outage":
            if 10 < minutes_from_end <= 40 and i % 2 == 0:
                status = 500  # outage that ended 10 minutes ago
        else:
            raise ValueError("unknown scenario " + scenario)
        out.append(_row(i, status, slow))
    return out


def to_csv(rs):
    return "timestamp,status,latency_ms\n" + "".join("%s,%d,%d\n" % r for r in rs)


def main():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    for name in ("healthy", "fast_burn", "slow_burn", "ended_outage"):
        with open(os.path.join(d, name + ".csv"), "w") as fh:
            fh.write(to_csv(rows(name)))
        print("wrote data/%s.csv" % name)


if __name__ == "__main__":
    sys.exit(main())
