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
with more results than the first page when no issue on that page is a hit. Every listed issue is read
with `issue-context.py` (work-issue skill), and the rule per issue is:
  - an issue with `needs-decision`: no hit while its newest `QUESTIONS` line is `open`, i.e. it waits
    for the owner. The planner removes the label only after it has read the answer, so a label-only
    test would skip an answered issue for good. The session does not read the label either: it decides
    by the `QUESTIONS` and `PLAN` lines, so an issue with the label and no questions of the account's
    own (a label set by hand) goes on to the next rule
  - an issue with a `RESUME` line whose stop marker names the PR's current head (compared by prefix)
    and is not `answered`: no hit, the PR waits. Reason `pending-check` is the exception: the worker
    stopped because a required check had not concluded, not for a person, so the issue is a hit once
    every required check of the default branch (active rulesets and classic branch protection, as in
    run-context.py) has the bucket `pass` on that PR in `gh pr checks --json name,bucket`. A required
    check that is missing there, pending or red keeps it waiting: a red one for a person, who re-runs
    it or pushes, a missing or pending one for the next tick. No other reason is released here: a
    push or an owner comment on the PR does that, as in the session
  - any other issue: a hit

This is the simple test of the selection, not the whole of it. It does not look at the labeler's
permission, the edit history, an assignee other than the account, a pull request of somebody else that
already closes the issue, an own PR that does not link the current plan, or a conflicting PR; such an
issue is a hit here and the session then reports it and spawns nothing. A `# SKIPPED resume` line
leaves the PR unread, and the issue is a hit as before.

Output, one line:
  WORK  <owner/repo#n>  <label|answered-or-unasked|checks-green>     a hit
  NONE                                                    no hit in any argument

Exit codes, as systemd.service(5) reads them for ExecCondition=:
  0    a hit: the unit goes on
  1    no hit: the unit is skipped and is not marked failed
  255  the check itself failed (bad argument, an owner that does not exist, `gh` missing or failing,
       time-out, an incomplete search, more search results than the first page and no hit on it,
       an unexpected error,
       or `issue-context.py` reporting `# SKIPPED account` or `plan`, or `# SKIPPED comments` while it
       printed no `QUESTIONS` line or for any reason but a thread of more than 100 comments, which leaves the
       questions unreadable, the required checks of a repository with a `pending-check` marker that
       cannot be read in full, or `gh pr checks` failing for any reason but "no checks reported"):
       the unit is marked failed and no session starts. A `QUESTIONS` line is used as it stands under the `# SKIPPED comments` line
       of a thread of more than 100 comments: the window cut itself loses nothing (the newest 100 hold
       the newest questions and every answer after them), but a comment that GitHub returned as null
       inside that window is not seen, so the check can then report a hit or a wait that the session
       would decide the other way. `# SKIPPED permission` only ever leaves `open` where an answer
       might exist, as the session reads it. Neither fails the check on its own. The reason is on stderr, nothing on stdout. Every `gh` call is given 60
       seconds, the `issue-context.py` call 600 (it makes one role lookup per commenter).
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
CONTEXT_TIMEOUT = 600  # issue-context.py makes one 60-second role lookup per distinct commenter
SEARCH_LIMIT = 1000  # the search API returns at most this many results
DEFAULT_LABEL = "auto-work"
WAITING_LABEL = "needs-decision"
UNREADABLE = {"account", "plan"}  # always fail; "comments" fails only without a QUESTIONS line
ISSUE_CONTEXT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "work-issue", "scripts", "issue-context.py"
)


def fail(message):
    print(f"has-work.py: {message}", file=sys.stderr)
    sys.exit(255)


def run(command, cwd=None, timeout=TIMEOUT):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        fail(f"{command[0]} not found")
    except subprocess.TimeoutExpired:
        fail(f"{' '.join(command[:3])} timed out after {timeout} seconds")
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


def context(repo, number):
    """The output lines of issue-context.py for the issue."""
    with tempfile.TemporaryDirectory() as scratch:
        out = run([sys.executable, ISSUE_CONTEXT, f"{repo}#{number}"], cwd=scratch,
                  timeout=CONTEXT_TIMEOUT)
    return out.splitlines()


def waiting(repo, number, lines):
    """True when the newest questions of the account are still open."""
    questions, skipped, incomplete = None, set(), False
    for line in lines:
        fields = line.split("\t")
        if fields[0] == "QUESTIONS":
            questions = fields[-1]
        elif line.startswith("# SKIPPED "):
            source = line[len("# SKIPPED "):].split(":")[0].strip()
            skipped.add(source)
            if source == "comments":
                # only a thread of more than 100 comments is a plain window; "of N" with N <= 100 means that
                # comments the query returned as null were dropped, which may be the newest questions or an answer
                window = re.search(r"only the newest 100 of (\d+) comments", line)
                if not window or int(window.group(1)) <= 100:
                    incomplete = True
    for source in sorted(skipped & UNREADABLE):
        fail(f"issue-context.py could not read {source} of {repo}#{number}")
    if incomplete or ("comments" in skipped and questions is None):
        fail(f"issue-context.py could not read the comments of {repo}#{number}")
    return questions == "open"


_required = {}


def required_checks(repo):
    """The names of the status checks the default branch requires, from the active rulesets and classic
    branch protection; a source that cannot be read fails the check."""
    if repo not in _required:
        default = run(["gh", "api", f"repos/{repo}", "-q", ".default_branch"]).strip()
        if not default:
            fail(f"cannot read the default branch of {repo}")
        names = set()
        try:
            rules = json.loads(run(["gh", "api", f"repos/{repo}/rules/branches/{default}"]))
            branch = json.loads(run(["gh", "api", f"repos/{repo}/branches/{default}"]))
        except json.JSONDecodeError:
            fail(f"the rules or the protection of {repo} did not return JSON")
        if not isinstance(rules, list) or not isinstance(branch, dict):
            fail(f"the rules or the protection of {repo} did not return the expected shape")
        for rule in rules:
            if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
                for check in (rule.get("parameters") or {}).get("required_status_checks") or []:
                    if check.get("context"):
                        names.add(check["context"])
        status = (branch.get("protection") or {}).get("required_status_checks") or {}
        names.update(check["context"] for check in status.get("checks") or [] if check.get("context"))
        names.update(context for context in status.get("contexts") or [] if context)
        _required[repo] = names
    return _required[repo]


def checks_green(repo, pull):
    """True when the default branch requires at least one check and every required check has the bucket
    `pass` on the pull request."""
    names = required_checks(repo)
    if not names:
        return False
    try:
        done = subprocess.run(
            ["gh", "pr", "checks", pull.lstrip("#"), "-R", repo, "--json", "name,bucket"],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except FileNotFoundError:
        fail("gh not found")
    except subprocess.TimeoutExpired:
        fail(f"gh pr checks {pull} timed out after {TIMEOUT} seconds")
    if done.returncode != 0:
        if "no checks reported" in done.stderr + done.stdout:
            return False
        if done.returncode != 8:  # 8: checks pending, the JSON is still printed
            reason = (done.stderr.strip() or done.stdout.strip() or f"exit {done.returncode}").splitlines()[-1]
            fail(f"gh pr checks {pull} of {repo} failed: {reason}")
    try:
        checks = json.loads(done.stdout)
    except json.JSONDecodeError:
        fail(f"gh pr checks {pull} of {repo} did not return JSON")
    if not isinstance(checks, list):
        fail(f"gh pr checks {pull} of {repo} did not return a list")
    buckets = {}
    for check in checks:
        buckets.setdefault(check.get("name"), set()).add(check.get("bucket"))
    return all(buckets.get(name) == {"pass"} for name in names)


def resume_state(repo, lines):
    """'waiting' when an own PR carries an unanswered stop marker on its current head and does not wait
    on checks that are green by now, 'checks-green' when such a marker has reason pending-check and its
    checks are green, else None."""
    state = None
    for line in lines:
        fields = line.split("\t")
        if fields[0] != "RESUME" or len(fields) != 10:
            continue
        pull, head, marker, reason, answered = fields[1], fields[2], fields[7], fields[8], fields[9]
        if marker == "-" or answered != "-" or not (head.startswith(marker) or marker.startswith(head)):
            continue
        if reason == "pending-check" and checks_green(repo, pull):
            state = state or "checks-green"
        else:
            return "waiting"
    return state


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
            lines = context(repo, number)
            if WAITING_LABEL in names and waiting(repo, number, lines):
                continue
            state = resume_state(repo, lines)
            if state == "waiting":
                continue
            kind = state or ("answered-or-unasked" if WAITING_LABEL in names else "label")
            print(f"WORK\t{repo}#{number}\t{kind}")
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
