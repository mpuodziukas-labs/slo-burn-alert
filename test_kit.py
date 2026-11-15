"""Tests for slo-burn-alert. Run: python3 test_kit.py"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import burn  # noqa: E402
import gen_data  # noqa: E402

HEADER = "timestamp,status,latency_ms\n"


def run_main(argv, stdin_text=None):
    out, err = io.StringIO(), io.StringIO()
    stdin = io.StringIO(stdin_text) if stdin_text is not None else None
    rc = burn.main(argv, out=out, err=err, stdin=stdin)
    return rc, out.getvalue(), err.getvalue()


def verdict_of(rows):
    return burn.analyze(burn.parse_csv(gen_data.to_csv(rows)))["verdict"]


class Scenarios(unittest.TestCase):
    def test_healthy_log_is_ok(self):
        res = burn.analyze(burn.parse_csv(gen_data.to_csv(gen_data.rows("healthy"))))
        self.assertEqual(res["verdict"], "OK")
        self.assertLess(res["slis"]["availability"]["burn"]["1h"], 1.0)

    def test_fast_burn_pages(self):
        self.assertEqual(verdict_of(gen_data.rows("fast_burn")), "PAGE")

    def test_slow_burn_tickets_but_does_not_page(self):
        self.assertEqual(verdict_of(gen_data.rows("slow_burn")), "TICKET")

    def test_cli_exit_codes_on_data_files(self):
        d = os.path.join(HERE, "data")
        self.assertEqual(run_main([os.path.join(d, "healthy.csv")])[0], 0)
        self.assertEqual(run_main([os.path.join(d, "slow_burn.csv")])[0], 2)
        self.assertEqual(run_main([os.path.join(d, "fast_burn.csv")])[0], 3)

    def test_outage_that_already_ended_does_not_page(self):
        # 1h window is hot, 5m window is clean: both must agree before paging
        self.assertEqual(verdict_of(gen_data.rows("ended_outage")), "TICKET")

    def test_row_order_does_not_matter(self):
        rows = gen_data.rows("fast_burn")
        self.assertEqual(verdict_of(list(reversed(rows))), "PAGE")

    def test_client_errors_are_not_budget_burn(self):
        rows = [(t, 404, 100) for (t, s, l) in gen_data.rows("healthy")]
        res = burn.analyze(burn.parse_csv(gen_data.to_csv(rows)))
        self.assertEqual(res["slis"]["availability"]["bad"], 0)


class Math(unittest.TestCase):
    def test_burn_rate_formula(self):
        self.assertAlmostEqual(burn.burn_rate(3, 1000, 0.001), 3.0)
        self.assertEqual(burn.burn_rate(0, 0, 0.001), 0.0)

    def test_budget_left_hand_derived(self):
        # 11 rows over 10 s -> 1.1 req/s -> 2851200 requests in 30 d
        # allowed bad = 0.001 * 2851200 = 2851.2 ; one 500 -> consumed 1/2851.2
        lines = ["2026-01-01T00:00:%02dZ,200,100" % i for i in range(10)]
        lines.append("2026-01-01T00:00:10Z,500,100")
        res = burn.analyze(burn.parse_csv(HEADER + "\n".join(lines) + "\n"))
        consumed = 1.0 - res["slis"]["availability"]["budget_left"]
        self.assertAlmostEqual(consumed, 1 / 2851.2, places=7)


class Rules(unittest.TestCase):
    def setUp(self):
        self.text = burn.render_rules()

    def test_four_alerts_present(self):
        for name in ("AvailabilityBurnPage", "AvailabilityBurnTicket",
                     "LatencyBurnPage", "LatencyBurnTicket"):
            self.assertIn("alert: " + name, self.text)

    def test_thresholds_are_burn_times_budget(self):
        for number in ("0.0144", "0.006", "0.144", "0.06"):
            self.assertIn("> " + number, self.text)

    def test_structure_without_yaml_library(self):
        self.assertTrue(self.text.startswith("groups:\n"))
        self.assertNotIn("\t", self.text)
        self.assertEqual(self.text.count("(") , self.text.count(")"))
        self.assertEqual(self.text.count("{"), self.text.count("}"))
        self.assertEqual(self.text.count("[") , self.text.count("]"))
        for block in self.text.split("- alert: ")[1:]:
            for key in ("expr:", "for:", "labels:", "annotations:"):
                self.assertIn(key, block)

    def test_each_alert_parses_to_its_windows_threshold_and_severity(self):
        import re
        want = {"AvailabilityBurnPage": (["1h", "5m"], "0.0144", "page"),
                "AvailabilityBurnTicket": (["6h", "30m"], "0.006", "ticket"),
                "LatencyBurnPage": (["1h", "5m"], "0.144", "page"),
                "LatencyBurnTicket": (["6h", "30m"], "0.06", "ticket")}
        got = {}
        for block in self.text.split("- alert: ")[1:]:
            name = block.split("\n", 1)[0].strip()
            windows = re.findall(r"\[(\d+[smh])\]", block.split("annotations:")[0])
            thresholds = set(re.findall(r">\s*([0-9.]+)\)", block))
            severity = re.search(r"severity:\s*(\w+)", block).group(1)
            self.assertEqual(len(thresholds), 1, name)
            # each window appears in both the error and the total series, so twice
            self.assertEqual(sorted(set(windows)), sorted(want[name][0]), name)
            self.assertEqual(len(windows), 4, name)
            got[name] = (sorted(set(windows)), thresholds.pop(), severity)
        self.assertEqual(sorted(got), sorted(want))
        for name, (w, th, sev) in want.items():
            self.assertEqual(got[name], (sorted(w), th, sev))

    def test_checked_in_rules_file_matches_the_generator(self):
        with open(os.path.join(HERE, "alerts.rules.yml"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), burn.render_rules())

    def test_page_alerts_point_at_runbook(self):
        self.assertTrue(os.path.exists(os.path.join(HERE, "RUNBOOK.md")))
        self.assertIn("RUNBOOK.md", self.text)

    def test_render_is_deterministic(self):
        self.assertEqual(burn.render_rules(), burn.render_rules())


class Chaos(unittest.TestCase):
    def test_header_only_log(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER)
        self.assertEqual(rc, 1)
        self.assertIn("no data rows", err)

    def test_zero_byte_log(self):
        rc, out, err = run_main(["-"], stdin_text="")
        self.assertEqual(rc, 1)
        self.assertIn("empty", err)

    def test_missing_file(self):
        rc, out, err = run_main([os.path.join(HERE, "data", "nope.csv")])
        self.assertEqual(rc, 1)
        self.assertIn("cannot read", err)
        self.assertNotIn("Traceback", out + err)

    def test_non_numeric_status_names_line(self):
        text = HEADER + "2026-01-01T00:00:00Z,200,10\n2026-01-01T00:00:10Z,abc,10\n"
        rc, out, err = run_main(["-"], stdin_text=text)
        self.assertEqual(rc, 1)
        self.assertIn("line 3", err)
        self.assertNotIn("Traceback", out + err)

    def test_bad_timestamp(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER + "yesterday,200,10\n")
        self.assertEqual(rc, 1)
        self.assertIn("timestamp", err)

    def test_timestamp_without_timezone(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER + "2026-01-01T00:00:00,200,10\n")
        self.assertEqual(rc, 1)
        self.assertIn("timezone", err)

    def test_missing_column(self):
        rc, out, err = run_main(["-"], stdin_text="timestamp,status\n2026-01-01T00:00:00Z,200\n")
        self.assertEqual(rc, 1)
        self.assertIn("header", err)

    def test_wrong_field_count(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER + "2026-01-01T00:00:00Z,200\n")
        self.assertEqual(rc, 1)
        self.assertIn("line 2", err)

    def test_duplicate_row(self):
        row = "2026-01-01T00:00:00Z,200,10\n"
        rc, out, err = run_main(["-"], stdin_text=HEADER + row + row)
        self.assertEqual(rc, 1)
        self.assertIn("duplicate", err)

    def test_negative_latency(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER + "2026-01-01T00:00:00Z,200,-5\n")
        self.assertEqual(rc, 1)
        self.assertIn("latency", err)

    def test_single_row_has_no_span(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER + "2026-01-01T00:00:00Z,200,10\n")
        self.assertEqual(rc, 1)
        self.assertIn("span", err)

    def test_no_arguments(self):
        rc, out, err = run_main([])
        self.assertEqual(rc, 1)
        self.assertIn("usage", err)


class DebugPass(unittest.TestCase):
    """Edge cases."""

    def burst(self, bad_count):
        # 1000 rows, 1 s apart; the bad rows come first so the 5 minute window is clean
        rows = []
        for i in range(1000):
            rows.append("2026-01-01T%02d:%02d:%02dZ,%d,100" % (i // 3600, (i // 60) % 60, i % 60,
                                                              500 if i < bad_count else 200))
        return HEADER + "\n".join(rows) + "\n"

    def test_exactly_on_ticket_threshold_does_not_ticket(self):
        res = burn.analyze(burn.parse_csv(self.burst(6)))
        self.assertAlmostEqual(res["slis"]["availability"]["burn"]["6h"], 6.0, places=6)
        self.assertEqual(res["verdict"], "OK")

    def test_just_over_ticket_threshold_tickets(self):
        self.assertEqual(burn.analyze(burn.parse_csv(self.burst(7)))["verdict"], "TICKET")

    def test_slow_responses_alone_page(self):
        # every request in the last hour is over 300 ms: latency SLO burns, availability does not
        rows = ["2026-01-01T%02d:%02d:%02dZ,200,%d" % (i // 3600, (i // 60) % 60, i % 60, 900)
                for i in range(0, 1000)]
        res = burn.analyze(burn.parse_csv(HEADER + "\n".join(rows) + "\n"))
        self.assertEqual(res["slis"]["latency"]["state"], "PAGE")
        self.assertEqual(res["slis"]["availability"]["state"], "OK")
        self.assertEqual(res["verdict"], "PAGE")

    def test_exactly_300ms_is_not_slow(self):
        rows = ["2026-01-01T00:%02d:%02dZ,200,300" % (i // 60, i % 60) for i in range(100)]
        res = burn.analyze(burn.parse_csv(HEADER + "\n".join(rows) + "\n"))
        self.assertEqual(res["slis"]["latency"]["bad"], 0)

    def test_status_500_is_bad_but_499_is_not(self):
        rows = ["2026-01-01T00:00:%02dZ,%d,10" % (i, s) for i, s in enumerate([499, 500, 200])]
        res = burn.analyze(burn.parse_csv(HEADER + "\n".join(rows) + "\n"))
        self.assertEqual(res["slis"]["availability"]["bad"], 1)

    def test_nan_latency_is_rejected(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER + "2026-01-01T00:00:00Z,200,nan\n2026-01-01T00:00:10Z,200,10\n")
        self.assertEqual(rc, 1)
        self.assertIn("latency", err)

    def test_nul_byte_is_a_named_error(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER + "2026-01-01T00:00:00Z,200,1\x000\n2026-01-01T00:00:10Z,200,10\n")
        self.assertEqual(rc, 1)
        self.assertNotIn("Traceback", out + err)

    def test_non_utf8_file_is_a_named_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.csv")
            with open(p, "wb") as fh:
                fh.write(b"timestamp,status,latency_ms\n\xff\xfe\n")
            rc, out, err = run_main([p])
        self.assertEqual(rc, 1)
        self.assertIn("UTF-8", err)
        self.assertNotIn("Traceback", out + err)

    def test_status_out_of_range(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER + "2026-01-01T00:00:00Z,999,10\n")
        self.assertEqual(rc, 1)
        self.assertIn("out of range", err)

    def test_unicode_in_field_does_not_crash(self):
        rc, out, err = run_main(["-"], stdin_text=HEADER + "2026-01-01T00:00:00Z,2ø00,10\n")
        self.assertEqual(rc, 1)
        self.assertNotIn("Traceback", out + err)

    def test_runbook_numbers_match_code(self):
        with open(os.path.join(HERE, "RUNBOOK.md")) as fh:
            text = fh.read()
        self.assertIn(str(burn.PAGE[0]), text)
        self.assertIn("%d ms" % burn.LAT_THRESHOLD_MS, text)


class HostileShapes(unittest.TestCase):
    """Cases from a case-running attack."""

    def test_undecodable_stdin_is_a_named_error(self):
        stdin = io.TextIOWrapper(io.BytesIO(HEADER.encode() + b"\xff\xfe,200,1\n"), "utf-8")
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(burn.main(["-"], out=out, err=err, stdin=stdin), 1)
        self.assertIn("not valid UTF-8", err.getvalue())

    def test_byte_order_mark_is_accepted(self):
        text = "﻿" + HEADER + "2026-01-01T00:00:00Z,200,10\n2026-01-01T00:00:10Z,200,10\n"
        self.assertEqual(len(burn.parse_csv(text)), 2)

    def test_error_line_is_the_physical_line(self):
        text = HEADER + '2026-01-01T00:00:00Z,"200\n",10\n2026-01-01T00:00:10Z,abc,10\n'
        with self.assertRaises(burn.LogError) as cm:
            burn.parse_csv(text)
        self.assertIn("line 4", str(cm.exception))

    def test_silence_after_an_outage_is_not_a_plain_ok(self):
        rows = ["2026-01-01T00:%02d:%02dZ,500,10" % (i // 60, i % 60) for i in range(1200)]
        rows.append("2026-01-01T04:20:00Z,200,10")
        rc, out, _err = run_main(["-"], stdin_text=HEADER + "\n".join(rows) + "\n")
        self.assertEqual(rc, 0)
        self.assertIn("NOTE thin data: the last 5m hold 1 request", out)

    def test_control_steady_traffic_has_no_note(self):
        rows = ["2026-01-01T00:%02d:%02dZ,200,10" % (i // 60, i % 60) for i in range(1200)]
        _rc, out, _err = run_main(["-"], stdin_text=HEADER + "\n".join(rows) + "\n")
        self.assertNotIn("NOTE", out)


class SurvivorKills(unittest.TestCase):
    """Pins found by the mutant QA: each case below fails on one named mutant of burn.py."""

    def log(self, offsets, status=200, latency=10):
        # one row per second offset from 2026-01-01T00:00:00Z; the last offset is the window anchor
        rows = ["2026-01-01T%02d:%02d:%02dZ,%d,%s" % (o // 3600, (o // 60) % 60, o % 60, status, latency)
                for o in offsets]
        return HEADER + "\n".join(rows) + "\n"

    def thin_note(self, offsets):
        rc, out, err = run_main(["-"], stdin_text=self.log(offsets))
        self.assertEqual(rc, 0, err)
        return [ln for ln in out.splitlines() if ln.startswith("NOTE thin data")]

    def test_ten_requests_in_the_last_5m_is_not_thin_but_nine_is(self):
        # S1: README says the NOTE appears when the last 5 minutes hold fewer than 10 requests.
        self.assertEqual(self.thin_note([0] + list(range(591, 601))), [])  # 10 in (300, 600]
        got = self.thin_note([0] + list(range(592, 601)))                   # 9 in (300, 600]
        self.assertEqual(len(got), 1)
        self.assertIn("hold 9 requests,", got[0])

    def test_note_uses_singular_for_one_request_and_plural_for_two(self):
        # S2: "1 request," but "2 requests,". The anchor row is always in the window, so 0 cannot occur.
        one = self.thin_note([0, 600])
        self.assertEqual(len(one), 1)
        self.assertIn("hold 1 request,", one[0])
        self.assertNotIn("1 requests", one[0])
        two = self.thin_note([0, 599, 600])
        self.assertEqual(len(two), 1)
        self.assertIn("hold 2 requests,", two[0])

    def test_row_exactly_5m_before_the_end_is_outside_the_last_5m(self):
        # S3: windows are (end - w, end], the same rule the burn windows use. The row at 300 is excluded,
        # so 9 rows count here and the NOTE appears; counting the edge row would make 10 and hide it.
        got = self.thin_note([0, 300] + list(range(592, 601)))
        self.assertEqual(len(got), 1)
        self.assertIn("hold 9 requests,", got[0])
        self.assertEqual(burn.analyze(burn.parse_csv(self.log([0, 300] + list(range(592, 601)))))["last5m"], 9)

    def test_zero_latency_is_valid_and_negative_latency_is_not(self):
        # S5: 0 and 0.0 are real latencies; anything below 0 is rejected by name.
        for v in ("0", "0.0"):
            rows = burn.parse_csv(self.log([0, 10], latency=v))
            self.assertEqual([r[2] for r in rows], [0.0, 0.0], v)
        with self.assertRaises(burn.LogError) as cm:
            burn.parse_csv(self.log([0, 10], latency="-0.001"))
        self.assertIn("latency must not be negative", str(cm.exception))

    def test_status_range_is_100_to_599_inclusive(self):
        # S6: 599 and 100 are accepted; 600 and 99 are rejected as out of range.
        for ok in (100, 599):
            rows = burn.parse_csv(self.log([0, 10], status=ok))
            self.assertEqual([r[1] for r in rows], [ok, ok], ok)
        for bad in (600, 99):
            with self.assertRaises(burn.LogError) as cm:
                burn.parse_csv(self.log([0, 10], status=bad))
            self.assertIn("status %d out of range" % bad, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
