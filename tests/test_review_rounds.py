"""Black-box test of scripts/review-rounds.py: its comment pagination, against a fake `gh`.

No network and no `gh` login are needed. The script is run as a subprocess with a fake `gh` first on PATH;
the fake answers from a recorded fixture (tests/fixtures/review_rounds/<name>.json, GitHub's GraphQL shape)
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
SCRIPT = ROOT / "scripts" / "review-rounds.py"
FIXTURES = ROOT / "tests" / "fixtures" / "review_rounds"

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


class ReviewRoundsPagination(unittest.TestCase):
    def run_script(self, name):
        """Run the script on fixture `name`; return (CompletedProcess, [(pr id, cursor) of each comment-page call])."""
        with tempfile.TemporaryDirectory() as tmp:
            gh = Path(tmp) / "gh"
            gh.write_text(FAKE_GH.format(python=sys.executable))
            gh.chmod(0o755)
            log = Path(tmp) / "calls.log"
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"],
                       FAKE_GH_FIXTURE=str(FIXTURES / (name + ".json")), FAKE_GH_LOG=str(log))
            try:
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT), "--since", "2026-01-01", "--until", "2026-01-31", "--owner", "octo"],
                    capture_output=True, text=True, env=env, timeout=60)
            except subprocess.TimeoutExpired:
                self.fail("the script did not finish within 60 seconds")
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        more = []
        for args in calls:
            if args[1] == "graphql" and any(a.startswith("query=") and "node(id:" in a for a in args):
                fields = dict(a.split("=", 1) for a in args[2:] if "=" in a and not a.startswith("-"))
                more.append((fields["id"], fields["after"]))
        return proc, more

    def check(self, name, expected_lines, expected_more):
        proc, more = self.run_script(name)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        lines = [ln for ln in proc.stdout.splitlines() if not ln.startswith("#")]
        self.assertNotIn("UNPARSED", [ln.split("\t")[0] for ln in lines])
        wanted = ["\t".join(ln) for ln in expected_lines]
        self.assertEqual([ln for ln in lines if ln.split("\t")[0] in ("PR", "NOVERDICT", "ROUNDS")], wanted)
        self.assertEqual(more, expected_more)

    def test_three_pages(self):
        # PR 7: 100 + 100 + 50 comments, the verdict is the last node of page 3; PR 8: 100 + 50, no verdict
        self.check("three_pages", [
            ("PR", "octo/r", "7", "1", "1/0/2", "3", "10", "4"),
            ("NOVERDICT", "octo/r", "8", "1", "2", "3"),
            ("ROUNDS", "1", "1"),
        ], [("PR_7", "c7-1"), ("PR_7", "c7-2"), ("PR_8", "c8-1")])

    def test_verdicts_on_the_page_boundary(self):
        # the first verdict ends page 1, the second starts page 2
        self.check("boundary", [
            ("PR", "octo/r", "9", "2", "0/1/0,0/0/0", "2", "5", "1"),
            ("ROUNDS", "2", "1"),
        ], [("PR_9", "c9-1")])

    def test_empty_last_page(self):
        # page 1 announces another page whose answer has no nodes: one call per PR, nothing lost
        self.check("empty_last_page", [
            ("PR", "octo/r", "10", "1", "0/0/0", "4", "6", "2"),
            ("NOVERDICT", "octo/r", "11", "1", "1", "1"),
            ("ROUNDS", "1", "1"),
        ], [("PR_10", "c10-1"), ("PR_11", "c11-1")])


if __name__ == "__main__":
    unittest.main()
