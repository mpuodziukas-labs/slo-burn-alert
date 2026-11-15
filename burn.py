"""SLO calculator: SLIs, error budget, multi-window burn rates. Standard library only.

Exit codes: 0 OK, 2 TICKET, 3 PAGE, 1 bad input (named cause on stderr).
"""
import csv
import datetime
import io
import sys

AVAIL_TARGET = 0.999       # 99.9 percent of requests are not 5xx
LAT_TARGET = 0.99          # 99 percent of requests finish within the threshold
LAT_THRESHOLD_MS = 300
WINDOW_SECONDS = 30 * 86400
# (burn-rate threshold, long window s, short window s)
PAGE = (14.4, 3600, 300)
TICKET = (6.0, 21600, 1800)
HEADER = ["timestamp", "status", "latency_ms"]
THIN_ROWS = 10             # fewer rows than this in the last 5 minutes gets a NOTE line
WINDOW_NAMES = {3600: "1h", 300: "5m", 21600: "6h", 1800: "30m"}


class LogError(Exception):
    """Input problem with a named cause. Never shown as a traceback."""


def _epoch(text, line):
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise LogError("line %d: bad timestamp %r" % (line, text))
    if dt.tzinfo is None:
        raise LogError("line %d: timestamp has no timezone (use Z or an offset)" % line)
    return dt.timestamp()


def parse_csv(text):
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.strip():
        raise LogError("log is empty")
    reader = csv.reader(io.StringIO(text))
    rows = []
    seen = set()
    records = []
    try:
        for rec in reader:
            records.append((reader.line_num, rec))
    except csv.Error as e:
        raise LogError("log is not readable CSV (%s)" % e)
    for i, (n, rec) in enumerate(records):
        if i == 0:
            if [c.strip() for c in rec] != HEADER:
                raise LogError("header must be %s, got %s" % (",".join(HEADER), ",".join(rec)))
            continue
        if not rec:
            continue
        if len(rec) != 3:
            raise LogError("line %d: expected 3 fields, got %d" % (n, len(rec)))
        ts = _epoch(rec[0], n)
        try:
            status = int(rec[1])
        except ValueError:
            raise LogError("line %d: status %r is not an integer" % (n, rec[1]))
        if not 100 <= status <= 599:
            raise LogError("line %d: status %d out of range" % (n, status))
        try:
            lat = float(rec[2])
        except ValueError:
            raise LogError("line %d: latency %r is not a number" % (n, rec[2]))
        if lat != lat:
            raise LogError("line %d: latency %r is not a number" % (n, rec[2]))
        if lat < 0:
            raise LogError("line %d: latency must not be negative" % n)
        key = (ts, status, lat)
        if key in seen:
            raise LogError("line %d: duplicate row (same timestamp, status, latency)" % n)
        seen.add(key)
        rows.append(key)
    if not rows:
        raise LogError("no data rows after the header")
    rows.sort()
    return rows


def burn_rate(bad, total, budget):
    if total == 0:
        return 0.0
    return (bad / float(total)) / budget


def _is_bad_avail(r):
    return r[1] >= 500


def _is_bad_lat(r):
    return r[2] > LAT_THRESHOLD_MS


def analyze(rows):
    start, end = rows[0][0], rows[-1][0]
    span = end - start
    if span <= 0:
        raise LogError("log has no time span (need at least two distinct timestamps)")
    total = len(rows)
    rate = total / span
    slis = {}
    for name, bad_fn, target in (("availability", _is_bad_avail, AVAIL_TARGET),
                                 ("latency", _is_bad_lat, LAT_TARGET)):
        budget = 1.0 - target
        bad = sum(1 for r in rows if bad_fn(r))
        allowed = budget * rate * WINDOW_SECONDS
        burn = {}
        for w in (3600, 300, 21600, 1800):
            win = [r for r in rows if end - w < r[0] <= end]
            burn[WINDOW_NAMES[w]] = burn_rate(sum(1 for r in win if bad_fn(r)), len(win), budget)
        page = burn["1h"] > PAGE[0] and burn["5m"] > PAGE[0]
        ticket = burn["6h"] > TICKET[0] and burn["30m"] > TICKET[0]
        slis[name] = {
            "target": target, "bad": bad, "sli": 1.0 - bad / float(total),
            "budget_left": 1.0 - bad / allowed, "burn": burn,
            "state": "PAGE" if page else ("TICKET" if ticket else "OK"),
        }
    states = [s["state"] for s in slis.values()]
    verdict = "PAGE" if "PAGE" in states else ("TICKET" if "TICKET" in states else "OK")
    last5m = sum(1 for r in rows if end - 300 < r[0] <= end)
    return {"rows": total, "span": span, "slis": slis, "verdict": verdict, "last5m": last5m}


def report(res):
    lines = ["log: rows=%d span=%ds window=30d (budget projected from the log's own request rate)"
             % (res["rows"], res["span"])]
    for name, s in res["slis"].items():
        b = s["burn"]
        lines.append("%s: sli=%.3f%% target=%.1f%% budget_left=%.1f%%" % (
            name, s["sli"] * 100, s["target"] * 100, s["budget_left"] * 100))
        lines.append("  burn 1h=%.2f 5m=%.2f | 6h=%.2f 30m=%.2f -> %s" % (
            b["1h"], b["5m"], b["6h"], b["30m"], s["state"]))
    if res.get("last5m", THIN_ROWS) < THIN_ROWS:
        n = res["last5m"]
        lines.append("NOTE thin data: the last 5m hold %d request%s, so a low burn rate here is not proof of health"
                     % (n, "" if n == 1 else "s"))
    lines.append("VERDICT %s" % res["verdict"])
    return "\n".join(lines) + "\n"


def _num(x):
    return format(x, ".6g")


def render_rules():
    """Prometheus alerting rules as plain text. Metric names are placeholders."""
    ab, lb = 1.0 - AVAIL_TARGET, 1.0 - LAT_TARGET
    lat_s = _num(LAT_THRESHOLD_MS / 1000.0)

    def avail(w):
        return ('sum(rate(http_requests_total{code=~"5.."}[%s])) / sum(rate(http_requests_total[%s]))' % (w, w))

    def lat(w):
        return ('1 - sum(rate(http_request_duration_seconds_bucket{le="%s"}[%s])) '
                '/ sum(rate(http_request_duration_seconds_count[%s]))' % (lat_s, w, w))

    out = ["groups:"]
    for gname, fn, budget, label in (("slo-availability-burn", avail, ab, "Availability"),
                                      ("slo-latency-burn", lat, lb, "Latency")):
        out.append("  - name: %s" % gname)
        out.append("    rules:")
        for kind, (burn, long_s, short_s), sev in (("Page", PAGE, "page"), ("Ticket", TICKET, "ticket")):
            lw, sw = {3600: "1h", 21600: "6h"}[long_s], {300: "5m", 1800: "30m"}[short_s]
            thr = _num(burn * budget)
            out.append("      - alert: %sBurn%s" % (label, kind))
            out.append("        expr: |")
            out.append("          (%s > %s)" % (fn(lw), thr))
            out.append("          and")
            out.append("          (%s > %s)" % (fn(sw), thr))
            out.append("        for: 2m")
            out.append("        labels:")
            out.append("          severity: %s" % sev)
            out.append("        annotations:")
            out.append('          summary: "%s error budget burning at more than %sx over %s and %s"'
                       % (label, _num(burn), lw, sw))
            if sev == "page":
                out.append('          runbook: "RUNBOOK.md"')
    return "\n".join(out) + "\n"


def main(argv, out=None, err=None, stdin=None):
    out = out or sys.stdout
    err = err or sys.stderr
    stdin = stdin or sys.stdin
    if len(argv) != 1:
        err.write("usage: burn.py LOG.csv   (use - for stdin)\n")
        return 1
    try:
        if argv[0] == "-":
            try:
                text = stdin.read()
            except UnicodeDecodeError:
                raise LogError("stdin is not valid UTF-8 text")
        else:
            try:
                with open(argv[0], "rb") as fh:
                    raw = fh.read()
            except OSError as e:
                raise LogError("cannot read file %s (%s)" % (argv[0], e.strerror))
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise LogError("file %s is not valid UTF-8 text" % argv[0])
        res = analyze(parse_csv(text))
    except LogError as e:
        err.write("ERROR %s\n" % e)
        return 1
    out.write(report(res))
    return {"OK": 0, "TICKET": 2, "PAGE": 3}[res["verdict"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
