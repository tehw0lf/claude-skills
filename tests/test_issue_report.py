"""Black-box test of scripts/issue-report.py against hand-written fixtures in tests/fixtures/issue_report/.

No network and no gh: the fixtures are written by hand in the exact output formats of issue-stats.py and
issue-cost.py, with made-up repositories and session ids. One case feeds the real output of issue-cost.py (run on
tests/fixtures/issue_cost/) into the report, so the parser also follows the script's actual output.
Run from the repository root: python3 -m unittest discover -s tests
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "issue-report.py"
FIX = "tests/fixtures/issue_report/"
COST_FIX = ROOT / "tests" / "fixtures" / "issue_cost"


def run(*args, stdin=None):
    # run from the root with relative paths: the default label of a file is its path as given
    return subprocess.run([sys.executable, str(SCRIPT)] + list(args), capture_output=True, text=True,
                          cwd=ROOT, input=stdin)


STATS = FIX + "stats.tsv"
A = FIX + "cost-a.tsv"
B = FIX + "cost-b.tsv"

EXPECTED = """\
# issue-report.py stats tests/fixtures/issue_report/stats.tsv cost a,b
# cost a issue-cost.py prices as_of 2026-11-01 source https://example.invalid/prices; estimate at list prices, not a bill
# cost a versions 9.9.1
# cost b issue-cost.py prices as_of 2026-11-01 source https://example.invalid/prices; estimate at list prices, not a bill
# cost b versions 9.9.1,9.9.2
# a SKIPPED file -proj-a/bad.jsonl: line 2: invalid JSON
# tests/fixtures/issue_report/stats.tsv SKIPPED search: 1500 issues match, 1000 were returned (GitHub search returns at most 1000); narrow the range
# DUPLICATE sessions: the same session id is in more than one cost file; its rows are summed, so the totals may count it twice
ISSUE	Acme/Gadgets	5	closed	merged	-	0	1	1	0/0/0	1	1000000	0	0	0	1.0000
ISSUE	Acme/Gadgets	6	open	open	pending-check	2	1	0	-	1	10	20	0	0	-
ISSUE	Acme/Gadgets	7	closed	merged	-	0	1	1	0/0/2	2	2000000	0	0	0	2.0000
ISSUE	Acme/Widgets	12	closed	merged	-	1	2	2	0/1/0,0/0/1	6	1001000	1500200	2000000	1000000	11.5100
ISSUE	Acme/Widgets	14	closed	closed-unmerged	-	0	1	1	1/0/0	1	0	0	0	2000000	1.0000
AGENT	Acme/Gadgets	5	work-issue:issue-worker	model-a	1	1000000	0	0	0	1.0000
AGENT	Acme/Gadgets	6	work-issue:issue-worker	model-zzz	1	10	20	0	0	-
AGENT	Acme/Gadgets	7	work-issue:issue-worker	model-a	2	2000000	0	0	0	2.0000
AGENT	Acme/Widgets	12	work-issue:issue-planner	model-a	3	1000000	1500000	2000000	1000000	11.5000
AGENT	Acme/Widgets	12	work-issue:issue-worker	model-a	3	1000	200	0	0	0.0100
AGENT	Acme/Widgets	14	work-issue:issue-worker	model-a	1	0	0	0	2000000	1.0000
STATSONLY	Acme/Widgets	13	open	none	-	0	1	0	-
COSTONLY	acme/other	1	1	0	0	0	1000000	0.5000
UNMATCHED	no-reference	-	model-a	2	10	10	0	0	0.0200
UNMATCHED	no-repository	-	model-zzz	2	9	0	0	0	-
DUPLICATE	s-one	a,b
TOTAL	joined	11	4001010	1500220	2000000	3000000	15.5100	1
TOTAL	costonly	1	0	0	0	1000000	0.5000	0
TOTAL	unmatched	4	19	10	0	0	0.0200	2
TOTAL	all	16	4001029	1500230	2000000	4000000	16.0300	3
SHARE	unmatched	0.0012
SUMMARY	merged	issues	3
SUMMARY	merged	priced	3
SUMMARY	merged	mean_usd	4.8367
SUMMARY	merged	median_usd	2.0000
SUMMARY	merged	mean_rounds	1.33
SUMMARY	merged	median_rounds	1.00
SUMMARY	merged	mean_question_rounds	0.33
SUMMARY	merged	median_question_rounds	0.00
SUMMARY	not-merged	issues	2
SUMMARY	not-merged	priced	1
SUMMARY	not-merged	mean_usd	1.0000
SUMMARY	not-merged	median_usd	1.0000
SUMMARY	not-merged	mean_rounds	0.50
SUMMARY	not-merged	median_rounds	0.50
SUMMARY	not-merged	mean_question_rounds	1.00
SUMMARY	not-merged	median_question_rounds	1.00
SUMMARY	all	issues	5
SUMMARY	all	priced	4
SUMMARY	all	mean_usd	3.8775
SUMMARY	all	median_usd	1.5000
SUMMARY	all	mean_rounds	1.00
SUMMARY	all	median_rounds	1.00
SUMMARY	all	mean_question_rounds	0.60
SUMMARY	all	median_question_rounds	0.00
AGENTMEAN	merged	work-issue:issue-planner	1	1.00	333333.33	500000.00	666666.67	333333.33
AGENTMEAN	merged	work-issue:issue-worker	3	2.00	1000333.33	66.67	0.00	0.00
AGENTMEAN	not-merged	work-issue:issue-worker	2	1.00	5.00	10.00	0.00	1000000.00
AGENTMEAN	all	work-issue:issue-planner	1	0.60	200000.00	300000.00	400000.00	200000.00
AGENTMEAN	all	work-issue:issue-worker	5	1.60	600202.00	44.00	0.00	400000.00
"""


def lines(out, kind):
    return [l for l in out.splitlines() if l.startswith(kind + "\t")]


class IssueReportTest(unittest.TestCase):
    def test_full_output(self):
        """Join across repository case, one issue and agent summed over two cost files, STATSONLY and COSTONLY,
        UNMATCHED summed, an unpriced row that makes its issue `-` and is counted as unpriced, the DUPLICATE
        session, the passed-on SKIPPED lines and the summaries (odd and even medians)."""
        r = run("--stats", STATS, "--cost", "a=" + A, "--cost", "b=" + B)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, EXPECTED)

    def test_unpriced_is_not_zero(self):
        out = run("--stats", STATS, "--cost", "a=" + A, "--cost", "b=" + B).stdout
        self.assertIn("ISSUE\tAcme/Gadgets\t6\topen\topen\tpending-check\t2\t1\t0\t-\t1\t10\t20\t0\t0\t-", out)
        self.assertIn("TOTAL\tjoined\t11\t4001010\t1500220\t2000000\t3000000\t15.5100\t1", out)

    def test_same_session_in_one_file_is_no_duplicate(self):
        out = run("--stats", STATS, "--cost", "a=" + A).stdout
        self.assertEqual(lines(out, "DUPLICATE"), [])
        self.assertNotIn("# DUPLICATE", out)

    def test_default_labels_and_stdin(self):
        r = run("--stats", STATS, "--cost", A, "--cost", "-", stdin=Path(ROOT / B).read_text())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# issue-report.py stats %s cost %s,stdin" % (STATS, A), r.stdout)
        self.assertEqual(lines(r.stdout, "DUPLICATE"), ["DUPLICATE\ts-one\t%s,stdin" % A])
        r = run("--stats", STATS, "--cost", "a=" + A, "--cost", "late=-", stdin=Path(ROOT / B).read_text())
        self.assertEqual(lines(r.stdout, "DUPLICATE"), ["DUPLICATE\ts-one\ta,late"])

    def test_stats_from_stdin(self):
        r = run("--stats", "-", "--cost", "a=" + A, stdin=Path(ROOT / STATS).read_text())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("# issue-report.py stats stdin cost a\n"))

    def test_invalid_arguments_exit_2(self):
        cases = {
            "no --stats": (["--cost", A], "--stats is needed exactly once"),
            "two --stats": (["--stats", STATS, "--stats", STATS, "--cost", A], "--stats is needed exactly once"),
            "no --cost": (["--stats", STATS], "--cost is needed at least once"),
            "stdin twice": (["--stats", "-", "--cost", "-"], "may be used once"),
            "duplicate labels": (["--stats", STATS, "--cost", "x=" + A, "--cost", "x=" + B], "label `x`"),
            "same path twice": (["--stats", STATS, "--cost", A, "--cost", A], "label"),
            "empty label": (["--stats", STATS, "--cost", "=" + A], "empty label"),
        }
        for name, (args, text) in cases.items():
            with self.subTest(name):
                r = run(*args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn(text, r.stderr)
                self.assertEqual(r.stdout, "")

    def test_malformed_input_exits_1(self):
        stats, cost = Path(ROOT / STATS).read_text(), Path(ROOT / A).read_text()
        cases = {
            "missing file": (["--stats", STATS, "--cost", "nope.tsv"], None, "cannot read"),
            "cost file as --stats": (["--stats", A, "--cost", "-"], cost, "not the output of issue-stats.py"),
            "stats file as --cost": (["--stats", STATS, "--cost", "-"], stats, "not the output of issue-cost.py"),
            "unknown kind": (["--stats", STATS, "--cost", "x=-"], cost + "BOGUS\t1\n", "x: line 18: unknown line kind `BOGUS`"),
            "wrong field count": (["--stats", STATS, "--cost", "x=-"], cost + "SESSION\ts\t-\t-\n", "SESSION has 4 fields, expected 5"),
            "not a number": (["--stats", STATS, "--cost", "x=-"], cost + "SESSION\ts\t-\t-\tmany\n", "not a number"),
            "bad usd": (["--stats", STATS, "--cost", "x=-"], cost + "UNMATCHED\tr\t-\tm\t1\t0\t0\t0\t0\tfree\n", "not a number or `-`"),
            "empty cost": (["--stats", STATS, "--cost", "x=-"], "", "not the output of issue-cost.py"),
            "duplicate stats key": (["--stats", "-", "--cost", "x=" + A],
                                    stats + "ISSUE\tacme/widgets\t12\tclosed\t2026-11-02\t0\t1\tnone\t-\t-\t0\t-\n",
                                    "appears twice"),
        }
        for name, (args, stdin, text) in cases.items():
            with self.subTest(name):
                r = run(*args, stdin=stdin)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(text, r.stderr)
                self.assertEqual(r.stdout, "")

    def test_real_issue_cost_output_is_read(self):
        """The report parses what issue-cost.py prints now, SESSION lines and notes included."""
        cost = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "issue-cost.py"), "--projects", str(COST_FIX), "--prices",
             str(COST_FIX / "prices.json"), "--since", "2026-11-01", "--until", "2026-11-30"],
            capture_output=True, text=True)
        self.assertEqual(cost.returncode, 0, cost.stderr)
        stats = "# owner acme\nISSUE\tAcme/Widgets\t12\tclosed\t2026-11-02\t0\t1\tmerged\t-\t30\t1\t0/0/0\n"
        # stats on stdin, the real cost output as a file: standard input can be used once
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cost.tsv"
            p.write_text(cost.stdout)
            r = run("--stats", "-", "--cost", "real=" + str(p), stdin=stats)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ISSUE\tAcme/Widgets\t12\tclosed\tmerged\t-\t0\t1\t1\t0/0/0\t8\t2000110\t1500220\t2001000\t2000000\t-", r.stdout)
        self.assertIn("COSTONLY\tacme/gadgets\t5\t2\t2000000\t0\t0\t0\t-", r.stdout)
        self.assertIn("# real SKIPPED file -proj-a/sess6.jsonl: line 2: invalid JSON", r.stdout)
        self.assertEqual(len(lines(r.stdout, "UNMATCHED")), 3)
        self.assertEqual(lines(r.stdout, "DUPLICATE"), [])


if __name__ == "__main__":
    unittest.main()
