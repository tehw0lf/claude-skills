"""Black-box test of plugins/inbox/skills/inbox/scripts/collect.py --owner: the --contributed mode and the
names of the failed queries, against a fake `gh`.

No network and no `gh` login are needed. The script is run as a subprocess with a fake `gh` first on PATH;
the fake answers from a fixture (tests/fixtures/collect/<name>.json, GitHub's GraphQL shape, written by hand,
never checked against GitHub's schema) and logs every call. XDG_CACHE_HOME and XDG_CONFIG_HOME point into the
test's temporary directory, so the real cache, lock and ignore file are never touched.
Run from the repository root: python3 -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "plugins" / "inbox" / "skills" / "inbox" / "scripts" / "collect.py"
FIXTURES = ROOT / "tests" / "fixtures" / "collect"

FAKE_GH = '''#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["api", "graphql"]:
    fields = dict(a.split("=", 1) for a in args[2:] if "=" in a and not a.startswith("-"))
    query = fields.get("query", "")
    fixture = json.load(open(os.environ["FAKE_GH_FIXTURE"]))
    if "repositoriesContributedTo" in query:
        kind = "contributed"
        answer = fixture["contributed"].get(fields.get("cursor", ""))
    elif "repositoryOwner" in query:
        kind = "repositories"
        answer = fixture["repositories"]
    elif "search(" in query:
        kind = "search"
        answer = fixture["search"]
    else:
        sys.exit("fake gh: unexpected query %r" % (query,))
    if os.environ.get("FAKE_GH_FAIL") == kind:
        sys.exit("fake gh: HTTP 502 for " + kind)
    if answer is None:
        sys.exit("fake gh: no recorded answer for %r" % (args,))
    print(json.dumps(answer))
elif len(args) > 1 and args[0] == "api" and "code-scanning" in args[1]:
    print("0\\t")
elif len(args) > 1 and args[0] == "api" and "actions/runs" in args[1]:
    print("[]")
else:
    sys.exit("fake gh: unexpected call %r" % (args,))
'''


class CollectOwner(unittest.TestCase):
    def run_script(self, args, fail=None):
        """Run the script with `args`; return (CompletedProcess, [kind of each graphql call], cursors of the
        contributed calls)."""
        with tempfile.TemporaryDirectory() as tmp:
            gh = Path(tmp) / "gh"
            gh.write_text(FAKE_GH.format(python=sys.executable))
            gh.chmod(0o755)
            log = Path(tmp) / "calls.log"
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"],
                       FAKE_GH_FIXTURE=str(FIXTURES / "owner.json"), FAKE_GH_LOG=str(log),
                       XDG_CACHE_HOME=str(Path(tmp) / "cache"), XDG_CONFIG_HOME=str(Path(tmp) / "config"))
            env.pop("INBOX_OWNER", None)
            env.pop("FAKE_GH_FAIL", None)
            if fail:
                env["FAKE_GH_FAIL"] = fail
            proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                                  env=env, timeout=60)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        queries = []
        cursors = []
        for call in calls:
            if call[:2] != ["api", "graphql"]:
                continue
            query = next(a for a in call if a.startswith("query="))
            kind = ("contributed" if "repositoriesContributedTo" in query else
                    "repositories" if "repositoryOwner" in query else "search")
            queries.append(kind)
            if kind == "contributed":
                cursors.append(next((a[len("cursor="):] for a in call if a.startswith("cursor=")), None))
        return proc, queries, cursors

    def header(self, proc):
        return dict(part.split("=", 1) for part in proc.stdout.splitlines()[0].split()[1:])

    def test_default_n_and_filter(self):
        proc, queries, cursors = self.run_script(["--owner", "Acme", "--contributed"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        head = self.header(proc)
        self.assertEqual(head["scope"], "Acme")
        self.assertEqual(head["contributed"], "30")
        # owner match is case-insensitive; the other owner, the archived repository and the fork are dropped
        self.assertEqual(head["repos"], "4")
        self.assertEqual(head["owners"], "Acme")
        self.assertEqual(cursors, [None, "c1"])
        self.assertNotIn("repositories", queries)
        self.assertIn("search", queries)
        self.assertIn(" scope=Acme contributed=30 generated=", proc.stdout.splitlines()[0])

    def test_limit_stops_paging(self):
        proc, queries, cursors = self.run_script(["--owner", "acme", "--contributed", "2"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.header(proc)["contributed"], "2")
        self.assertEqual(self.header(proc)["repos"], "2")
        self.assertEqual(cursors, [None])  # two repositories on the first page: the second page is not asked

    def test_limit_across_pages(self):
        proc, queries, cursors = self.run_script(["--owner", "acme", "--contributed", "3"])
        self.assertEqual(self.header(proc)["repos"], "3")
        self.assertEqual(cursors, [None, "c1"])

    def test_empty_result(self):
        proc, _, _ = self.run_script(["--owner", "nobody", "--contributed"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.header(proc)["repos"], "0")
        self.assertIn("# NOTICE none of nobody's repositories that are neither archived nor forks is among the "
                      "viewer's recent contributions", proc.stdout)

    def test_rejected_arguments(self):
        for args in (["--contributed"], ["--repo", "a/b", "--contributed"], ["--here", "--contributed"],
                     ["--summary", "--contributed"], ["--contributed", "--owner", "acme"],
                     ["--owner", "acme", "--contributed", "0"], ["--owner", "acme", "--contributed", "x"],
                     ["--owner", "acme", "--contributed", "-1"], ["--owner", "acme", "--contributed", "2", "3"]):
            with self.subTest(args=args):
                proc, queries, _ = self.run_script(args)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("--contributed", proc.stderr)
                self.assertEqual(queries, [])

    def test_full_run_unchanged(self):
        proc, queries, _ = self.run_script(["--owner", "acme"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("contributed", queries)
        self.assertNotIn("contributed=", proc.stdout.splitlines()[0])
        self.assertEqual(self.header(proc)["repos"], "1")

    def test_failed_query_is_named(self):
        for fail, args in (("repositories", ["--owner", "acme"]), ("search", ["--owner", "acme"]),
                           ("contributed", ["--owner", "acme", "--contributed"])):
            with self.subTest(query=fail):
                proc, _, _ = self.run_script(args, fail=fail)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(f"collect.py: GitHub query {fail} failed:", proc.stderr)


if __name__ == "__main__":
    unittest.main()
