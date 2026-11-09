"""Black-box test of scripts/issue-cost.py against synthetic transcripts.

No network, no gh and no real transcript: the fixtures in tests/fixtures/issue_cost/ are written by hand in the
observed field shape, with made-up paths, ids and prompts. The script runs as a subprocess with `--projects` and
`--prices` pointing at them. Run from the repository root: python3 -m unittest discover -s tests
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "issue-cost.py"
FIX = ROOT / "tests" / "fixtures" / "issue_cost"


def run(*extra, prices=FIX / "prices.json", projects=FIX):
    args = [sys.executable, str(SCRIPT), "--projects", str(projects), "--prices", str(prices)] + list(extra)
    return subprocess.run(args, capture_output=True, text=True)


EXPECTED = """\
# issue-cost.py prices as_of 2026-11-01 source https://example.invalid/prices; estimate at list prices, not a bill
# versions 9.9.1,9.9.2
TOKENS	acme/gadgets	5	-	model-a	2	2000000	0	0	0	-
TOKENS	acme/widgets	12	-	model-a	1	0	0	0	1000000	0.5000
TOKENS	acme/widgets	12	pr-review:pr-reviewer	model-a	1	0	1000000	0	0	2.0000
TOKENS	acme/widgets	12	work-issue:issue-planner	model-a	2	1000000	500000	2000000	1000000	9.5000
TOKENS	acme/widgets	12	work-issue:issue-worker	model-a	3	1000100	200	1000	0	-
TOKENS	acme/widgets	12	work-issue:issue-worker	model-zzz	1	10	20	0	0	-
ISSUE	acme/gadgets	5	2	2000000	0	0	0	-
ISSUE	acme/widgets	12	8	2000110	1500220	2001000	2000000	-
UNMATCHED	no-reference	-	model-a	1	5	5	0	0	0.0000
UNMATCHED	no-repository	-	model-a	1	9	0	0	0	0.0000
UNMATCHED	several-branches	-	model-a	1	7	0	0	0	0.0000
SESSION	sess1	acme/widgets	12	1
SESSION	sess1	acme/widgets	12	1
SESSION	sess1	acme/widgets	12	2
SESSION	sess1	acme/widgets	12	4
SESSION	sess2	-	-	1
SESSION	sess3	-	-	1
SESSION	sess4	-	-	1
SESSION	sess7	acme/gadgets	5	2
SESSION	sess8	acme/gadgets	5	0
# SKIPPED fields -proj-a/sess1/subagents/agent-wk.jsonl: 1 assistant messages without message.usage
# SKIPPED file -proj-a/sess6.jsonl: line 2: invalid JSON
# SKIPPED price model-a: 1 messages (no cache write breakdown)
# SKIPPED price model-a: 2 messages (speed, service_tier or inference_geo is not standard)
# SKIPPED price model-zzz: 1 messages (not in price table)
"""


class IssueCostTest(unittest.TestCase):
    def test_full_output(self):
        """Matching by prompt (both forms, lowercased key), by parent agent and by branch, the three UNMATCHED
        reasons, the split message counted once, a message repeated in a second file counted once, a file whose
        first line has no timestamp, the 5m/1h price arithmetic and the three SKIPPED kinds."""
        r = run("--since", "2026-11-01", "--until", "2026-11-30")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, EXPECTED)

    def test_planner_counts_split_message_once(self):
        r = run("--since", "2026-11-01", "--until", "2026-11-30")
        # m2 is written on three lines (two with the same id): 500000 output tokens, not 1500000
        self.assertIn("work-issue:issue-planner\tmodel-a\t2\t1000000\t500000\t2000000\t1000000\t9.5000", r.stdout)

    def test_date_filter(self):
        r = run("--since", "2026-10-01", "--until", "2026-10-31")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("TOKENS\tacme/widgets\t12\t-\tmodel-a\t1\t999\t0\t0\t0\t0.0010", r.stdout)
        self.assertNotIn("planner", r.stdout)
        # only the file in the range gets a SESSION line
        self.assertEqual([l for l in r.stdout.splitlines() if l.startswith("SESSION\t")],
                         ["SESSION\tsess5\tacme/widgets\t12\t1"])

    def test_empty_price_table_prices_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "prices.json"
            p.write_text(json.dumps({"as_of": "2026-11-09", "source": "https://example.invalid/p", "models": {}}))
            r = run("--since", "2026-11-01", "--until", "2026-11-30", prices=p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# SKIPPED price model-a: 12 messages (not in price table)", r.stdout)
        self.assertIn("ISSUE\tacme/widgets\t12\t8\t2000110\t1500220\t2001000\t2000000\t-", r.stdout)

    def test_missing_or_invalid_price_table_exits_1(self):
        r = run("--since", "2026-11-01", prices=FIX / "nope.json")
        self.assertEqual(r.returncode, 1)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "prices.json"
            p.write_text(json.dumps({"as_of": "yesterday", "source": "x", "models": {}}))
            self.assertEqual(run("--since", "2026-11-01", prices=p).returncode, 1)
            p.write_text(json.dumps({"as_of": "2026-11-01", "source": "x", "models": {"m": {"input": 1}}}))
            self.assertEqual(run("--since", "2026-11-01", prices=p).returncode, 1)

    def test_invalid_arguments_exit_2(self):
        for args, text in (((), "usage: issue-cost.py"), (("--since", "2026-13-01"), "not a date"),
                           (("--since", "2026-11-05", "--until", "2026-11-01"), "--since is after --until")):
            r = run(*args)
            self.assertEqual(r.returncode, 2)
            self.assertIn(text, r.stderr)

    def test_spawn_prompt_shapes_match_one_issue_each(self):
        """The prompt shapes that orchestrate-issue, work-issue and propose-auto-work prescribe (`owner/repo#n`,
        other issues and pull requests only as `#m` / `PR #m`) and the older `#n in owner/repo` form each match
        exactly their one issue; a second issue named as `owner/repo#m` stays unmatched."""
        prompts = {
            1: "Plan issue acme/widgets#1. Checkout: /tmp/x",
            2: "Work issue acme/widgets#2. Checkout: /tmp/x, resume PR #20",
            3: "Work issue acme/widgets#3. Checkout: /tmp/x, finish branch fix/3-some-slug",
            4: "Plan acme/widgets#4 again: the issue waits for the open #40",
            5: "Plan issue #5 in acme/widgets. Checkout: /tmp/x",
            6: "Plan acme/widgets#6 again: the issue waits for the open acme/widgets#60",
        }
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "-proj"
            proj.mkdir()
            for n, prompt in prompts.items():
                lines = [
                    {"type": "user", "message": {"role": "user", "content": prompt},
                     "timestamp": "2026-11-03T10:00:00.000Z", "version": "9.9.1", "gitBranch": "HEAD"},
                    {"type": "assistant", "message": {
                        "id": "a%d" % n, "model": "model-a", "role": "assistant", "content": [{"type": "text"}],
                        "usage": {"input_tokens": 7, "output_tokens": 0, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0, "service_tier": "standard", "speed": "standard",
                                  "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                     "ephemeral_1h_input_tokens": 0}}},
                     "timestamp": "2026-11-03T10:00:00.000Z", "version": "9.9.1", "gitBranch": "HEAD"},
                ]
                (proj / ("sess%d.jsonl" % n)).write_text("".join(json.dumps(x) + "\n" for x in lines))
            r = run("--since", "2026-11-01", "--until", "2026-11-30", projects=d)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [line.split("\t") for line in r.stdout.splitlines() if line.startswith("ISSUE\t")]
        self.assertEqual(sorted((row[1], row[2]) for row in rows),
                         [("acme/widgets", str(n)) for n in (1, 2, 3, 4, 5)])
        unmatched = [line for line in r.stdout.splitlines() if line.startswith("UNMATCHED\t")]
        self.assertEqual(len(unmatched), 1, r.stdout)

    def test_shipped_price_table_is_valid(self):
        with tempfile.TemporaryDirectory() as d:
            r = run("--since", "2026-11-01", projects=d, prices=ROOT / "scripts" / "issue-cost-prices.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("# issue-cost.py prices as_of "))


if __name__ == "__main__":
    unittest.main()
