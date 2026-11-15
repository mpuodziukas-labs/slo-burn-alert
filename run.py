"""Run the SLO kit on the synthetic logs, write alerts.rules.yml and OUTPUT.txt."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import burn  # noqa: E402

CASES = [("healthy.csv", "OK"), ("slow_burn.csv", "TICKET"), ("fast_burn.csv", "PAGE")]


def main():
    lines = []
    ok = 0
    for name, want in CASES:
        with open(os.path.join(HERE, "data", name)) as fh:
            res = burn.analyze(burn.parse_csv(fh.read()))
        good = res["verdict"] == want
        ok += 1 if good else 0
        lines.append("== %s (expect %s) -> %s" % (name, want, "as expected" if good else "UNEXPECTED"))
        lines.extend(burn.report(res).rstrip("\n").split("\n"))
    try:
        burn.parse_csv("timestamp,status,latency_ms\n")
        lines.append("== empty log: accepted (UNEXPECTED)")
    except burn.LogError as e:
        ok += 1
        lines.append("== empty log: rejected with cause '%s' -> as expected" % e)
    rules = burn.render_rules()
    with open(os.path.join(HERE, "alerts.rules.yml"), "w") as fh:
        fh.write(rules)
    lines.append("wrote alerts.rules.yml with %d alert rules" % rules.count("- alert:"))
    total = len(CASES) + 1
    lines.append("slo-burn-alert RESULT %d/%d" % (ok, total))
    text = "\n".join(lines) + "\n"
    with open(os.path.join(HERE, "OUTPUT.txt"), "w") as fh:
        fh.write(text)
    sys.stdout.write(text)
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
