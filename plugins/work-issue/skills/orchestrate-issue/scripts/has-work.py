#!/usr/bin/env python3
"""Say whether a scheduled orchestrate-issue run has anything to do, without starting a session. Read-only: writes nothing on GitHub, clones nothing, changes no checkout. Meant for `ExecCondition=` of a systemd unit.

usage: has-work.py [--label <name>] [--owner <login>]... [<owner/repo>]...

  --label    the opt-in label (default `auto-work`)

Needs `gh` with the `repo` scope. For each argument, in the given order (the `owner/repo` arguments first, then the
`--owner` ones), it lists the open issues that carry the label and stops at the first hit. For
`owner/repo` that is one `gh issue list` (at most 1000); for `--owner` (an account or an organisation,
as for run-context.py; a bare word is an error) one issue search (`user:<login>`, archived repositories
left out, oldest first) of which only the first page of 100 is read. The search index
can lag behind a label set just before the call, so such an issue is found by the next tick; a search
that reports itself incomplete or holds more than 1000 results is a failed check (255), and so is one
with more results than the first page when no issue on that page is a hit. The rule per issue:
  - an issue without `needs-decision`: a hit
  - an issue with `needs-decision`: a hit unless `issue-context.py` (work-issue skill) reports its
    newest `QUESTIONS` line as `open`, i.e. it waits for the owner. The planner removes the label only
    after it has read the answer, so a label-only test would skip an answered issue for good. The
    session does not read the label either: it decides by the `QUESTIONS` and `PLAN` lines, so an
    issue with the label and no questions of the account's own (a label set by hand) is a hit

This is the simple test of the selection, not the whole of it. It does not look at the labeler's
permission, the edit history, an assignee other than the account, a pull request that already closes
the issue (including one that waits for a person) or the stop marker; such an issue is a hit here and
the session then reports it and spawns nothing.

Output, one line:
  WORK  <owner/repo#n>  <label|answered-or-unasked>     a hit
  NONE                                       no hit in any argument

Exit codes, as systemd.service(5) reads them for ExecCondition=:
  0    a hit: the unit goes on
  1    no hit: the unit is skipped and is not marked failed
  255  the check itself failed (bad argument, an owner that does not exist, `gh` missing or failing,
       time-out, an incomplete search, more search results than the first page and no hit on it,
       an unexpected error,
       or `issue-context.py` reporting `# SKIPPED account`, `plan`, `comments` or `permission`, which
       leaves the questions unreadable): the unit is marked failed and no session starts. The reason
       is on stderr, nothing on stdout. Every `gh` and `issue-context.py` call is given 60 seconds.
       A script that systemd cannot start at all (path missing, `python3` not on the unit's PATH) is
       not 255: systemd skips the unit, see scheduling.md
"""
import json
import os
import re
import subprocess
import sys
import tempfile

TIMEOUT = 60
SEARCH_LIMIT = 1000  # the search API returns at most this many results
DEFAULT_LABEL = "auto-work"
WAITING_LABEL = "needs-decision"
UNREADABLE = {"account", "plan", "comments", "permission"}
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
    label, targets, owners, index = DEFAULT_LABEL, [], [], 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--label":
            if index + 1 >= len(argv) or not argv[index + 1] or '"' in argv[index + 1]:
                fail("--label needs a name without a double quote")
            label = argv[index + 1]
            index += 2
            continue
        if arg == "--owner":
            if index + 1 >= len(argv) or not re.fullmatch(r"[\w.-]+", argv[index + 1]):
                fail("--owner needs an account or organisation name")
            owners.append(argv[index + 1])
            index += 2
            continue
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", arg):
            fail(f"not owner/repo: {arg!r}")
        targets.append(arg)
        index += 1
    if not targets and not owners:
        fail("usage: has-work.py [--label <name>] [--owner <login>]... [<owner/repo>]...")
    return label, targets, owners


def waiting(repo, number):
    """True when the newest questions of the account are still open."""
    with tempfile.TemporaryDirectory() as scratch:
        out = run([sys.executable, ISSUE_CONTEXT, f"{repo}#{number}"], cwd=scratch)
    questions = None
    for line in out.splitlines():
        fields = line.split("\t")
        if fields[0] == "QUESTIONS":
            questions = fields[-1]
        elif line.startswith("# SKIPPED "):
            source = line[len("# SKIPPED "):].split(":")[0].strip()
            if source in UNREADABLE:
                fail(f"issue-context.py could not read {source} of {repo}#{number}")
    return questions == "open"


def listing(target, label, is_owner):
    """([(owner/repo, number, label names)], more) for the open issues of the target that carry the label, oldest
    first; `more` is True when the search holds more than the first page of 100, which the caller reads as a failed
    check unless one of the page is a hit."""
    if not is_owner:
        out = run([
            "gh", "issue", "list", "-R", target, "--label", label, "--state", "open",
            "--limit", "1000", "--json", "number,labels",
        ])
        try:
            issues = json.loads(out)
        except json.JSONDecodeError:
            fail(f"gh issue list for {target} did not return JSON")
        if not isinstance(issues, list):
            fail(f"gh issue list for {target} did not return a list")
        return [
            (target, issue["number"], {entry["name"] for entry in issue["labels"]})
            for issue in sorted(issues, key=lambda item: item["number"])
        ], False
    login = run(["gh", "api", f"users/{target}", "-q", ".login"]).strip()
    if not login:
        fail(f"cannot resolve {target}")
    out = run([
        "gh", "api", "-X", "GET", "search/issues", "-f",
        f'q=label:"{label}" is:issue is:open user:{login} archived:false',
        "-f", "sort=created", "-f", "order=asc", "-f", "per_page=100",
    ])
    try:
        page = json.loads(out)
    except json.JSONDecodeError:
        fail(f"the search for {target} did not return JSON")
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        fail(f"the search for {target} did not return a page")
    if page.get("incomplete_results") is not False:
        fail(f"the search for {target} reported an incomplete result")
    total = page.get("total_count")
    if not isinstance(total, int) or total > SEARCH_LIMIT:
        fail(f"the search for {target} holds {total} results, at most {SEARCH_LIMIT} are returned")
    found = []
    for item in page["items"]:
        match = re.search(r"/repos/([\w.-]+/[\w.-]+)$", item.get("repository_url") or "")
        if not match or not isinstance(item.get("number"), int):
            fail(f"the search for {target} returned an entry without repository or number")
        found.append((match.group(1), item["number"], {entry["name"] for entry in item["labels"]}))
    return found, total > len(found)


def main():
    label, targets, owners = parse(sys.argv[1:])
    for target, is_owner in [(t, False) for t in targets] + [(o, True) for o in owners]:
        found, more = listing(target, label, is_owner)
        for repo, number, names in found:
            if WAITING_LABEL not in names:
                print(f"WORK\t{repo}#{number}\tlabel")
                sys.exit(0)
            if not waiting(repo, number):
                print(f"WORK\t{repo}#{number}\tanswered-or-unasked")
                sys.exit(0)
        if more:
            fail(f"the search for {target} holds more than {len(found)} results and the first {len(found)} are no hit")
    print("NONE")
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # an uncaught exception would exit 1, which systemd reads as "skip"
        fail(f"unexpected {type(error).__name__}: {error}")
