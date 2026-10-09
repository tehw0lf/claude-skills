#!/usr/bin/env python3
"""Count the review rounds of merged PRs from their verdict comments.
usage: review-rounds.py --since YYYY-MM-DD [--until YYYY-MM-DD] [--owner <login>]
  --since   first merge date to read (required: nothing is stored between runs, so an open range
            would read every merged PR the search returns)
  --until   last merge date, default today (UTC)
  --owner   whose repositories are searched, default the login `gh` works as
Read-only on GitHub (needs gh auth). Nothing is cached: every run reads the PRs and their comments live.
A verdict is an issue comment on the PR whose first line is exactly `## Independent local review`,
written by an owner, member or collaborator of the repository (anyone can comment on a PR, so any
other comment is ignored). The rounds of a PR are its verdicts in creation order. Both layouts of
plugins/pr-review/agents/pr-reviewer.md are read: the folded one (count line
`**Blocking: <n> · Fix in PR: <n> · Follow-up: <n>**`) and the flat one before it (unindented numbered or bulleted
items under `### Blocking`, `### Fix in PR`, `### Follow-up`; `none` counts 0). A verdict that matches
neither is reported as UNPARSED and counted as `?/?/?`, never guessed.
Output, tab separated; lines starting with `#` are the header and notes:
  PR        <owner/repo> <number> <rounds> <findings> <files> <additions> <deletions>
            findings = per round, in order, `blocking/fixinpr/followup`, comma separated
  NOVERDICT <owner/repo> <number> <files> <additions> <deletions>     merged PR without any verdict
  UNPARSED  <owner/repo> <number> <round>
  ROUNDS    <k> <prs>                      PRs with exactly k rounds
  ROUND     <i> <prs> <with_blocking_or_fix> <with_followup>   PRs with a round i; of those, how many
            had blocking or fix-in-PR / follow-up findings in it (unparsed rounds are in neither)
  LIMIT     <k> <stopped>                  PRs that needed more than k rounds, i.e. a limit of k
            would have stopped them (for k = 1 .. the largest round count seen)
  # SKIPPED <source>: <reason>             something that was not read, or only in part
Exit 0 on a completed run (also when nothing was found), 2 on invalid arguments, 1 when gh fails.
Why: the model of a round (round 1 runs on a different model than later rounds) and a re-check of an
unchanged head are not recorded in the comments, so the counts are the verdicts as posted."""
import argparse, datetime, json, re, subprocess, sys
from collections import Counter

HEADING = "## Independent local review"
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
COUNT_LINE = re.compile(r"^\*\*Blocking: (\d+) · Fix in PR: (\d+) · Follow-up: (\d+)\*\*\s*$", re.M)
FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.M | re.S)
SECTIONS = (("Blocking", "Blocking"), ("Fix in PR", "Fix in PR"), ("Follow-up", "Follow-up"))

SEARCH = """query($q: String!, $after: String) { search(query: $q, type: ISSUE, first: 25, after: $after) {
  issueCount pageInfo { hasNextPage endCursor }
  nodes { ... on PullRequest { id number repository { nameWithOwner } additions deletions changedFiles
    comments(first: 100) { pageInfo { hasNextPage endCursor } nodes { authorAssociation createdAt body } } } } } }"""
MORE = """query($id: ID!, $after: String) { node(id: $id) { ... on PullRequest {
  comments(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes { authorAssociation createdAt body } } } } }"""


def die(msg, code=1):
    print(f"review-rounds.py: {msg}", file=sys.stderr)
    sys.exit(code)


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


def parse(body):
    """(blocking, fixinpr, followup) of one verdict body, or None when neither layout matches."""
    m = COUNT_LINE.search(body)
    if m:
        return tuple(int(x) for x in m.groups())
    text = FENCE.sub("", body)
    counts = []
    for name, _ in SECTIONS:
        h = re.search(rf"^### {re.escape(name)}[ \t]*$", text, re.M)
        if h:
            rest = text[h.end():]
            nxt = re.search(r"^###? ", rest, re.M)
            sect = rest[:nxt.start()] if nxt else rest
        else:
            # transitional comments carry a plain `**Blocking:** none` line instead of a heading
            line = re.search(rf"^\*\*{re.escape(name)}:\*\*[ \t]*(.*)$", text, re.M)
            if not line:
                return None
            sect = line.group(1)
        items = len(re.findall(r"^(?:\d+\.|[-*]) ", sect, re.M))
        if items:
            counts.append(items)
        elif re.match(r"\s*none\b", sect, re.I):
            counts.append(0)
        else:
            return None
    return tuple(counts)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until")
    ap.add_argument("--owner")
    a = ap.parse_args()
    try:
        since = datetime.date.fromisoformat(a.since)
        until = datetime.date.fromisoformat(a.until) if a.until else datetime.datetime.now(datetime.timezone.utc).date()
    except ValueError as e:
        die(f"invalid date: {e}", 2)
    if since > until:
        die("--since is after --until", 2)
    owner = a.owner or gh(["api", "user", "-q", ".login"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner):
        die(f"invalid owner: {owner!r}", 2)

    # the GraphQL search answers an unknown or invisible owner with 0 results and no error
    gh(["api", f"users/{owner}", "-q", ".login"])
    q = f"is:pr is:merged user:{owner} merged:{since}..{until}"
    prs, after, count = [], None, 0
    while True:
        s = graphql(SEARCH, q=q, after=after)["search"]
        count = s["issueCount"]
        prs += [n for n in s["nodes"] if n]
        if not s["pageInfo"]["hasNextPage"]:
            break
        after = s["pageInfo"]["endCursor"]
    skipped = []
    if count > len(prs):
        skipped.append(f"# SKIPPED search: {count} merged PRs match, {len(prs)} were returned (GitHub search returns at most 1000); narrow the range")

    print(f"# owner {owner}, merged {since}..{until}, {len(prs)} merged PRs")
    print("# the model of a round and a re-check of an unchanged head are not recorded in the comments; rounds are the verdict comments as posted")
    print("# ROUND counts: unparsed rounds are in prs but in neither of the other two columns")
    for line in skipped:
        print(line)

    rows, noverdict, unparsed, per_round = [], [], [], {}
    for pr in sorted(prs, key=lambda p: (p["repository"]["nameWithOwner"], p["number"])):
        repo, num = pr["repository"]["nameWithOwner"], pr["number"]
        com = pr["comments"]
        nodes = list(com["nodes"])
        page = com["pageInfo"]
        while page["hasNextPage"]:
            more = graphql(MORE, id=pr["id"], after=page["endCursor"])["node"]["comments"]
            nodes += more["nodes"]
            page = more["pageInfo"]
        verdicts = sorted((c for c in nodes
                           if c["authorAssociation"] in TRUSTED and c["body"].split("\n", 1)[0].rstrip() == HEADING),
                          key=lambda c: c["createdAt"])
        size = (pr["changedFiles"], pr["additions"], pr["deletions"])
        if not verdicts:
            noverdict.append((repo, num, *size))
            continue
        found = []
        for i, c in enumerate(verdicts, 1):
            r = parse(c["body"])
            found.append(r)
            if r is None:
                unparsed.append((repo, num, i))
            slot = per_round.setdefault(i, [0, 0, 0])
            slot[0] += 1
            if r is not None:
                slot[1] += bool(r[0] or r[1])
                slot[2] += bool(r[2])
        rows.append((repo, num, found, size))

    for repo, num, found, size in rows:
        fs = ",".join("?/?/?" if r is None else "/".join(map(str, r)) for r in found)
        print("\t".join(["PR", repo, str(num), str(len(found)), fs, *map(str, size)]))
    for row in noverdict:
        print("\t".join(["NOVERDICT", row[0], *map(str, row[1:])]))
    for row in unparsed:
        print("\t".join(["UNPARSED", row[0], *map(str, row[1:])]))
    dist = Counter(len(f) for _, _, f, _ in rows)
    for k in sorted(dist):
        print(f"ROUNDS\t{k}\t{dist[k]}")
    for i in sorted(per_round):
        print("ROUND\t" + "\t".join(map(str, (i, *per_round[i]))))
    for k in range(1, max(dist, default=0) + 1):
        print(f"LIMIT\t{k}\t{sum(v for r, v in dist.items() if r > k)}")


if __name__ == "__main__":
    main()
