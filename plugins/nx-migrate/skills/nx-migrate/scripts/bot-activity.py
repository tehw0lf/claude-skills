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
  BOT      <issue-comment|review|review-comment>  <id>  <login>  <created_at>  <updated_at>  <commit or ->  <no-findings or ->
             one line per bot entry at or after the verdict. Review: both times are `submitted_at`, the
             commit is `commit_id`. Inline comment: the commit is `original_commit_id` (`commit_id`
             follows the head while the comment still applies). Issue comment: no commit, -.
             Last field: `no-findings` only for an issue comment of `coderabbitai[bot]` in which every
             part this script tests shows a finished review of the commit the verdict reviewed without
             findings. All of these hold: a line is exactly the sentence `No actionable comments were
             generated in the recent review.` (a 🎉 may follow); no "review in progress by coderabbit.ai"
             marker, no "Currently processing new changes", no "Actionable comments posted", "Review skipped",
             "Reviews paused", auto-pause, rate-limit or "Review failed" text anywhere in it (any case) and no
             `[!WARNING]` or `[!CAUTION]` block (also written `[! WARNING]`) (CodeRabbit reports a failed tool or an incomplete review
             that way); exactly one
             `between <sha> and <sha>` range whose end equals, in full, the verdict's `Reviewed head:`
             (the word `between` occurs exactly once in the whole text, so a second range written with it
             fails; one written without it, such as `a..b`, is not seen); at least one Merge Risk line, and
             every one is `Minimal · up to <sha>` where `<sha>` begins the reviewed head, read to the end of
             its line; a line that is label-shaped (the name as written `Merge Risk`, any case with
             separators, `&nbsp;`, inner markup or `level` before a separator, or `merge risk` at the start of
             a line after markup, any characters that are not letters or digits and list numbers or letters such
             as `1)`, `a.` or `(1)`) must be exactly one valid label line, checked per line, so a second label in
             another spelling fails whatever the other lines hold; a prose sentence that mentions the merge risk
             fails too when it is label-shaped, for instance `Merge Risk` written exactly, `no merge risk: …`
             or `merge risk-free` (false rejections, which only cost a round), while other prose passes (the hidden
             `final_review_risk_coverage` marker, when there is one, says `reviewed` for exactly that
             commit); a pre-merge checks section with a passed-checks part; every row of the table between
             the block's own start and end markers, whatever its headings are called, has the status Passed
             except Docstring Coverage, and the number of rows equals the total of the counters in the
             block's summary, with exactly one start and one end marker (so a renamed heading or counter
             cannot hide a row; a line of the block that holds a pipe but neither starts with one nor with
             `<summary` is not a table this script reads and fails); and the checks listed as failed or
             inconclusive, in the emoji and in the image format, are only Docstring Coverage (a style gate
             that says nothing about the change). The list is read as the table lines that follow its
             heading through markup only, its rows must number what the heading says, and their total must
             equal the failed and inconclusive counters of the section's summary; otherwise it is not
             readable and counts as not passing. A part that is missing or in a format this script does
             not know makes the field `-`: a missing line is no evidence. Anything else, also every review
             and inline comment, another author and an abbreviated `Reviewed head:`, is `-`. The text is
             only tested, never printed. The script does not read the prose under the Merge Risk level or
             any other block of the comment: it finds no sign of anything else, it does not prove there is
             none.
             The tests rest on what CodeRabbit wrote in the account's recent pull requests (149
             comments with the sentence, all worded alike; the range is in all of them and its last end
             was the commit reviewed; the Merge Risk line is missing in the older ones and one level
             above Minimal was seen; since about 2026-10-10 the checks table uses images for its
             status). A changed wording or format of a part it tests is `-`, which costs a review round and never a merge;
             text in a shape it does not test is not seen.
             The comment is edited in place for every pass, so the sentence can sit next to an in-progress
             block for a newer range, or describe a pass after earlier findings; findings are posted as
             review objects and inline comments, which are `-` and so stay a round. The caller treats
             `no-findings` as "nothing to read" only when every BOT line has it. A bot can still write
             text next to the sentence that none of these tests sees.
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
REVIEWER = "coderabbitai[bot]"
NO_FINDINGS = re.compile(r"No actionable comments were generated in the recent review\.(?:\s*🎉)?")
IN_PROGRESS = re.compile(
    r"review in progress by coderabbit\.ai|Currently processing new changes|Actionable comments posted|Review skipped"
    r"|Reviews? paused|auto-?paus|rate[- ]limit|Review failed", re.IGNORECASE)
REVIEW_RANGE = re.compile(r"between ([0-9a-f]{7,40}) and ([0-9a-f]{7,40})")
MERGE_RISK = re.compile(r"Merge Risk:([^\n]*)", re.IGNORECASE)
RISK_NAME = r"merge(?:\s|&nbsp;|&#160;|[\-_]|<[^>]*>)*risk"
RISK_LABEL = re.compile(RISK_NAME + r"(?:\s+level)?(?:\s*</?[a-z]+>|\*|_)*\s*[:=—–\-]", re.IGNORECASE)
RISK_START = re.compile(r"(?:<[^>]*>|[^\w<]|[0-9a-z]{1,3}[.)])*" + RISK_NAME, re.IGNORECASE)
MARKUP = re.compile(r"<[^>]*>|[*_`]")
MINIMAL = re.compile(r"[^\w]*Minimal\s*·\s*up to ([0-9a-f]{5,40})")
BETWEEN = re.compile(r"\bbetween\b", re.IGNORECASE)
COVERAGE = re.compile(r"final_review_risk_coverage:(\{.*?\})\s*-->", re.DOTALL)
CHECKS_START = re.compile(r"pre_merge_checks_walkthrough_start")
CHECKS_END = re.compile(r"pre_merge_checks_walkthrough_end")
CHECKS_BLOCK = re.compile(r"<!--\s*pre_merge_checks_walkthrough_start\s*-->(.*?)<!--\s*pre_merge_checks_walkthrough_end\s*-->", re.DOTALL)
PASSED_ROW = re.compile(r"✅\s*Passed|!\[Passed\]\([^)]*\)")
PASSED_COUNTER = re.compile(r"(?:✅|alt=\"Passed\")(?:[^0-9<]|<[^>]*>)*?(\d+)")
UNPASSED = re.compile(r"(?:Failed|Inconclusive) checks", re.IGNORECASE)
ONLY_MARKUP = re.compile(r"(?:<[^>]*>|\s)*")
COUNTER = re.compile(r"(?:❌|❓|alt=\"(?:Failed|Inconclusive)\")(?:[^0-9<]|<[^>]*>)*?(\d+)")
ALERT = re.compile(r"\[!\s*(?:WARNING|CAUTION)\s*\]", re.IGNORECASE)
TOLERATED = "docstring coverage"


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


def unpassed_checks(body):
    """The names of the pre-merge checks listed as failed or inconclusive, or None when that cannot be read
    with certainty: a heading that is not followed, through markup only, by a table; a table whose rows do
    not number what its heading says; or a total that differs from the counter in the section's summary.
    The rows are the table lines that follow the heading without a break: text after the table, or in a
    cell, is not looked at as a boundary."""
    names, announced = [], 0
    for heading in UNPASSED.finditer(body):
        lines = body[heading.end():].split("\n")
        count = re.search(r"(\d+)", lines[0])
        rows, started = 0, False
        for line in lines[1:]:
            if line.lstrip().startswith("|"):
                started = True
                if re.fullmatch(r"[\s|:\-]+", line):
                    continue
                name = line.strip().strip("|").split("|")[0].strip()
                if name.lower() != "check name":
                    names.append(name)
                    rows += 1
            elif started or not ONLY_MARKUP.fullmatch(line):
                break
        if not rows or not count or int(count.group(1)) != rows:
            return None
        announced += rows
    summary = re.search(r"Pre-merge checks[^\n]*", body)
    shown = sum(int(n) for n in COUNTER.findall(summary.group(0))) if summary else None
    if shown is None or shown != announced:
        return None
    return names


def check_rows(body):
    """The (name, passed) pairs of every row of the pre-merge checks table, read between the block's own
    start and end markers whatever the headings are called, or None when that cannot be done with certainty:
    no block or more than one start or end marker, no row, a row without a status cell, a line that holds a
    pipe but neither starts with one nor with `<summary` (a row written without its leading pipe), or a
    number of rows that differs from the total of the counters in the block's summary (passed, failed and
    inconclusive)."""
    block = CHECKS_BLOCK.search(body)
    if not block or len(CHECKS_START.findall(body)) != 1 or len(CHECKS_END.findall(body)) != 1:
        return None
    rows = []
    for line in block.group(1).split("\n"):
        if not line.lstrip().startswith("|"):
            if "|" in line and not re.match(r"\s*<summary[\s>]", line):
                return None  # a row written without its leading pipe: not a table this reader knows
            continue
        if re.fullmatch(r"[\s|:\-]+", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0].lower() == "check name":
            continue
        if len(cells) < 2:
            return None
        rows.append((cells[0], bool(PASSED_ROW.fullmatch(cells[1]))))
    summary = re.search(r"Pre-merge checks[^\n]*", block.group(1))
    if not rows or not summary:
        return None
    total = sum(int(n) for n in PASSED_COUNTER.findall(summary.group(0))) + sum(int(n) for n in COUNTER.findall(summary.group(0)))
    return rows if total == len(rows) else None


def no_findings(login, body, reviewed):
    """True for a finished review without findings of `reviewed` (the verdict's `Reviewed head:`, all 40
    characters, or None) by CodeRabbit in which every part tested here passes; the tests are in the module
    docstring. They find no sign of anything else, they do not prove there is none. Every test that cannot
    be made, because a part it looks for is missing or in a format it does not know, makes it False; text in a
    shape it does not look for is not seen."""
    if login != REVIEWER or not reviewed or not body or not re.fullmatch(r"[0-9a-fA-F]{40}", reviewed):
        return False
    lines = [line.strip() for line in body.split("\n")]
    if not any(NO_FINDINGS.fullmatch(line) for line in lines) or IN_PROGRESS.search(body) or ALERT.search(body):
        return False
    ranges = REVIEW_RANGE.findall(body)
    if len(ranges) != 1 or ranges[0][1] != reviewed.lower() or len(BETWEEN.findall(body)) != 1:
        return False
    labels = list(MERGE_RISK.finditer(body))
    for line in body.split("\n"):
        shaped = (len(RISK_LABEL.findall(line)), 1 if RISK_START.match(line) else 0, line.count("Merge Risk"))
        if any(shaped) and not (shaped[0] == 1 and shaped[1] <= 1 and shaped[2] == 1 and len(MERGE_RISK.findall(line)) == 1):
            return False
    risks = [MINIMAL.fullmatch(MARKUP.sub("", risk.group(1)).strip()) for risk in labels]
    if not risks or not all(risks) or not all(reviewed.lower().startswith(risk.group(1)) for risk in risks):
        return False
    markers = list(COVERAGE.finditer(body))
    if body.count("final_review_risk_coverage") != len(markers):
        return False
    for marker in markers:
        try:
            coverage = json.loads(marker.group(1))
        except ValueError:
            return False
        if not isinstance(coverage, dict) or coverage.get("kind") != "reviewed" or str(coverage.get("coveredCommitId", "")).lower() != reviewed.lower():
            return False
    if "pre-merge checks" not in body.lower() or not re.search(r"Passed checks|✅ Passed|alt=\"Passed\"", body):
        return False
    unpassed = unpassed_checks(body)
    rows = check_rows(body)
    if unpassed is None or rows is None or not all(name.lower() == TOLERATED for name in unpassed):
        return False
    return all(passed or name.lower() == TOLERATED for name, passed in rows)


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
        reviewed = m.group(1) if m else None

        def is_bot(item):
            return (item.get("user") or {}).get("type") == "Bot"

        for c in issue_comments or []:
            if is_bot(c) and max(c["created_at"], c.get("updated_at") or "") >= since:
                print("BOT\tissue-comment\t%s\t%s\t%s\t%s\t-\t%s" % (
                    c["id"], c["user"]["login"], c["created_at"], c.get("updated_at") or c["created_at"],
                    "no-findings" if no_findings(c["user"]["login"], c.get("body"), reviewed) else "-"))
        for r in reviews or []:
            t = r.get("submitted_at") or ""
            if is_bot(r) and t >= since:
                print("BOT\treview\t%s\t%s\t%s\t%s\t%s\t-" % (
                    r["id"], r["user"]["login"], t, t, r.get("commit_id") or "-"))
        for c in inline or []:
            if is_bot(c) and max(c["created_at"], c.get("updated_at") or "") >= since:
                print("BOT\treview-comment\t%s\t%s\t%s\t%s\t%s\t-" % (
                    c["id"], c["user"]["login"], c["created_at"], c.get("updated_at") or c["created_at"],
                    c.get("original_commit_id") or "-"))
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
