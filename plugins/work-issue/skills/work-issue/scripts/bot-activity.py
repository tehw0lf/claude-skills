#!/usr/bin/env python3
"""List what an automated reviewer wrote on a pull request at or after the latest verdict. Read-only on GitHub.

usage: bot-activity.py <owner/repo> <pr>

Needs `gh` with authentication. The verdict is the latest issue comment on the PR whose first line is
exactly `## Independent local review` and whose author is the account `gh` works as (anyone can comment
on a PR, so the author is part of the test; a comment that only quotes the heading does not start with it).

An automated reviewer is any author whose account type is `Bot`, not a list of names: a name list goes
stale. The cost is that a bot comment which is not a review, posted after the verdict, is listed too.

"After the verdict" means a `created_at` or `updated_at` (reviews: `submitted_at`) at or after the
verdict's `created_at`. `updated_at` is needed because an automated reviewer posts a placeholder comment
and edits it when its review is done, so the creation time alone misses the finished review. "At or
after" because equal timestamps cannot be ordered and one round too many costs less than a missed
review. The reference time is when the verdict was posted, not when the reviewer last read the three
places (just before writing it): a bot entry in between is neither read by the reviewer nor listed here;
only the verdict's `Not covered` can catch it. The three places are read through the API: `gh pr view --comments`, run non-interactively,
leaves out minimized comments without saying so.

Output, one tab-separated line per fact on stdout:
  VERDICT  <comment id>  <created_at>  <sha of the `Reviewed head:` line, - when absent>
  HEAD     <sha>                       the current head of the PR
  BOT      <issue-comment|review|review-comment>  <id>  <login>  <created_at>  <updated_at>  <commit or ->
             one line per bot entry at or after the verdict. Review: both times are `submitted_at`, the
             commit is `commit_id`. Inline comment: the commit is `original_commit_id` (`commit_id`
             follows the head while the comment still applies). Issue comment: no commit, -.
  SKIPPED  <source>  <reason>
             a source that could not be read: account, head, issue comments, reviews, review comments,
             verdict. With no verdict comment there is no reference time: `SKIPPED verdict ...` and no
             BOT lines.
Exit codes: 0 every source was read (with or without BOT lines), 1 at least one SKIPPED line,
2 usage error. BOT lines do not change the exit code: the caller acts on them.
"""
import json
import re
import subprocess
import sys

HEADING = "## Independent local review"


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
    reviews, err = pages("%s/pulls/%s/reviews" % (base, pr))
    if err:
        skip("reviews", err)
    inline, err = pages("%s/pulls/%s/comments" % (base, pr))
    if err:
        skip("review comments", err)

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

        def is_bot(item):
            return (item.get("user") or {}).get("type") == "Bot"

        for c in issue_comments or []:
            if is_bot(c) and max(c["created_at"], c.get("updated_at") or "") >= since:
                print("BOT\tissue-comment\t%s\t%s\t%s\t%s\t-" % (
                    c["id"], c["user"]["login"], c["created_at"], c.get("updated_at") or c["created_at"]))
        for r in reviews or []:
            t = r.get("submitted_at") or ""
            if is_bot(r) and t >= since:
                print("BOT\treview\t%s\t%s\t%s\t%s\t%s" % (
                    r["id"], r["user"]["login"], t, t, r.get("commit_id") or "-"))
        for c in inline or []:
            if is_bot(c) and max(c["created_at"], c.get("updated_at") or "") >= since:
                print("BOT\treview-comment\t%s\t%s\t%s\t%s\t%s" % (
                    c["id"], c["user"]["login"], c["created_at"], c.get("updated_at") or c["created_at"],
                    c.get("original_commit_id") or "-"))
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
