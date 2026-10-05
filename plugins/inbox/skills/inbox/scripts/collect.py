#!/usr/bin/env python3
"""Collect everything on GitHub that a task can be derived from, for one owner. Read-only.

usage: collect.py [--owner <login>]   refresh from GitHub, write the cache, print the list
       collect.py --summary           SessionStart hook: print one line from the cache as hook JSON and,
                                      when the cache is missing or older than $INBOX_MAX_AGE_HOURS
                                      (default 6), start a refresh in the background. Never blocks on
                                      the network and always exits 0.

The owner defaults to $INBOX_OWNER, then to the account `gh` is logged in as. Needs `gh` with the
`repo` scope. Covers the owner's own repositories that are neither archived nor forks.

Output, tab-separated, sorted by priority and then by last update, newest first:
  prio  kind  repo  ref  state  updated  title  url
kinds: 1 SECURITY (ref dependabot | code-scanning, state "<n> open, max <severity>")
       2 CI       (default branch whose last commit failed its checks; ref is the branch)
       3 PR       (ref #n, state "<checks>/<review decision>[/draft]")
       4 DEPS     (a PR opened by dependabot or renovate; same state as PR)
       5 ISSUE    (ref #n, state is the comma-separated labels, "bot" prepended for bot-opened issues)
Status lines start with "#":
  # owner=<login> generated=<epoch> repos=<n> ignored=<n>
  # SKIPPED <source> <repo>: <reason>      a source that could not be read; its items are missing

Ignore rules come from $XDG_CONFIG_HOME/inbox-ignore (default ~/.config/inbox-ignore): one glob per
line, a line starting with "#" is a comment. A glob is matched against "owner/repo" (hides everything in that
repository) and against "owner/repo" + ref, e.g. "tehw0lf/airbash#7" or "tehw0lf/*code-scanning".

A failed GitHub call for the repository list or the search exits non-zero and leaves the cache as it
was; an unreadable per-repository source becomes a SKIPPED line.
"""
import fnmatch
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "claude-inbox" / "inbox.tsv"
IGNORE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "inbox-ignore"
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
        nameWithOwner url pushedAt
        vulnerabilityAlerts(states: OPEN, first: 100) { totalCount nodes { securityVulnerability { severity } } }
        defaultBranchRef { name target { ... on Commit { statusCheckRollup { state } } } }
      }
    }
  }
}"""

ITEMS = """
query($q: String!, $cursor: String) {
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {
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


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def graphql(query, **variables):
    """Yield every page of a paginated query; exit on the first failed call."""
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
        yield from page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return
        cursor = page["pageInfo"]["endCursor"]


def code_scanning(repo):
    """(count of open alerts or None, skip reason or None). A repository without code scanning is not
    a failure: 404 means no analysis was ever uploaded, the 403 below that the feature is off."""
    result = gh("api", f"repos/{repo}/code-scanning/alerts?state=open&per_page=100", "-q", "length")
    if result.returncode == 0:
        return int(result.stdout.strip() or 0), None
    message = result.stderr.strip().splitlines()[0] if result.stderr.strip() else "unknown error"
    if "HTTP 404" in message or "Code scanning is not enabled" in message:
        return None, None
    return None, message


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
    rows, skipped = [], []
    repos = list(graphql(REPOS, owner=owner))

    for repo in repos:
        name = repo["nameWithOwner"]
        alerts = repo["vulnerabilityAlerts"]
        if alerts["totalCount"]:
            found = [n["securityVulnerability"]["severity"] for n in alerts["nodes"] if n]
            worst = max(found, key=SEVERITIES.index).lower() if found else "unknown"
            rows.append((1, "SECURITY", name, "dependabot", f"{alerts['totalCount']} open, max {worst}",
                         repo["pushedAt"], "Dependabot alerts", f"{repo['url']}/security/dependabot"))
        branch = repo["defaultBranchRef"]
        rollup = ((branch or {}).get("target") or {}).get("statusCheckRollup")
        if rollup and rollup["state"] in ("FAILURE", "ERROR"):
            rows.append((2, "CI", name, branch["name"], rollup["state"].lower(), repo["pushedAt"],
                         f"checks failed on {branch['name']}", f"{repo['url']}/actions"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        for repo, (count, reason) in zip(repos, pool.map(code_scanning, (r["nameWithOwner"] for r in repos))):
            if reason:
                skipped.append(f"# SKIPPED code-scanning {repo['nameWithOwner']}: {reason}")
            elif count:
                shown = "100+" if count == 100 else str(count)
                rows.append((1, "SECURITY", repo["nameWithOwner"], "code-scanning", f"{shown} open",
                             repo["pushedAt"], "Code scanning alerts", f"{repo['url']}/security/code-scanning"))

    known = {repo["nameWithOwner"] for repo in repos}
    for item in graphql(ITEMS, q=f"user:{owner} is:open archived:false"):
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

    rules = ignore_rules()
    kept = [row for row in rows if not ignored(rules, row[2], row[3])]
    kept.sort(key=lambda row: row[5], reverse=True)
    kept.sort(key=lambda row: row[0])
    header = f"# owner={owner} generated={int(time.time())} repos={len(repos)} ignored={len(rows) - len(kept)}"
    return "\n".join([header, *skipped, *("\t".join(clean(field) for field in row) for row in kept)]) + "\n"


def refresh(owner):
    if not owner:
        result = gh("api", "user", "-q", ".login")
        if result.returncode != 0:
            sys.exit(f"collect.py: cannot determine the owner: {result.stderr.strip()}")
        owner = result.stdout.strip()
    text = collect(owner)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(CACHE)
    return text


def summary():
    max_age = float(os.environ.get("INBOX_MAX_AGE_HOURS", "6")) * 3600
    line, stale = "inbox: no data yet, collecting in the background", True
    try:
        lines = CACHE.read_text().splitlines()
        head = dict(part.split("=", 1) for part in lines[0].split()[1:])
        age = time.time() - int(head["generated"])
        stale = age > max_age
        kinds = [fields[1] for fields in (row.split("\t") for row in lines[1:] if not row.startswith("#"))]
        counts = ", ".join(f"{kinds.count(kind)} {label}" for kind, label in KINDS)
        skipped = sum(row.startswith("# SKIPPED") for row in lines)
        line = (f"inbox ({head['owner']}): {counts}"
                + (f", {skipped} sources unreadable" if skipped else "")
                + f" - {int(age // 3600)}h old" + (", refreshing in the background" if stale else "")
                + " - run the inbox skill for the list")
    except (OSError, ValueError, KeyError, IndexError):
        pass
    if stale:
        subprocess.Popen([sys.executable, os.path.abspath(__file__)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print(json.dumps({"systemMessage": line,
                      "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": line}}))


def main():
    args = sys.argv[1:]
    if args == ["--summary"]:
        try:
            summary()
        except Exception as error:  # a broken inbox must never break a session start
            print(json.dumps({"systemMessage": f"inbox: summary failed: {error}"}))
        return
    owner = os.environ.get("INBOX_OWNER")
    if len(args) == 2 and args[0] == "--owner":
        owner = args[1]
    elif args:
        sys.exit(__doc__)
    sys.stdout.write(refresh(owner))


if __name__ == "__main__":
    main()
