#!/usr/bin/env python3
"""Collect everything on GitHub that a task can be derived from, for one owner. Read-only on GitHub.

usage: collect.py [--owner <login>]   refresh from GitHub, write that owner's cache, print the list
       collect.py --summary           SessionStart hook: print one line from the default owner's cache as
                                      hook JSON and, when that cache is missing or older than
                                      $INBOX_MAX_AGE_HOURS (default 6), start `collect.py --background`.
                                      Never touches the network itself and always exits 0.
       collect.py --background        the detached refresh behind --summary: prints nothing, does nothing
                                      while another refresh runs, and records a failure in refresh.error

The default owner is $INBOX_OWNER, else the account `gh` is logged in as. Needs `gh` with the `repo`
scope. Covers the owner's own repositories that are neither archived nor forks. POSIX only (flock).

Output, tab-separated, sorted by priority and then by `updated`, newest first:
  prio  kind  repo  ref  state  updated  title  url
kinds: 1 SECURITY (ref dependabot | code-scanning, state "<n> open, max <severity>" or "<n> open";
                   updated is when the newest open alert was created)
       2 CI       (default branch whose head commit failed its checks; ref is the branch,
                   updated is that commit's date)
       3 PR       (ref #n, state "<checks>/<review decision>[/draft]", updated is the last activity)
       4 DEPS     (a PR opened by dependabot or renovate; as PR)
       5 ISSUE    (ref #n, state is the comma-separated labels, "bot" prepended for bot-opened issues,
                   updated is the last activity)
Status lines start with "#":
  # owner=<login> generated=<epoch> repos=<n> ignored=<n>
  # SKIPPED <source> <repo>: <reason>      a source that could not be read; its items are missing.
                                           <repo> is "<owner>/*" when the source spans repositories.

Ignore rules come from $XDG_CONFIG_HOME/inbox-ignore (default ~/.config/inbox-ignore): one glob per
line, a line starting with "#" is a comment. A glob is matched against "owner/repo" (hides everything
in that repository) and against "owner/repo" + ref, e.g. "tehw0lf/airbash#7" or
"tehw0lf/*code-scanning". An ignored source is not queried, so it produces no SKIPPED line either.

Files, all in $XDG_CACHE_HOME/claude-inbox (default ~/.cache/claude-inbox):
  inbox-<owner>.tsv   the last output for that owner
  default-owner       the login a run without --owner resolved to; --summary reads it
  refresh.lock        held while a refresh runs, so refreshes never overlap
  refresh.error       "<epoch> <message>" of the last failed background refresh; removed by the next
                      successful refresh of the default owner

A `gh` call that fails or takes longer than 60 seconds is retried once for the repository list and
the search and then ends the run non-zero, leaving the cache as it was; an unreadable per-repository
source becomes a SKIPPED line.
"""
import fcntl
import fnmatch
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "claude-inbox"
IGNORE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "inbox-ignore"
GH_TIMEOUT = 60
SEARCH_LIMIT = 1000  # GitHub's search hands out no results beyond this
DEP_BOTS = {"dependabot", "dependabot-preview", "renovate", "renovate-bot"}
SEVERITIES = ["LOW", "MODERATE", "HIGH", "CRITICAL"]
KINDS = [("SECURITY", "security"), ("CI", "failing default branches"), ("PR", "PRs"),
         ("DEPS", "dependency PRs"), ("ISSUE", "issues")]

REPOS = """
query($owner: String!, $cursor: String) {
  repositoryOwner(login: $owner) {
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, isArchived: false, isFork: false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner url viewerCanAdminister
        vulnerabilityAlerts(states: OPEN, first: 100) {
          totalCount nodes { createdAt securityVulnerability { severity } }
        }
        defaultBranchRef { name target { ... on Commit { committedDate statusCheckRollup { state } } } }
      }
    }
  }
}"""

ITEMS = """
query($q: String!, $cursor: String) {
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      __typename
      ... on Issue {
        number title url updatedAt author { __typename login }
        labels(first: 20) { nodes { name } } repository { nameWithOwner }
      }
      ... on PullRequest {
        number title url updatedAt author { __typename login } isDraft reviewDecision
        repository { nameWithOwner }
        commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
      }
    }
  }
}"""


def cache_file(owner):
    return DIR / f"inbox-{owner.lower()}.tsv"


def gh(*args):
    """Run gh; a missing binary or a call over GH_TIMEOUT comes back as a failed call, not an exception."""
    try:
        return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"gh did not answer within {GH_TIMEOUT}s")
    except OSError as error:
        return subprocess.CompletedProcess(args, 127, "", f"cannot run gh: {error}")


def graphql(query, totals, **variables):
    """Yield every node of a paginated query; exit when a page fails twice. Stores a search's
    issueCount in totals["count"]."""
    cursor = None
    while True:
        args = ["api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            args += ["-f", f"{key}={value}"]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        result = gh(*args)
        if result.returncode != 0:  # GitHub answers the odd query with a 502; one retry, then give up
            time.sleep(2)
            result = gh(*args)
        if result.returncode != 0:
            sys.exit(f"collect.py: GitHub query failed: {result.stderr.strip() or result.stdout.strip()}")
        data = json.loads(result.stdout)["data"]
        page = data.get("search") or (data.get("repositoryOwner") or {}).get("repositories")
        if page is None:
            sys.exit(f"collect.py: no such owner: {variables.get('owner')}")
        totals["count"] = page.get("issueCount")
        yield from page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return
        cursor = page["pageInfo"]["endCursor"]


def code_scanning(repo):
    """(count of open alerts, creation date of the newest, skip reason). A repository without code
    scanning is not a failure: 404 means no analysis was ever uploaded, the 403 below that the
    feature is off."""
    result = gh("api", f"repos/{repo}/code-scanning/alerts?state=open&per_page=100",
                "-q", '[length, (map(.created_at) | max // "")] | @tsv')
    if result.returncode == 0:
        count, _, newest = result.stdout.strip().partition("\t")
        return int(count or 0), newest, None
    message = result.stderr.strip().splitlines()[0] if result.stderr.strip() else "unknown error"
    if "HTTP 404" in message or "Code scanning is not enabled" in message:
        return 0, "", None
    return 0, "", message


def ignore_rules():
    if not IGNORE.is_file():
        return []
    lines = (line.strip() for line in IGNORE.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def ignored(rules, repo, ref):
    return any(fnmatch.fnmatchcase(repo, rule) or fnmatch.fnmatchcase(repo + ref, rule) for rule in rules)


def clean(text):
    return " ".join(str(text).split())


def collect(owner):
    rows, skipped, rules = [], [], ignore_rules()
    repos = list(graphql(REPOS, {}, owner=owner))

    unconfirmed = 0  # no alerts reported, but this token could not have read any
    for repo in repos:
        name = repo["nameWithOwner"]
        alerts = repo["vulnerabilityAlerts"]
        if alerts["totalCount"]:
            nodes = [node for node in alerts["nodes"] if node]
            found = [node["securityVulnerability"]["severity"] for node in nodes]
            worst = max(found, key=SEVERITIES.index).lower() if found else "unknown"
            rows.append((1, "SECURITY", name, "dependabot", f"{alerts['totalCount']} open, max {worst}",
                         max((node["createdAt"] for node in nodes), default=""),
                         "Dependabot alerts", f"{repo['url']}/security/dependabot"))
        elif not repo["viewerCanAdminister"] and not ignored(rules, name, "dependabot"):
            unconfirmed += 1
        branch = repo["defaultBranchRef"]
        commit = (branch or {}).get("target") or {}
        rollup = commit.get("statusCheckRollup")
        if rollup and rollup["state"] in ("FAILURE", "ERROR"):
            rows.append((2, "CI", name, branch["name"], rollup["state"].lower(), commit["committedDate"],
                         f"checks failed on {branch['name']}", f"{repo['url']}/actions"))
    if unconfirmed:
        skipped.append(f"# SKIPPED dependabot {owner}/*: {unconfirmed} repositories report no alerts, but "
                       "without admin permission on them that cannot be told apart from unreadable")

    scanned = [repo for repo in repos if not ignored(rules, repo["nameWithOwner"], "code-scanning")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(code_scanning, (repo["nameWithOwner"] for repo in scanned))
        for repo, (count, newest, reason) in zip(scanned, results):
            if reason:
                skipped.append(f"# SKIPPED code-scanning {repo['nameWithOwner']}: {reason}")
            elif count:
                shown = "100+" if count == 100 else str(count)
                rows.append((1, "SECURITY", repo["nameWithOwner"], "code-scanning", f"{shown} open", newest,
                             "Code scanning alerts", f"{repo['url']}/security/code-scanning"))

    known = {repo["nameWithOwner"] for repo in repos}
    totals, received = {}, 0
    for item in graphql(ITEMS, totals, q=f"user:{owner} is:open archived:false"):
        received += 1
        if not item:
            continue
        name = item["repository"]["nameWithOwner"]
        if name not in known:  # a fork: not this owner's work queue
            continue
        author = item["author"] or {}
        login = (author.get("login") or "").removesuffix("[bot]")
        ref = f"#{item['number']}"
        if item["__typename"] == "PullRequest":
            commits = item["commits"]["nodes"]
            rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
            state = "/".join(filter(None, [
                (rollup["state"] if rollup else "NO_CHECKS").lower(),
                (item["reviewDecision"] or "NO_REVIEW").lower(),
                "draft" if item["isDraft"] else None]))
            prio, kind = (4, "DEPS") if login in DEP_BOTS else (3, "PR")
        else:
            labels = [label["name"] for label in item["labels"]["nodes"]]
            state = ",".join((["bot"] if author.get("__typename") == "Bot" else []) + labels) or "-"
            prio, kind = 5, "ISSUE"
        rows.append((prio, kind, name, ref, state, item["updatedAt"], item["title"], item["url"]))
    if (totals.get("count") or 0) > received:
        skipped.append(f"# SKIPPED search {owner}/*: only {received} of {totals['count']} open issues and "
                       f"pull requests were read, the search stops at {SEARCH_LIMIT}")

    kept = [row for row in rows if not ignored(rules, row[2], row[3])]
    kept.sort(key=lambda row: row[5], reverse=True)
    kept.sort(key=lambda row: row[0])
    header = f"# owner={owner} generated={int(time.time())} repos={len(repos)} ignored={len(rows) - len(kept)}"
    return "\n".join([header, *skipped, *("\t".join(clean(field) for field in row) for row in kept)]) + "\n"


def refresh(owner, background=False):
    """Collect for `owner` (None: the default owner) and write its cache. One refresh at a time: a
    background run gives way to a running one, a foreground run waits for it and then reads live."""
    DIR.mkdir(parents=True, exist_ok=True)
    with open(DIR / "refresh.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if background else 0))
        except BlockingIOError:
            return ""
        default = owner is None
        if default:
            owner = os.environ.get("INBOX_OWNER")
        if not owner:
            result = gh("api", "user", "-q", ".login")
            if result.returncode != 0:
                sys.exit(f"collect.py: cannot determine the owner: {result.stderr.strip()}")
            owner = result.stdout.strip()
        text = collect(owner)
        tmp = DIR / f"inbox.{os.getpid()}.tmp"
        tmp.write_text(text)
        tmp.replace(cache_file(owner))
        if default:
            (DIR / "default-owner").write_text(owner + "\n")
            (DIR / "refresh.error").unlink(missing_ok=True)
        return text


def refresh_running():
    try:
        with open(DIR / "refresh.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def summary():
    max_age = float(os.environ.get("INBOX_MAX_AGE_HOURS", "6")) * 3600
    line, stale = "inbox: no data yet", True
    try:
        owner = os.environ.get("INBOX_OWNER") or (DIR / "default-owner").read_text().strip()
        lines = cache_file(owner).read_text().splitlines()
        head = dict(part.split("=", 1) for part in lines[0].split()[1:])
        age = time.time() - int(head["generated"])
        stale = age > max_age
        kinds = [fields[1] for fields in (row.split("\t") for row in lines[1:] if not row.startswith("#"))]
        counts = ", ".join(f"{kinds.count(kind)} {label}" for kind, label in KINDS)
        skipped = sum(row.startswith("# SKIPPED") for row in lines)
        line = (f"inbox ({head['owner']}): {counts}"
                + (f", {skipped} sources unreadable" if skipped else "") + f" - {int(age // 3600)}h old")
    except (OSError, ValueError, KeyError, IndexError):
        pass
    try:
        failed_at, _, message = (DIR / "refresh.error").read_text().strip().partition(" ")
        line += f" - last refresh failed {int((time.time() - int(failed_at)) // 3600)}h ago: {message}"
    except (OSError, ValueError):
        pass
    if stale:
        if not refresh_running():
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--background"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        line += " - refreshing in the background"
    line += " - run the inbox skill for the list"
    print(json.dumps({"systemMessage": line,
                      "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": line}}))


def background():
    try:
        refresh(None, background=True)
    except BaseException as error:  # nobody reads a detached process: leave the reason for --summary
        message = error.code if isinstance(error, SystemExit) else repr(error)
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / "refresh.error").write_text(f"{int(time.time())} {clean(message)[:200]}\n")


def main():
    args = sys.argv[1:]
    if args == ["--summary"]:
        try:
            summary()
        except Exception as error:  # a broken inbox must never break a session start
            print(json.dumps({"systemMessage": f"inbox: summary failed: {error}"}))
    elif args == ["--background"]:
        background()
    elif len(args) == 2 and args[0] == "--owner":
        sys.stdout.write(refresh(args[1]))
    elif not args:
        sys.stdout.write(refresh(None))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
