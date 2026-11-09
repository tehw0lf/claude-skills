"""Black-box test of scripts/issue-stats.py against a fake `gh`.

No network and no `gh` login are needed. The script is run as a subprocess with a fake `gh` first on PATH;
the fake answers from a recorded fixture (tests/fixtures/issue_stats/<name>.json, GitHub's GraphQL shape)
and logs every call. Run from the repository root: python3 -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "issue-stats.py"
FIXTURES = ROOT / "tests" / "fixtures" / "issue_stats"

FAKE_GH = '''#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
with open(os.environ["FAKE_GH_LOG"]) as log:
    if log.read().splitlines().count(json.dumps(args)) > 1:
        sys.exit("fake gh: the same call twice, the loop does not advance: %r" % (args,))
if args[:1] != ["api"] or len(args) < 2:
    sys.exit("fake gh: unexpected call %r" % (args,))
if args[1] == "users/octo":
    print("octo")
    sys.exit(0)
if args[1] != "graphql":
    sys.exit("fake gh: unexpected call %r" % (args,))
fields = dict(a.split("=", 1) for a in args[2:] if "=" in a and not a.startswith("-"))
fixture = json.load(open(os.environ["FAKE_GH_FIXTURE"]))
query = fields.get("query", "")
if "search(" in query:
    answer = fixture["search"].get(fields.get("after", ""))
elif "node(id:" in query:
    answer = fixture["more"].get(fields.get("id", "") + ":" + fields.get("after", ""))
else:
    answer = None
if answer is None:
    sys.exit("fake gh: no recorded answer for %r" % (args,))
print(json.dumps(answer))
'''

OUTCOMES = ["ISSUE\tocto/r\t%s" % row for row in (
    "1\tclosed\t2026-01-05\t1\t1\tmerged\t-\t101\t2\t1/2/0,0/0/1",
    "2\topen\t2026-01-06\t0\t1\topen\tblocked\t102\t1\t0/1/0",
    "3\topen\t2026-01-09\t0\t1\tfinish-stopped\treview-limit\t-\t0\t-",
    "4\tclosed\t2026-01-10\t2\t1\tclosed-unmerged\t-\t104\t1\t?/?/?",
    "5\tclosed\t2026-01-12\t0\t1\tplanner-closed\tnot_planned\t-\t0\t-",
    "6\topen\t2026-01-13\t0\t1\tnone\t-\t-\t0\t-",
    "7\topen\t2026-01-14\t1\t0\tnone\t-\t-\t0\t-",
)]
SUMMARY = [
    "UNPARSED\tocto/r\t4\t104\t1",
    "QROUNDS\t0\t4", "QROUNDS\t1\t2", "QROUNDS\t2\t1",
    "OUTCOME\tclosed-unmerged\t1", "OUTCOME\tfinish-stopped\t1", "OUTCOME\tmerged\t1",
    "OUTCOME\tnone\t2", "OUTCOME\topen\t1", "OUTCOME\tplanner-closed\t1",
    "ROUNDS\t1\t2", "ROUNDS\t2\t1",
    "SUMMARY\tissues\t7", "SUMMARY\twithout_questions\t4", "SUMMARY\tmerged\t1", "SUMMARY\tmerge_rate\t0.14",
    "SUMMARY\tmean_question_rounds\t0.57", "SUMMARY\tmean_rounds\t1.33",
    "SUMMARY\tmean_blocking\t0.50", "SUMMARY\tmean_fixinpr\t1.50", "SUMMARY\tmean_followup\t0.50",
]


class IssueStats(unittest.TestCase):
    def run_script(self, name, *extra):
        """Run the script on fixture `name`; return (CompletedProcess, [(id, cursor) of each comment-page call])."""
        with tempfile.TemporaryDirectory() as tmp:
            gh = Path(tmp) / "gh"
            gh.write_text(FAKE_GH.format(python=sys.executable))
            gh.chmod(0o755)
            log = Path(tmp) / "calls.log"
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"],
                       FAKE_GH_FIXTURE=str(FIXTURES / (name + ".json")), FAKE_GH_LOG=str(log))
            args = list(extra) or ["--since", "2026-01-01", "--until", "2026-01-31"]
            if "--account" not in args:
                args += ["--account", "bot"]
            try:
                proc = subprocess.run([sys.executable, str(SCRIPT), *args, "--owner", "octo"],
                                      capture_output=True, text=True, env=env, timeout=60)
            except subprocess.TimeoutExpired:
                self.fail("the script did not finish within 60 seconds")
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        more = []
        for call in calls:
            if call[1] == "graphql" and any(a.startswith("query=") and "node(id:" in a for a in call):
                fields = dict(a.split("=", 1) for a in call[2:] if "=" in a and not a.startswith("-"))
                more.append((fields["id"], fields["after"]))
        return proc, more

    def test_outcomes_denominator_and_summary(self):
        # one issue per outcome; I8 starts before --since and I9 has a plan comment of a stranger only: not counted
        proc, _ = self.run_script("outcomes")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        lines = [ln for ln in proc.stdout.splitlines() if not ln.startswith("#")]
        self.assertEqual(lines, OUTCOMES + SUMMARY)

    def test_comment_pages_are_read_to_the_end(self):
        # I1: issue comments over three pages (the last empty), P101: two pages; no call repeated (the fake refuses one)
        _, more = self.run_script("outcomes")
        self.assertEqual(more, [("I1", "c1"), ("I1", "c2"), ("P101", "p1")])

    def test_comments_of_others_are_ignored(self):
        # P101 carries a NONE verdict with 9 findings (not counted); P102 a later stop marker by a stranger with the
        # reason `forged`, so the reason `blocked` of I2 depends on the author filter of the stop markers
        proc, _ = self.run_script("outcomes")
        rows = {ln.split("\t")[2]: ln.split("\t") for ln in proc.stdout.splitlines() if ln.startswith("ISSUE\t")}
        self.assertNotIn("9", rows["1"][-1])
        self.assertEqual(rows["2"][8], "blocked")

    def test_account_is_compared_case_insensitively(self):
        proc, _ = self.run_script("outcomes", "--since", "2026-01-01", "--until", "2026-01-31", "--account", "BOT")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SUMMARY\tissues\t7", proc.stdout.splitlines())

    def test_range_after_the_data_counts_nothing(self):
        proc, _ = self.run_script("outcomes", "--since", "2026-02-01", "--until", "2026-02-28")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [ln for ln in proc.stdout.splitlines() if not ln.startswith("#")]
        self.assertEqual(lines, ["SUMMARY\tissues\t0", "SUMMARY\twithout_questions\t0", "SUMMARY\tmerged\t0",
                                 "SUMMARY\tmerge_rate\t-", "SUMMARY\tmean_question_rounds\t-", "SUMMARY\tmean_rounds\t-",
                                 "SUMMARY\tmean_blocking\t-", "SUMMARY\tmean_fixinpr\t-", "SUMMARY\tmean_followup\t-"])

    def test_invalid_arguments_exit_2(self):
        for args in (["--since", "2026-13-01"], ["--since", "2026-02-01", "--until", "2026-01-01"], ["--since", "nope"]):
            proc, _ = self.run_script("outcomes", *args)
            self.assertEqual(proc.returncode, 2, (args, proc.stderr))
            self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
