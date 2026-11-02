#!/usr/bin/env python3
"""Collect everything on GitHub that a task can be derived from, for one owner. Read-only on GitHub.

usage: collect.py                     refresh from GitHub for the default owner, write its cache, print the list
       collect.py --owner <login>     the list for that one user or organisation, live; writes no cache
                                      (a cache of it would replace the file --summary reads when the
                                      owner is the default one), but takes the lock like a refresh
       collect.py --repo <owner/name> the same list for one repository, live; writes no cache and takes no
                                      lock, so the owner's cache and the --summary line stay owner-wide
       collect.py --here              --repo for the repository `gh repo view` resolves for the current
                                      directory: the one set with `gh repo set-default`, else the remote
                                      `upstream`, `github` or `origin`, in that order (a fork checkout with an
                                      `upstream` remote lists the parent). Exit 2 (message on stderr) when gh
                                      says there is none (no work tree, no remote, no remote on a GitHub
                                      host); every other failure, a failed gh call included, exits 1.
       collect.py --summary           SessionStart hook: print one line from the default owner's cache as
                                      hook JSON and, when that cache is missing or older than
                                      $INBOX_MAX_AGE_HOURS (default 6), start `collect.py --background`.
                                      Never touches the network itself and always exits 0.
       collect.py --background        the detached refresh behind --summary: prints nothing, does nothing
                                      while another refresh runs, and records a failure in refresh.error

The default owner is $INBOX_OWNER, else the account `gh` is logged in as. When it is the `gh` login
itself, the default run also covers every organisation that login is a member of (`viewer { organizations }`,
read at run time, first 100), in one merged list and one cache file; an organisation hidden by an
"<org>/*" ignore line is not queried. With $INBOX_OWNER naming someone else, `--owner`, `--repo` and
`--here` the run stays that one owner or repository. Needs `gh` with the `repo` scope, and `read:org` to
see the organisations. Covers the owners' own repositories that are neither archived nor forks. POSIX only
(flock).

Output, tab-separated, sorted by priority and then by `updated`, newest first:
  prio  kind  repo  ref  state  updated  title  url  assignees  closing
kinds: 1 SECURITY (ref dependabot | code-scanning, state "<n> open, max <severity>[, <k> without patch]"
                   or "<n> open"; "without patch" counts alerts with no patched version, i.e. ones a
                   dependency update cannot fix, counted over the first 100 alerts; updated is when the newest open alert was created)
       2 CI       (default branch whose head commit failed its checks; ref is the branch,
                   updated is that commit's date)
       2 RUN      (a workflow, or a Dependabot update job, of the default branch whose newest completed
                   run failed; listed after all CI rows; ref is the workflow file's name or the job
                   ("npm_and_yarn in /app"), state is the conclusion, updated is when the run was created)
       3 PR       (ref #n, state "<checks>/<review decision>[/draft]", updated is the last activity)
       4 DEPS     (a PR opened by dependabot or renovate; as PR)
       5 ISSUE    (ref #n, state is the comma-separated labels, "bot" prepended for bot-opened issues,
                   updated is the last activity; assignees is the comma-separated logins and closing the
                   open pull requests that would close the issue, as "#n" or "owner/repo#n", from the
                   first 10 of each)
The last two columns are "-" for every kind but ISSUE, and "-" for an issue without assignees or closing PRs.
Status lines start with "#":
  # owner=<login> owners=<login>[,<org>...] scope=<login | owner/name> generated=<epoch> repos=<n> ignored=<n>
                                           owners are the owners whose repositories were read (repos is
                                           their total); an older cache file has no owners key
  # NOTICE <text>                          repos=0: nothing was found to list, and why
  # SKIPPED <source> <repo>: <reason>      a source that could not be read; its items are missing.
                                           <repo> is "<owner>/*" when the source spans repositories.
                                           Sources: "organizations <login>/*" (the organisation list could
                                           not be read, so only the login is listed; or it has more than
                                           100, so the first 100 are; or $INBOX_OWNER is set and the gh
                                           login could not be read, so no organisation is added) and
                                           "repositories <org>/*" (that organisation's repositories could not
                                           be read; its search is not run either) and
                                           "dependabot <owner>/*" (repositories report no alerts but the token
                                           lacks admin permission to tell that from unreadable, or they have
                                           Dependabot alerts disabled; hidden by a "dependabot" ignore rule).

Ignore rules come from $XDG_CONFIG_HOME/inbox-ignore (default ~/.config/inbox-ignore): one glob per
line, a line starting with "#" is a comment. A glob is matched against "owner/repo" (hides everything
in that repository) and against "owner/repo" + ref, e.g. "tehw0lf/airbash#7" or
"tehw0lf/*code-scanning" or "tehw0lf/*runs". An ignored source is not queried, so it produces no SKIPPED line either.

Files, all in $XDG_CACHE_HOME/claude-inbox (default ~/.cache/claude-inbox):
  inbox-<owner>.tsv   the last output of the default run, which resolved to that owner (only that run
                      writes it)
  default-owner       the login a run without --owner resolved to; --summary reads it
  refresh.lock        held while a refresh runs, so refreshes never overlap
  refresh.error       "<epoch> <message>" of the last failed background refresh; removed by the next
                      successful refresh of the default owner

A `gh` call that fails or takes longer than 60 seconds is retried once for the repository list and
the search and then ends the run non-zero, leaving the cache as it was; for an organisation of the
default run, a failed repository list or search becomes a SKIPPED line instead. An unreadable
per-repository source becomes a SKIPPED line. The runs of a repository are read with one call for the newest 100 runs
of its default branch; a workflow or job whose newest run is not among them is not seen. A run decides
for its workflow only when it is completed and belongs to the repository itself; red is failure,
timed_out or startup_failure. A failed push run of the head commit becomes no RUN row (its CI row covers
it), except a startup_failure, which creates no checks and so never reaches the rollup; the run still
counts as the newest run of its workflow, so an older red run does not come back.
Dependabot runs are grouped per update job, keyed by the run title ("<ecosystem> in <directory>"), because
one Dependabot workflow runs a job per ecosystem and directory; this depends on the shape of GitHub's
titles, and a title of none of the three known shapes falls back to one job per workflow path. The key
includes the directory list, so a change of a repository's directory list for an ecosystem splits its rows,
like a change of GitHub's title text: the last red run under the old key stays listed until it leaves the
newest 100 runs.
"""
import fcntl
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "claude-inbox"
IGNORE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "inbox-ignore"
GH_TIMEOUT = 60
SEARCH_LIMIT = 1000  # GitHub's search hands out no results beyond this
DEP_BOTS = {"dependabot", "dependabot-preview", "renovate", "renovate-bot"}
SEVERITIES = ["LOW", "MODERATE", "HIGH", "CRITICAL"]
KINDS = [("SECURITY", "security"), ("CI", "failing default branches"), ("RUN", "failed runs"), ("PR", "PRs"),
         ("DEPS", "dependency PRs"), ("ISSUE", "issues")]

REPO_FIELDS = """
        nameWithOwner url viewerCanAdminister hasVulnerabilityAlertsEnabled
        vulnerabilityAlerts(states: OPEN, first: 100) {
          totalCount nodes { createdAt securityVulnerability { severity firstPatchedVersion { identifier } } }
        }
        defaultBranchRef { name target { ... on Commit { committedDate oid statusCheckRollup { state } } } }"""

REPOS = """
query($owner: String!, $cursor: String) {
  repositoryOwner(login: $owner) {
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, isArchived: false, isFork: false) {
      pageInfo { hasNextPage endCursor }
      nodes {%s}
    }
  }
}""" % REPO_FIELDS

ORGANIZATIONS = """
query {
  viewer { organizations(first: 100) { totalCount pageInfo { hasNextPage } nodes { login } } }
}"""

REPO = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {%s}
}""" % REPO_FIELDS

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
        assignees(first: 10) { nodes { login } }
        closedByPullRequestsReferences(first: 10, includeClosedPrs: false) {
          nodes { number repository { nameWithOwner } }
        }
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
        if "repository" in data:  # a single repository: no pages; GitHub answers an unknown one with an error
            yield data["repository"]
            return
        page = data.get("search") or (data.get("repositoryOwner") or {}).get("repositories")
        if page is None:
            sys.exit(f"collect.py: no such owner: {variables.get('owner')}")
        totals["count"] = page.get("issueCount")
        yield from page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return
        cursor = page["pageInfo"]["endCursor"]


def organizations():
    """(organisation logins of the token's user, sorted, skip reason). Does not exit: without them the run
    lists the login alone. One retry, like graphql()."""
    args = ["api", "graphql", "-f", f"query={ORGANIZATIONS}"]
    result = gh(*args)
    if result.returncode != 0:
        time.sleep(2)
        result = gh(*args)
    if result.returncode != 0:
        return [], clean(result.stderr or result.stdout) or "unknown error"
    try:
        page = json.loads(result.stdout)["data"]["viewer"]["organizations"]
        logins = sorted((node["login"] for node in page["nodes"]), key=str.lower)
    except (ValueError, KeyError, TypeError):
        return [], "unreadable answer"
    if page["pageInfo"]["hasNextPage"]:
        return logins, f"only the first 100 of {page['totalCount']} organisations were read"
    return logins, None


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


RED = ("failure", "timed_out", "startup_failure")
RUN_FIELDS = ('[.workflow_runs[] | select(.status == "completed") | {conclusion, event, path, name, '
              'title: .display_title, sha: .head_sha, created: .created_at, url: .html_url, '
              'repo: .head_repository.full_name}]')


def dependabot_job(title):
    """The update job of a Dependabot run title: "npm_and_yarn in /app - Update #123" and
    "npm_and_yarn in /app for lodash - Update #123" give "npm_and_yarn in /app", "Graph Update: uv in /
    #456" gives "Graph Update: uv in /"."""
    match = re.fullmatch(r"(Graph Update: )?(\S+ in \S+(?:, \S+)*)(?: for .*| - Update #\d+| #\d+)?", title.strip())
    return (match.group(1) or "") + match.group(2) if match else ""


def workflow_runs(repo, branch, head):
    """(rows, skip reason). The newest completed run of each workflow, or of each Dependabot update job,
    on the default branch decides: a row for it when it is red, none when it is green. A push run of the
    head commit decides but gives no row, except a startup_failure: the CI row covers the others."""
    result = gh("api", f"repos/{repo}/actions/runs?branch={urllib.parse.quote(branch, safe='')}&per_page=100",
                "-q", RUN_FIELDS)
    if result.returncode != 0:
        return [], result.stderr.strip().splitlines()[0] if result.stderr.strip() else "unknown error"
    try:
        runs = json.loads(result.stdout or "[]")
    except ValueError:
        return [], "unreadable answer"
    runs.sort(key=lambda run: run["created"], reverse=True)
    seen, rows = set(), []
    for run in runs:
        if run["repo"] != repo:
            continue
        dependabot = run["path"].startswith("dynamic/dependabot/")
        job = dependabot_job(run["title"]) if dependabot else ""
        key = ("dependabot", job) if job else run["path"]
        if key in seen:
            continue
        seen.add(key)
        if run["conclusion"] not in RED or (run["event"] == "push" and run["sha"] == head
                                                  and run["conclusion"] != "startup_failure"):
            continue
        if job:
            ref, title = job, f"Dependabot {job} failed"
        else:
            ref, title = run["path"].rsplit("/", 1)[-1], f"{run['name']} failed on {branch}"
        rows.append((2, "RUN", repo, ref, run["conclusion"], run["created"], title, run["url"], "-", "-"))
    return rows, None


def ignore_rules():
    if not IGNORE.is_file():
        return []
    lines = (line.strip() for line in IGNORE.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def ignored(rules, repo, ref):
    return any(fnmatch.fnmatchcase(repo, rule) or fnmatch.fnmatchcase(repo + ref, rule) for rule in rules)


def clean(text):
    return " ".join(str(text).split())


def collect(owner, only=None, organisations=(), notes=(), default=False):
    """The list for all of `owner`'s repositories, or, with `only` ("owner/name"), for that one. With
    `organisations`, their repositories are listed too; `notes` are status lines to print (SKIPPED);
    `default` marks the default run, whose empty result points to /inbox <org>."""
    rows, skipped, rules = [], list(notes), ignore_rules()
    groups = []  # (owner, its repositories, search): only owners that were read
    if only:
        groups.append((owner, list(graphql(REPO, {}, owner=owner, name=only.partition("/")[2])),
                       f"repo:{only} is:open"))
        scope = only
    else:
        scope = owner
        for name in [owner, *organisations]:
            try:
                found = list(graphql(REPOS, {}, owner=name))
            except SystemExit as error:  # the login's own list stays fatal, an organisation's does not
                if name == owner:
                    raise
                skipped.append(f"# SKIPPED repositories {name}/*: {clean(error.code).removeprefix('collect.py: ')}")
                continue
            groups.append((name, found, f"user:{name} is:open archived:false"))
    repos = [repo for _, found, _ in groups for repo in found]
    owners = ",".join(name for name, _, _ in groups)

    for name, found, _ in groups:
        unconfirmed = sum(1 for repo in found if not repo["vulnerabilityAlerts"]["totalCount"]
                          and not repo["viewerCanAdminister"]
                          and not ignored(rules, repo["nameWithOwner"], "dependabot"))
        if unconfirmed:  # no alerts reported, but this token could not have read any
            skipped.append(f"# SKIPPED dependabot {only or name + '/*'}: {unconfirmed} repositories report no "
                           "alerts, but without admin permission on them that cannot be told apart from unreadable")
        disabled = sum(1 for repo in found if repo["viewerCanAdminister"]
                       and not repo["hasVulnerabilityAlertsEnabled"]
                       and not ignored(rules, repo["nameWithOwner"], "dependabot"))
        if disabled:  # alerts switched off: the zero alerts it reports is not "no alerts"
            skipped.append(f"# SKIPPED dependabot {only or name + '/*'}: {disabled} repositories have "
                           "Dependabot alerts disabled")
    for repo in repos:
        name = repo["nameWithOwner"]
        alerts = repo["vulnerabilityAlerts"]
        if alerts["totalCount"]:
            nodes = [node for node in alerts["nodes"] if node]
            found = [node["securityVulnerability"]["severity"] for node in nodes]
            worst = max(found, key=SEVERITIES.index).lower() if found else "unknown"
            unpatched = sum(not node["securityVulnerability"]["firstPatchedVersion"] for node in nodes)
            state = f"{alerts['totalCount']} open, max {worst}" + (f", {unpatched} without patch" if unpatched else "")
            rows.append((1, "SECURITY", name, "dependabot", state,
                         max((node["createdAt"] for node in nodes), default=""),
                         "Dependabot alerts", f"{repo['url']}/security/dependabot", "-", "-"))
        branch = repo["defaultBranchRef"]
        commit = (branch or {}).get("target") or {}
        rollup = commit.get("statusCheckRollup")
        if rollup and rollup["state"] in ("FAILURE", "ERROR"):
            rows.append((2, "CI", name, branch["name"], rollup["state"].lower(), commit["committedDate"],
                         f"checks failed on {branch['name']}", f"{repo['url']}/actions", "-", "-"))

    scanned = [repo for repo in repos if not ignored(rules, repo["nameWithOwner"], "code-scanning")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(code_scanning, (repo["nameWithOwner"] for repo in scanned))
        for repo, (count, newest, reason) in zip(scanned, results):
            if reason:
                skipped.append(f"# SKIPPED code-scanning {repo['nameWithOwner']}: {reason}")
            elif count:
                shown = "100+" if count == 100 else str(count)
                rows.append((1, "SECURITY", repo["nameWithOwner"], "code-scanning", f"{shown} open", newest,
                             "Code scanning alerts", f"{repo['url']}/security/code-scanning", "-", "-"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        checked = [repo for repo in repos if repo["defaultBranchRef"]
                   and not ignored(rules, repo["nameWithOwner"], "runs")]
        results = pool.map(lambda repo: workflow_runs(
            repo["nameWithOwner"], repo["defaultBranchRef"]["name"],
            (repo["defaultBranchRef"].get("target") or {}).get("oid")), checked)
        for repo, (found, reason) in zip(checked, results):
            if reason:
                skipped.append(f"# SKIPPED runs {repo['nameWithOwner']}: {reason}")
            rows.extend(found)

    known = {repo["nameWithOwner"] for repo in repos}
    items = []
    for name, _, search in groups:
        totals, found = {}, []
        try:
            found.extend(graphql(ITEMS, totals, q=search))
        except SystemExit as error:
            if name == owner:
                raise
            skipped.append(f"# SKIPPED search {name}/*: {clean(error.code).removeprefix('collect.py: ')}")
            continue
        items.extend(found)
        received = len(found)
        if (totals.get("count") or 0) > received:
            skipped.append(f"# SKIPPED search {only or name + '/*'}: only {received} of {totals['count']} open "
                           f"issues and pull requests were read, the search stops at {SEARCH_LIMIT}")
    for item in items:
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
            assignees = closing = "-"
        else:
            labels = [label["name"] for label in item["labels"]["nodes"]]
            state = ",".join((["bot"] if author.get("__typename") == "Bot" else []) + labels) or "-"
            prio, kind = 5, "ISSUE"
            assignees = ",".join(a["login"] for a in item["assignees"]["nodes"]) or "-"
            closing = ",".join(
                f"#{pr['number']}" if pr["repository"]["nameWithOwner"] == name
                else f"{pr['repository']['nameWithOwner']}#{pr['number']}"
                for pr in item["closedByPullRequestsReferences"]["nodes"]) or "-"
        rows.append((prio, kind, name, ref, state, item["updatedAt"], item["title"], item["url"],
                     assignees, closing))

    kept = [row for row in rows if not ignored(rules, row[2], row[3])]
    kept.sort(key=lambda row: row[5], reverse=True)
    kept.sort(key=lambda row: [kind for kind, _ in KINDS].index(row[1]))  # CI before RUN: both are prio 2
    kept.sort(key=lambda row: row[0])
    header = (f"# owner={owner} owners={owners} scope={scope} generated={int(time.time())} "
              f"repos={len(repos)} ignored={len(rows) - len(kept)}")
    notice = []
    if not repos:
        notice = [f"# NOTICE no repositories found for {owners}: "
                  + ("none of them has a" if "," in owners else "it has no")
                  + " repository that is neither archived nor a fork"
                  + ("; list an organisation with /inbox <org>" if default else "")]
    return "\n".join([header, *notice, *skipped, *("\t".join(clean(field) for field in row) for row in kept)]) + "\n"


def refresh(owner, background=False):
    """Collect for `owner` (None: the default owner) and, for the default run only, write its cache. One
    refresh at a time: a background run gives way to a running one, a foreground run waits for it and then
    reads live. The default run covers the organisations of the `gh` login too, when the owner is that login."""
    DIR.mkdir(parents=True, exist_ok=True)
    with open(DIR / "refresh.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if background else 0))
        except BlockingIOError:
            return ""
        default = owner is None
        if default:
            owner = os.environ.get("INBOX_OWNER")
        login, notes = None, []
        if default or not owner:
            result = gh("api", "user", "-q", ".login")
            if result.returncode != 0:
                time.sleep(2)
                result = gh("api", "user", "-q", ".login")
            if result.returncode == 0:
                login = result.stdout.strip()
            elif not owner:
                sys.exit(f"collect.py: cannot determine the owner: {result.stderr.strip()}")
            else:  # $INBOX_OWNER is set: whether it is the login, and so whether organisations apply, is unknown
                notes.append(f"# SKIPPED organizations {owner}/*: cannot read the gh login: "
                             f"{clean(result.stderr) or 'unknown error'}")
        owner = owner or login
        organisations = []
        if default and login and owner.lower() == login.lower():
            found, reason = organizations()
            if reason:
                notes.append(f"# SKIPPED organizations {owner}/*: {reason}")
            rules = ignore_rules()
            organisations = [org for org in found if org.lower() != owner.lower() and not ignored(rules, f"{org}/*", "")]
        text = collect(owner, organisations=organisations, notes=notes, default=default)
        if default:
            tmp = DIR / f"inbox.{os.getpid()}.tmp"
            tmp.write_text(text)
            tmp.replace(cache_file(owner))
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
        extra = len(head.get("owners", head["owner"]).split(",")) - 1  # a cache file from before has no owners
        name = head["owner"] + (f" +{extra} org{'s' if extra > 1 else ''}" if extra > 0 else "")
        line = (f"inbox ({name}): "
                + ("no repositories found - run /inbox <org> for an organisation" if head["repos"] == "0" else counts)
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


NO_REPOSITORY = ("not a git repository", "no git remotes found", "none of the git remotes")  # gh's own words


def local_repository():
    """The repository `gh repo view` resolves for the current directory, as GitHub names it. Exit 2 only
    when gh says there is none (no work tree, no remote, no remote on a GitHub host); a failed call
    (token, network, timeout, no gh) exits 1 like every other failure."""
    result = gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    message = result.stderr.strip()
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    if any(phrase in message for phrase in NO_REPOSITORY):
        print(f"collect.py: not a checkout of a GitHub repository: {message.splitlines()[0]}", file=sys.stderr)
        sys.exit(2)
    sys.exit(f"collect.py: cannot resolve the current repository: {clean(message) or 'gh printed nothing'}")


def single(repo):
    """One repository's list, live. Writes no cache and takes no lock: the owner-wide cache stays what
    the SessionStart summary reads."""
    if repo.count("/") != 1 or not all(repo.split("/")):
        sys.exit(f"collect.py: not owner/name: {repo}")
    return collect(repo.partition("/")[0], only=repo)


def main():
    args = sys.argv[1:]
    if args == ["--summary"]:
        try:
            summary()
        except Exception as error:  # a broken inbox must never break a session start
            print(json.dumps({"systemMessage": f"inbox: summary failed: {error}"}))
    elif args == ["--background"]:
        background()
    elif args == ["--here"]:
        sys.stdout.write(single(local_repository()))
    elif len(args) == 2 and args[0] == "--repo":
        sys.stdout.write(single(args[1]))
    elif len(args) == 2 and args[0] == "--owner":
        sys.stdout.write(refresh(args[1]))
    elif not args:
        sys.stdout.write(refresh(None))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
