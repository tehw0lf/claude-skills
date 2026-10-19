#!/usr/bin/env python3
"""Say whether a scheduled orchestrate-issue run has anything to do, without starting a session. Read-only: writes nothing on GitHub, clones nothing, changes no checkout. Meant for `ExecCondition=` of a systemd unit.

usage: has-work.py [--label <name>] <owner/repo>...

  --label    the opt-in label (default `auto-work`)

Needs `gh` with the `repo` scope. For each repository, in the given order, it lists the open issues
that carry the label (one `gh issue list`, at most 1000) and stops at the first hit:
  - an issue without `needs-decision`: a hit
  - an issue with `needs-decision`: a hit only when `issue-context.py` (work-issue skill) reports its
    newest `QUESTIONS` line as `answered`. The planner removes the label only after it has read the
    answer, so a label-only test would skip an answered issue for good

This is the simple test of the selection, not the whole of it. It does not look at the labeler's
permission, the edit history, an assignee other than the account, a pull request that already closes
the issue (including one that waits for a person) or the stop marker; such an issue is a hit here and
the session then reports it and spawns nothing.

Output, one line:
  WORK  <owner/repo#n>  <label|answered>     a hit
  NONE                                       no hit in any repository

Exit codes, as systemd.service(5) reads them for ExecCondition=:
  0    a hit: the unit goes on
  1    no hit: the unit is skipped and is not marked failed
  255  the check itself failed (bad argument, `gh` missing or failing, time-out): the unit is marked
       failed and no session starts. The reason is on stderr, nothing on stdout. Every `gh` and
       `issue-context.py` call is given 60 seconds
"""
import json
import os
import re
import subprocess
import sys
import tempfile

TIMEOUT = 60
DEFAULT_LABEL = "auto-work"
WAITING_LABEL = "needs-decision"
ISSUE_CONTEXT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "work-issue", "scripts", "issue-context.py"
)


def fail(message):
    print(f"has-work.py: {message}", file=sys.stderr)
    sys.exit(255)


def run(command, cwd=None):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT, cwd=cwd)
    except FileNotFoundError:
        fail(f"{command[0]} not found")
    except subprocess.TimeoutExpired:
        fail(f"{' '.join(command[:3])} timed out after {TIMEOUT} seconds")
    if done.returncode != 0:
        reason = (done.stderr.strip() or done.stdout.strip() or f"exit {done.returncode}").splitlines()[-1]
        fail(f"{' '.join(command[:3])} failed: {reason}")
    return done.stdout


def parse(argv):
    label, repos, index = DEFAULT_LABEL, [], 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--label":
            if index + 1 >= len(argv) or not argv[index + 1]:
                fail("--label needs a name")
            label = argv[index + 1]
            index += 2
            continue
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", arg):
            fail(f"not owner/repo: {arg!r}")
        repos.append(arg)
        index += 1
    if not repos:
        fail("usage: has-work.py [--label <name>] <owner/repo>...")
    return label, repos


def answered(repo, number):
    with tempfile.TemporaryDirectory() as scratch:
        out = run([sys.executable, ISSUE_CONTEXT, f"{repo}#{number}"], cwd=scratch)
    for line in out.splitlines():
        fields = line.split("\t")
        if fields[0] == "QUESTIONS":
            return fields[-1] == "answered"
    return False


def main():
    label, repos = parse(sys.argv[1:])
    for repo in repos:
        out = run([
            "gh", "issue", "list", "-R", repo, "--label", label, "--state", "open",
            "--limit", "1000", "--json", "number,labels",
        ])
        try:
            issues = json.loads(out)
        except json.JSONDecodeError:
            fail(f"gh issue list for {repo} did not return JSON")
        for issue in sorted(issues, key=lambda item: item["number"]):
            names = {entry["name"] for entry in issue["labels"]}
            if WAITING_LABEL not in names:
                print(f"WORK\t{repo}#{issue['number']}\tlabel")
                sys.exit(0)
            if answered(repo, issue["number"]):
                print(f"WORK\t{repo}#{issue['number']}\tanswered")
                sys.exit(0)
    print("NONE")
    sys.exit(1)


if __name__ == "__main__":
    main()
