#!/usr/bin/env python3
"""One row per issue the planner ran on: question rounds, plans, outcome, review rounds and findings.
usage: issue-stats.py --since YYYY-MM-DD [--until YYYY-MM-DD] [--owner <login>] [--account <login>]
  --since    first date to read (required: nothing is stored between runs, so an open range would read
             every issue the search returns)
  --until    last date, default today (UTC)
  --owner    whose repositories are searched, default the login `gh` works as
  --account  whose marker comments count as the planner's and worker's, default the login `gh` works as
             (the `ACCOUNT` of plugins/work-issue/skills/work-issue/scripts/issue-context.py)
Read-only on GitHub (needs gh auth), stdlib only. Nothing is cached: every run reads the issues, their closing
PRs and all their comments live (every comment list is read to its last page).
An issue is counted when its first plan or questions comment by --account lies in --since..--until (the date
of its createdAt, UTC). That is the denominator: every issue a planner ran on, not only the merged ones. The
candidates come from one search `is:issue user:<owner> updated:>=<since>`.
Markers are read on the first line of comments by --account only (anyone can comment on an issue or a PR):
`<!-- work-issue:plan ... -->`, `<!-- work-issue:questions -->`, `<!-- work-issue:finish-stopped ... -->`,
`<!-- work-issue:closed -->` on the issue and `<!-- work-issue:stopped ... -->` on a PR, in the formats that
issue-context.py reads. A verdict is read as scripts/review-rounds.py reads it (loaded by path beside this
file: its parse(), HEADING and TRUSTED): first line exactly the heading, author an owner, member or
collaborator, rounds in creation order.
Outcome, one value per issue, the first that applies (the closing PRs are those of the issue by --account,
GraphQL closedByPullRequestsReferences with closed ones included):
  merged           a closing PR is merged (the newest one is the outcome PR)
  open             a closing PR is open; reason = reason of its newest stop marker, else `-`
  finish-stopped   a finish marker on the issue; reason = the reason of the newest one
  closed-unmerged  only closed, unmerged closing PRs (the newest one is the outcome PR)
  planner-closed   a `work-issue:closed` marker on the issue; reason = the issue's stateReason, lowercased
  none             no worker trace
Output, tab separated; lines starting with `#` are the header and notes:
  ISSUE     <owner/repo> <n> <open|closed> <first planner comment date> <question rounds> <plans> <outcome>
            <reason or -> <outcome PR or -> <rounds> <findings>
            findings = per round, in order, `blocking/fixinpr/followup`, comma separated, `?/?/?` for an
            unparsed verdict; `0` and `-` without an outcome PR or without verdicts
  UNPARSED  <owner/repo> <n> <pr> <round>
  QROUNDS   <k> <issues>                   issues with exactly k question rounds (k = 0: ran straight through)
  OUTCOME   <kind> <issues>
  ROUNDS    <k> <issues>                   issues whose outcome PR has exactly k verdicts (k >= 1)
  SUMMARY   <name> <value>                 issues, without_questions, merged, merge_rate, mean_question_rounds,
            mean_rounds (issues with a verdict), mean_blocking, mean_fixinpr, mean_followup (sum over the
            rounds of an issue, averaged over the issues with at least one parsed round; unparsed rounds are
            left out); `-` where the denominator is 0. A PR that closes several issues is counted for each
  # SKIPPED <source>: <reason>             something that was not read, or only in part
Exit 0 on a completed run (also when nothing was found), 2 on invalid arguments, 1 when gh fails or
review-rounds.py cannot be loaded."""
import argparse, datetime, importlib.util, json, re, subprocess, sys
from collections import Counter
from pathlib import Path

PLAN_MARKER = re.compile(r"<!--\s*work-issue:plan\s+base=([0-9a-f]{7,40})(?:\s+adopts=(\d+))?\s*-->")
QUESTIONS_MARKER = re.compile(r"<!--\s*work-issue:questions\s*-->")
FINISH_MARKER = re.compile(
    r"<!--\s*work-issue:finish-stopped\s+branch=(\S+)\s+head=([0-9a-f]{7,40})\s+plan=(\d+)\s+attempt=(\d+)\s+reason=([a-z-]+)\s*-->"
)
STOP_MARKER = re.compile(r"<!--\s*work-issue:stopped\s+head=([0-9a-f]{7,40})\s+reason=([a-z-]+)\s*-->")
CLOSED_MARKER = re.compile(r"<!--\s*work-issue:closed\s*-->")

COMMENTS = "pageInfo { hasNextPage endCursor } nodes { author { login } authorAssociation createdAt body }"
SEARCH = """query($q: String!, $after: String) { search(query: $q, type: ISSUE, first: 25, after: $after) {
  issueCount pageInfo { hasNextPage endCursor }
  nodes { ... on Issue { id number state stateReason repository { nameWithOwner }
    comments(first: 100) { %s }
    closedByPullRequestsReferences(first: 10, includeClosedPrs: true) { pageInfo { hasNextPage }
      nodes { id number state merged createdAt author { login } repository { nameWithOwner }
        comments(first: 100) { %s } } } } } } }""" % (COMMENTS, COMMENTS)
MORE = """query($id: ID!, $after: String) { node(id: $id) { ... on Issue {
  comments(first: 100, after: $after) { %s } } ... on PullRequest {
  comments(first: 100, after: $after) { %s } } } }""" % (COMMENTS, COMMENTS)


def die(msg, code=1):
    print(f"issue-stats.py: {msg}", file=sys.stderr)
    sys.exit(code)


def load_review_rounds():
    path = Path(__file__).with_name("review-rounds.py")
    try:
        spec = importlib.util.spec_from_file_location("review_rounds", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in ("parse", "HEADING", "TRUSTED"):
            getattr(mod, name)
    except Exception as e:
        die(f"cannot load {path}: {e}")
    return mod


def gh(args):
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
    except FileNotFoundError:
        die("gh is not installed")
    if r.returncode != 0:
        die(f"gh {' '.join(args[:2])} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def graphql(query, **variables):
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        if v is not None:
            args += ["-f", f"{k}={v}"]
    out = json.loads(gh(args))
    if out.get("errors"):
        die(f"graphql: {out['errors'][0].get('message', out['errors'])}")
    return out["data"]


def all_comments(node):
    """Every comment of an issue or PR node, the pages after the first read to the end."""
    com = node["comments"]
    nodes = list(com["nodes"])
    page = com["pageInfo"]
    while page["hasNextPage"]:
        more = graphql(MORE, id=node["id"], after=page["endCursor"])["node"]["comments"]
        nodes += more["nodes"]
        page = more["pageInfo"]
    return sorted(nodes, key=lambda c: c["createdAt"])


def first_line(c):
    """First line of a verdict, as review-rounds.py reads it."""
    return c["body"].split("\n", 1)[0].rstrip()


def marker_line(c):
    """First line of a marker comment, as issue-context.py reads it (leading whitespace skipped, stripped)."""
    return c["body"].lstrip().split("\n", 1)[0].strip()


def by(c, account):
    return ((c.get("author") or {}).get("login") or "").lower() == account.lower()


def mean(values):
    return "-" if not values else f"{sum(values) / len(values):.2f}"


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until")
    ap.add_argument("--owner")
    ap.add_argument("--account")
    a = ap.parse_args()
    try:
        since = datetime.date.fromisoformat(a.since)
        until = datetime.date.fromisoformat(a.until) if a.until else datetime.datetime.now(datetime.timezone.utc).date()
    except ValueError as e:
        die(f"invalid date: {e}", 2)
    if since > until:
        die("--since is after --until", 2)
    login = None
    if not a.owner or not a.account:
        login = gh(["api", "user", "-q", ".login"]).strip()
    owner, account = a.owner or login, a.account or login
    for what, value in (("owner", owner), ("account", account)):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", value):
            die(f"invalid {what}: {value!r}", 2)
    rr = load_review_rounds()

    # the GraphQL search answers an unknown or invisible owner with 0 results and no error
    gh(["api", f"users/{owner}", "-q", ".login"])
    q = f"is:issue user:{owner} updated:>={since}"
    issues, after, count = [], None, 0
    while True:
        s = graphql(SEARCH, q=q, after=after)["search"]
        count = s["issueCount"]
        issues += [n for n in s["nodes"] if n]
        if not s["pageInfo"]["hasNextPage"]:
            break
        after = s["pageInfo"]["endCursor"]
    skipped = []
    if count > len(issues):
        skipped.append(f"# SKIPPED search: {count} issues match, {len(issues)} were returned (GitHub search returns at most 1000); narrow the range")

    rows, unparsed = [], []
    for issue in sorted(issues, key=lambda i: (i["repository"]["nameWithOwner"], i["number"])):
        repo, num = issue["repository"]["nameWithOwner"], issue["number"]
        comments = [c for c in all_comments(issue) if by(c, account)]
        planner = [c for c in comments if PLAN_MARKER.fullmatch(marker_line(c)) or QUESTIONS_MARKER.fullmatch(marker_line(c))]
        if not planner:
            continue
        started = planner[0]["createdAt"][:10]
        if not (since <= datetime.date.fromisoformat(started) <= until):
            continue
        qrounds = sum(1 for c in comments if QUESTIONS_MARKER.fullmatch(marker_line(c)))
        plans = sum(1 for c in comments if PLAN_MARKER.fullmatch(marker_line(c)))
        refs = issue["closedByPullRequestsReferences"]
        if refs["pageInfo"]["hasNextPage"]:
            skipped.append(f"# SKIPPED closing-prs: {repo}#{num} has more than 10 closing PRs, the first 10 were read")
        prs = sorted((p for p in refs["nodes"] if p and ((p["author"] or {}).get("login") or "").lower() == account.lower()),
                     key=lambda p: p["createdAt"])
        finish = [m for m in (FINISH_MARKER.fullmatch(marker_line(c)) for c in comments) if m]
        closed = any(CLOSED_MARKER.fullmatch(marker_line(c)) for c in comments)
        merged = [p for p in prs if p["merged"]]
        opened = [p for p in prs if p["state"] == "OPEN"]
        unmerged = [p for p in prs if p["state"] == "CLOSED" and not p["merged"]]
        outcome_pr, reason = None, "-"
        pr_comments = None
        if merged:
            outcome, outcome_pr = "merged", merged[-1]
        elif opened:
            outcome, outcome_pr = "open", opened[-1]
            pr_comments = all_comments(outcome_pr)
            stops = [m for m in (STOP_MARKER.fullmatch(marker_line(c)) for c in pr_comments if by(c, account)) if m]
            reason = stops[-1].group(2) if stops else "-"
        elif finish:
            outcome, reason = "finish-stopped", finish[-1].group(5)
        elif unmerged:
            outcome, outcome_pr = "closed-unmerged", unmerged[-1]
        elif closed:
            outcome, reason = "planner-closed", (issue["stateReason"] or "-").lower()
        else:
            outcome = "none"
        found = []
        if outcome_pr is not None:
            pr_comments = pr_comments if pr_comments is not None else all_comments(outcome_pr)
            verdicts = [c for c in pr_comments if c["authorAssociation"] in rr.TRUSTED and first_line(c) == rr.HEADING]
            found = [rr.parse(c["body"]) for c in verdicts]
            for i, r in enumerate(found, 1):
                if r is None:
                    unparsed.append((repo, num, outcome_pr["number"], i))
        rows.append(dict(repo=repo, num=num, state=issue["state"].lower(), started=started, qrounds=qrounds,
                         plans=plans, outcome=outcome, reason=reason,
                         pr=str(outcome_pr["number"]) if outcome_pr else "-", found=found))

    print(f"# owner {owner}, account {account}, planner comments {since}..{until}, {len(rows)} issues")
    print("# denominator: every issue with a plan or questions comment by the account in the range, merged or not")
    print("# means over rounds leave unparsed verdicts out; the model of a round and a re-check of an unchanged head are not recorded in the comments")
    for line in skipped:
        print(line)
    for r in rows:
        fs = ",".join("?/?/?" if x is None else "/".join(map(str, x)) for x in r["found"]) or "-"
        print("\t".join(["ISSUE", r["repo"], str(r["num"]), r["state"], r["started"], str(r["qrounds"]), str(r["plans"]),
                         r["outcome"], r["reason"], r["pr"], str(len(r["found"])), fs]))
    for row in unparsed:
        print("\t".join(["UNPARSED", row[0], *map(str, row[1:])]))
    qd = Counter(r["qrounds"] for r in rows)
    for k in sorted(qd):
        print(f"QROUNDS\t{k}\t{qd[k]}")
    od = Counter(r["outcome"] for r in rows)
    for kind in sorted(od):
        print(f"OUTCOME\t{kind}\t{od[kind]}")
    rd = Counter(len(r["found"]) for r in rows if r["found"])
    for k in sorted(rd):
        print(f"ROUNDS\t{k}\t{rd[k]}")
    n = len(rows)
    withv = [r for r in rows if r["found"]]
    parsed = [p for p in ([x for x in r["found"] if x is not None] for r in withv) if p]
    summary = [
        ("issues", str(n)),
        ("without_questions", str(qd.get(0, 0))),
        ("merged", str(od.get("merged", 0))),
        ("merge_rate", "-" if not n else f"{od.get('merged', 0) / n:.2f}"),
        ("mean_question_rounds", mean([r["qrounds"] for r in rows])),
        ("mean_rounds", mean([len(r["found"]) for r in withv])),
        ("mean_blocking", mean([sum(x[0] for x in p) for p in parsed])),
        ("mean_fixinpr", mean([sum(x[1] for x in p) for p in parsed])),
        ("mean_followup", mean([sum(x[2] for x in p) for p in parsed])),
    ]
    for name, value in summary:
        print(f"SUMMARY\t{name}\t{value}")


if __name__ == "__main__":
    main()
