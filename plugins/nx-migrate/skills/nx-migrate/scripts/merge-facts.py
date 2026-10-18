#!/usr/bin/env python3
"""List the verdict and the base-branch changes of a pull request. Read-only on GitHub.

usage: merge-facts.py <owner/repo> <pr>

Needs `gh` with authentication. The verdict is the latest issue comment on the PR whose first line is
exactly `## Independent local review` and whose author is the account `gh` works as (anyone can comment
on a PR, so the author is part of the test; a comment that only quotes the heading does not start with it).
Comments are read through the API: `gh pr view --comments`, run non-interactively, leaves out minimized
comments without saying so.

A base change is a `BaseRefChangedEvent` in the PR timeline (GraphQL `timelineItems`, item type
`BASE_REF_CHANGED_EVENT`) with a `createdAt` at or after the verdict's `created_at`. The verdict binds only
the head, so a PR retargeted after its review keeps a verdict made against the diff of another base.
The reviewer reads the base at the start of the review and again just before writing the verdict, and
repeats its base-dependent checks when the base changed. A retarget between that last read and the posting
of the verdict is older than the verdict, so it is not listed here.

Output, one tab-separated line per fact on stdout:
  VERDICT  <comment id>  <created_at>  <sha of the `Reviewed head:` line, - when absent>
  HEAD     <sha>                       the current head of the PR
  BASE     <created_at>  <previous base branch>  <current base branch>
             one line per change of the PR's base branch at or after the verdict.
  LASTBASE <created_at>
             the newest change of the PR's base branch of the whole timeline, whatever the verdict: no line
             when the base never changed or the base changes were not read. Unlike BASE it is not bound to
             the verdict, because after the round a BASE line causes, the verdict is newer than the change and
             no BASE line is printed any more. The caller compares it with the start time of the required checks: a
             check that started before it ran against the merge result of the old base (a `pull_request`
             workflow without the `edited` activity type does not run again on a retarget, and the head SHA
             does not change). Printed after the BASE lines, also without a verdict.
  SKIPPED  <source>  <reason>
             a source that could not be read: account, head, issue comments,
             base changes (also when the timeline holds more events than were returned), verdict. With
             no verdict comment there is no reference time: `SKIPPED verdict ...` and no BASE
             line.
Exit codes: 0 every source was read (with or without BASE lines), 1 at least one SKIPPED line,
2 usage error. BASE and LASTBASE lines do not change the exit code: the caller acts on them.
"""
import json
import re
import subprocess
import sys

HEADING = "## Independent local review"

BASE_QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      timelineItems(last: 100, itemTypes: [BASE_REF_CHANGED_EVENT]) {
        filteredCount
        nodes { ... on BaseRefChangedEvent { createdAt previousRefName currentRefName } }
      }
    }
  }
}"""


def clean(text):
    """One line, no tabs: the output format is tab-separated and line-oriented."""
    return re.sub(r"[\t\r\n]+", " ", str(text)).strip()


def gh(args):
    """Run gh; return (stdout, None) or (None, reason)."""
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True)
    except OSError as e:
        return None, clean(e.strerror or e)
    if p.returncode != 0:
        return None, clean(p.stderr) or "exit %d" % p.returncode
    return p.stdout, None


def pages(path):
    """All items of a paginated list endpoint; (items, None) or (None, reason)."""
    out, err = gh(["api", "--paginate", "--slurp", path])
    if err:
        return None, err
    try:
        items = []
        for page in json.loads(out):
            items.extend(page)
        return items, None
    except (ValueError, TypeError) as e:
        return None, clean(e)


def main():
    if len(sys.argv) != 3 or "/" not in sys.argv[1] or not sys.argv[2].isdigit():
        print(__doc__.split("\n")[2], file=sys.stderr)
        return 2
    repo, pr = sys.argv[1], sys.argv[2]
    base = "repos/%s" % repo
    skipped = []

    def skip(source, reason):
        skipped.append(source)
        print("SKIPPED\t%s\t%s" % (source, clean(reason)))

    login, err = gh(["api", "user", "-q", ".login"])
    login = login.strip() if login else None
    if err or not login:
        skip("account", err or "empty login")
    head, err = gh(["api", "%s/pulls/%s" % (base, pr), "-q", ".head.sha"])
    head = head.strip() if head else None
    if err or not head:
        skip("head", err or "empty head")

    issue_comments, err = pages("%s/issues/%s/comments" % (base, pr))
    if err:
        skip("issue comments", err)

    base_events = None
    owner, _, name = repo.partition("/")
    out, err = gh(["api", "graphql", "-f", "query=" + BASE_QUERY, "-f", "owner=" + owner,
                   "-f", "name=" + name, "-F", "number=" + pr])
    if err:
        skip("base changes", err)
    else:
        try:
            items = json.loads(out)["data"]["repository"]["pullRequest"]["timelineItems"]
            nodes = items["nodes"]
            if items["filteredCount"] > len(nodes):
                skip("base changes", "timeline holds %d events, %d returned" % (items["filteredCount"], len(nodes)))
            else:
                base_events = nodes
        except (ValueError, TypeError, KeyError) as e:
            skip("base changes", "unexpected answer: %s" % clean(e))

    verdict = None
    if login and issue_comments is not None:
        mine = [c for c in issue_comments
                if (c.get("user") or {}).get("login") == login
                and (c.get("body") or "").split("\n", 1)[0].strip() == HEADING]
        if mine:
            verdict = max(mine, key=lambda c: c["created_at"])
        else:
            skip("verdict", "no verdict comment by %s" % login)
    elif login:
        skip("verdict", "issue comments not read")
    else:
        skip("verdict", "account not known")

    if verdict:
        m = re.search(r"^Reviewed head:\s*([0-9a-fA-F]{7,64})\s*$", verdict["body"], re.M)
        print("VERDICT\t%s\t%s\t%s" % (verdict["id"], verdict["created_at"], m.group(1) if m else "-"))
    if head:
        print("HEAD\t%s" % head)

    if verdict:
        since = verdict["created_at"]  # ISO 8601 UTC with Z: strings compare in time order

        for e in base_events or []:
            if e.get("createdAt", "") >= since:
                print("BASE\t%s\t%s\t%s" % (clean(e["createdAt"]), clean(e["previousRefName"]),
                                              clean(e["currentRefName"])))
    if base_events:
        print("LASTBASE\t%s" % clean(max(e.get("createdAt", "") for e in base_events)))
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
