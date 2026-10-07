#!/usr/bin/env python3
"""Print what has to be known before work on one GitHub issue starts. Writes nothing, on GitHub or locally.

usage: issue-context.py <n | '#n' | 'owner/repo#n'>     (quote a "#": unquoted it starts a shell comment)

Run it inside the checkout of the repository the issue belongs to. A bare number is resolved against
the repository `gh` sees in the current directory. With owner/repo#n that repository is read on
GitHub, and the CHECKOUT line says whether the current directory is a checkout of it; the BRANCH
(local), MANIFEST and WORKTREE lines always describe the current directory. Needs `gh` with the
`repo` scope. Does not print the issue's text: read that with
`gh issue view <n> --json title,body,comments` (without --json, gh prints the body only to a terminal).

Output, one tab-separated line per fact, in this order:
  ISSUE     <owner/repo#n>  <open|closed>  <author>  <human|bot>  <labels, comma-separated or ->  <assignees or ->
              owner/repo is the name GitHub stores, whatever case or former name was typed
  TITLE     <title>
  DEFAULT   <default branch or ->
  CHECKOUT  <owner/repo of the current directory or ->  <match|mismatch|unknown>
              unknown: the current directory is not a checkout `gh` can resolve
  ACCOUNT   <login `gh` is authenticated as>
  PR        <#n>  <open|closed|merged>  <closes|mentions>  <head branch>  <title>
              a pull request of the same repository that closes the issue (GitHub's own link) or
              mentions it
  QUESTIONS <comment id>  <createdAt>  <open|answered>
              the newest question comment written by ACCOUNT: its first line is
              `<!-- work-issue:questions -->`. answered: a comment by an owner, member or collaborator,
              or an edit of the issue text, came after it. Comments whose first line is either marker
              never count as answers or as owner comments, whoever wrote them: ACCOUNT is usually the
              owner's own login
  PLAN      <comment id>  <createdAt>  <base sha>  <current|stale>  <reason or ->
              the newest plan comment written by ACCOUNT: its first line is
              `<!-- work-issue:plan base=<sha> -->`. stale: the issue text was edited after the plan
              (reason issue-edited), a comment by an owner, member or collaborator came after it
              (owner-comment), or questions were posted after it (newer-questions). Whether the code
              moved since <base sha> is not checked here: that needs the plan's file list
  REF       <#n>  <issue|pr>  <open|closed|merged>  <title>
              an issue or pull request of the same repository that the issue's body or comments
              mention as #n outside code spans, in order of first mention
  BRANCH    <remote|local>  <name>
              a branch with a path segment that starts with the issue number: <n>-…, <n>_…,
              issue-<n>…, or the number alone (fix/12-crash, 12-crash, feat/issue-12)
  MANIFEST  <path>  <version or ->
              package.json, pyproject.toml, Cargo.toml in the current directory and one or two levels
              below it; node_modules, dist, build, target, coverage, vendor and hidden directories
              are skipped
  WORKTREE  <clean|dirty>  <current branch or (detached)>
Status lines start with "#":
  # SKIPPED <source>: <reason>     a source that could not be read, or was read only in part; its
                                   lines are missing or incomplete, not empty. Sources: query (GitHub
                                   answered with an error next to the data), labels, assignees,
                                   account, pull-requests, comments, plan (ACCOUNT is unknown, so
                                   plan and question comments were not evaluated), refs,
                                   remote-branches, local-branches, manifests, worktree

Limits, each reported as a SKIPPED line when it is hit: the newest 100 pull requests that close the
issue, the newest 100 cross-references, the newest 100 comments, the first 30 mentioned numbers and
the first 40 manifests.

Exits non-zero, with the reason on stderr and nothing on stdout, when the argument is missing or not
an issue reference, `gh` is missing or cannot resolve the repository of the current directory, the issue
cannot be read, or the number belongs to a pull request. Every `gh` call is given 60 seconds.
"""
import json
import os
import re
import subprocess
import sys

TIMEOUT = 60
PAGE = 100
MAX_REFS = 30
MAX_MANIFESTS = 40
SKIP_DIRS = {"node_modules", "dist", "build", "target", "coverage", "vendor"}
MANIFESTS = ("package.json", "pyproject.toml", "Cargo.toml")

QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    defaultBranchRef { name }
    issueOrPullRequest(number: $number) {
      __typename
      ... on Issue {
        title state body lastEditedAt
        author { __typename login }
        labels(first: 50) { nodes { name } }
        assignees(first: 20) { nodes { login } }
        comments(last: %(page)d) {
          totalCount
          nodes { databaseId body createdAt authorAssociation author { login } }
        }
        closedByPullRequestsReferences(last: %(page)d, includeClosedPrs: true) {
          totalCount
          nodes { number state title headRefName repository { nameWithOwner } }
        }
        timelineItems(itemTypes: [CROSS_REFERENCED_EVENT], last: %(page)d) {
          totalCount
          nodes { ... on CrossReferencedEvent { source {
            __typename
            ... on PullRequest { number state title headRefName repository { nameWithOwner } }
          } } }
        }
      }
    }
  }
}
""" % {"page": PAGE}


def die(message):
    print(f"issue-context: {message}", file=sys.stderr)
    sys.exit(1)


def run(args):
    """Return (stdout, None) on success, (stdout or None, reason) otherwise."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return None, f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return None, f"no answer within {TIMEOUT} seconds"
    if done.returncode != 0:
        reason = (done.stderr.strip() or done.stdout.strip() or f"exit {done.returncode}").splitlines()[-1]
        return done.stdout or None, reason
    return done.stdout, None


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def emit(*fields):
    print("\t".join(clean(str(field)) or "-" for field in fields))


def skipped(source, reason):
    print(f"# SKIPPED {source}: {clean(reason)}")


def local_repository():
    """The repository `gh` resolves for the current directory, as GitHub stores its name, or None."""
    out, reason = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if reason is None and out and out.strip():
        return out.strip(), None
    return None, reason or "no repository"


def parse_argument(argument):
    match = re.fullmatch(r"(?:([\w.-]+/[\w.-]+)#|#)?(\d+)", argument.strip())
    if not match:
        die(f"not an issue reference: {argument!r} (expected n, #n or owner/repo#n)")
    return match.group(1), int(match.group(2))


def graphql(query, **variables):
    """Return (data, None), or (data or None, reason) when GitHub answered with errors."""
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    out, reason = run(args)
    data = None
    if out:
        try:
            data = json.loads(out).get("data")
        except ValueError:
            data = None
    if reason is None and data is None:
        reason = "unexpected answer"
    return data, reason


def mentioned_numbers(texts, own):
    seen = []
    for text in texts:
        # code is where colour values and HTML entities live; a reference is written as prose
        prose = re.sub(r"```.*?```|`[^`\n]*`", " ", text or "", flags=re.DOTALL)
        # "#12", not after a word character, "/" (another repository's ref) or "&" (an HTML entity)
        for found in re.findall(r"(?<![\w/&])#([1-9]\d*)\b", prose):
            number = int(found)
            if number != own and number not in seen:
                seen.append(number)
    return seen


PLAN_MARKER = re.compile(r"<!--\s*work-issue:plan\s+base=([0-9a-f]{7,40})\s*-->")
QUESTIONS_MARKER = re.compile(r"<!--\s*work-issue:questions\s*-->")
OWNER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def print_plan_state(issue, comments, login):
    """PLAN and QUESTIONS lines. Anyone can comment on an issue, so only comments written by `login`
    (the account the agents act as) count as plan or questions, and only owner, member or collaborator
    comments that carry no marker count as answers."""
    if not login:
        skipped("plan", "the account is unknown, so plan and question comments cannot be attributed")
        return
    plan = questions = None
    for comment in comments:  # oldest first: the last match is the newest
        first = (comment.get("body") or "").lstrip().split("\n", 1)[0]
        author = (comment.get("author") or {}).get("login") or ""
        if author.lower() != login.lower():
            continue
        match = PLAN_MARKER.fullmatch(first.strip())
        if match:
            plan = (comment, match.group(1))
        elif QUESTIONS_MARKER.fullmatch(first.strip()):
            questions = comment

    def is_marker(comment):
        first = (comment.get("body") or "").lstrip().split("\n", 1)[0].strip()
        return bool(PLAN_MARKER.fullmatch(first) or QUESTIONS_MARKER.fullmatch(first))

    def owner_comments_after(since):
        return [
            c
            for c in comments
            if c.get("authorAssociation") in OWNER_ASSOCIATIONS
            and not is_marker(c)
            and (c.get("createdAt") or "") > since
        ]

    if questions:
        edited = (issue.get("lastEditedAt") or "") > questions["createdAt"]
        state = "answered" if edited or owner_comments_after(questions["createdAt"]) else "open"
        emit("QUESTIONS", questions["databaseId"], questions["createdAt"], state)
    if plan:
        comment, base = plan
        reason = "-"
        if (issue.get("lastEditedAt") or "") > comment["createdAt"]:
            reason = "issue-edited"
        elif owner_comments_after(comment["createdAt"]):
            reason = "owner-comment"
        elif questions and questions["createdAt"] > comment["createdAt"]:
            reason = "newer-questions"
        emit("PLAN", comment["databaseId"], comment["createdAt"], base, "current" if reason == "-" else "stale", reason)


def print_refs(owner, name, numbers):
    if len(numbers) > MAX_REFS:
        skipped("refs", f"{len(numbers)} numbers are mentioned, only the first {MAX_REFS} were looked up")
        numbers = numbers[:MAX_REFS]
    if not numbers:
        return
    fields = " ".join(
        f"n{number}: issueOrPullRequest(number: {number}) {{ __typename "
        f"... on Issue {{ number state title }} ... on PullRequest {{ number state title }} }}"
        for number in numbers
    )
    # a mentioned number that is neither an issue nor a pull request makes GitHub answer with an
    # error next to the data for the others; only an answer without any data is a failure
    data, reason = graphql(
        f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {fields} }} }}",
        owner=owner,
        name=name,
    )
    found = (data or {}).get("repository")
    if found is None:
        skipped("refs", reason or "no answer")
        return
    for number in numbers:
        node = found.get(f"n{number}")
        if node:
            kind = "pr" if node["__typename"] == "PullRequest" else "issue"
            emit("REF", f"#{node['number']}", kind, node["state"].lower(), node["title"])


def print_branches(repo, number):
    # the form step 2 of the skill creates, plus the common "issue-<n>"; a number elsewhere in a
    # name (release/2.19, v19, lodash-4.17.19) is a version, not this issue
    pattern = re.compile(rf"(?:^|/)(?:issue[-_]?)?{number}(?:[-_]|$)", re.IGNORECASE)
    out, reason = run(["gh", "api", f"repos/{repo}/branches", "--paginate", "-q", ".[].name"])
    if reason is not None:
        skipped("remote-branches", reason)
    else:
        for branch in out.split():
            if pattern.search(branch):
                emit("BRANCH", "remote", branch)
    out, reason = run(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"])
    if reason is not None:
        skipped("local-branches", reason)
    else:
        for branch in out.split():
            if pattern.search(branch):
                emit("BRANCH", "local", branch)


def manifest_version(path):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, ValueError):  # ValueError covers a file that is not UTF-8
        return "-"
    if path.endswith(".json"):
        try:
            content = json.loads(text)
        except ValueError:
            return "-"
        version = content.get("version") if isinstance(content, dict) else None
        return version if isinstance(version, str) and version else "-"
    # pyproject.toml / Cargo.toml: the `version = "..."` of [project], [package] or [tool.poetry]
    section = None
    for line in text.splitlines():
        header = re.match(r"\s*\[([^\]]+)\]", line)
        if header:
            section = header.group(1).strip()
            continue
        if section in ("project", "package", "tool.poetry"):
            found = re.match(r"\s*version\s*=\s*[\"']([^\"']+)[\"']", line)
            if found:
                return found.group(1)
    return "-"


def print_manifests():
    found = []
    for root, dirs, files in os.walk("."):
        depth = 0 if root == "." else root.count(os.sep)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")) if depth < 2 else []
        for name in MANIFESTS:
            if name in files:
                found.append(os.path.normpath(os.path.join(root, name)))
    found.sort(key=lambda path: (path.count(os.sep), path))
    if len(found) > MAX_MANIFESTS:
        skipped("manifests", f"{len(found)} manifests found, only the first {MAX_MANIFESTS} are listed")
    for path in found[:MAX_MANIFESTS]:
        emit("MANIFEST", path, manifest_version(path))


def print_worktree():
    # --no-optional-locks: a plain `git status` refreshes and rewrites .git/index
    status, reason = run(["git", "--no-optional-locks", "status", "--porcelain"])
    if reason is not None:
        skipped("worktree", reason)
        return
    branch, _ = run(["git", "branch", "--show-current"])
    emit("WORKTREE", "dirty" if status.strip() else "clean", (branch or "").strip() or "(detached)")


def main():
    if len(sys.argv) == 2 and sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)
    if len(sys.argv) != 2:
        die("expected one argument: n, '#n' or 'owner/repo#n' (an unquoted # starts a shell comment)")
    typed, number = parse_argument(sys.argv[1])
    local, local_reason = local_repository()
    if not typed and not local:
        die(f"cannot resolve the repository of the current directory: {local_reason}")
    owner, name = (typed or local).split("/", 1)

    data, reason = graphql(QUERY, owner=owner, name=name, number=number)
    repository = (data or {}).get("repository") or {}
    issue = repository.get("issueOrPullRequest")
    if not issue:
        die(f"cannot read {typed or local}#{number}: {reason or 'it does not exist or is not readable'}")
    repo = repository["nameWithOwner"]
    if issue["__typename"] != "Issue":
        die(f"{repo}#{number} is a pull request, not an issue")
    owner, name = repo.split("/", 1)
    # GitHub can answer with the issue and an error for a single field, which is then null. The
    # fields the first lines need are checked before anything is printed; a missing list is
    # replaced by an empty one and reported as SKIPPED where its lines would have been.
    if not isinstance(issue.get("title"), str) or not isinstance(issue.get("state"), str):
        die(f"cannot read {repo}#{number}: {reason or 'incomplete answer'}")
    empty = {"totalCount": 0, "nodes": []}
    partial = {}
    for field, source in (
        ("closedByPullRequestsReferences", "pull-requests"),
        ("timelineItems", "pull-requests"),
        ("comments", "comments"),
        ("labels", "labels"),
        ("assignees", "assignees"),
    ):
        value = issue.get(field)
        if not isinstance(value, dict) or not isinstance(value.get("nodes"), list):
            issue[field] = dict(empty)
            partial[source] = reason or "GitHub returned no data for this part"
        else:
            value["nodes"] = [node for node in value["nodes"] if node]
            value.setdefault("totalCount", len(value["nodes"]))
    if reason and not partial:
        partial["query"] = reason

    author = issue["author"] or {"login": "ghost", "__typename": "User"}
    emit(
        "ISSUE",
        f"{repo}#{number}",
        issue["state"].lower(),
        author["login"],
        "bot" if author["__typename"] == "Bot" else "human",
        ",".join(label["name"] for label in issue["labels"]["nodes"]),
        ",".join(user["login"] for user in issue["assignees"]["nodes"]),
    )
    for source in ("labels", "assignees", "query"):
        if source in partial:
            skipped(source, partial[source])
    emit("TITLE", issue["title"])
    emit("DEFAULT", (repository.get("defaultBranchRef") or {}).get("name"))
    emit("CHECKOUT", local or "-", ("match" if local.lower() == repo.lower() else "mismatch") if local else "unknown")

    login, login_reason = run(["gh", "api", "user", "-q", ".login"])
    if login_reason is None and login.strip():
        emit("ACCOUNT", login.strip())
    else:
        skipped("account", login_reason or "no login")

    closing = issue["closedByPullRequestsReferences"]
    crossrefs = issue["timelineItems"]
    if "pull-requests" in partial:
        skipped("pull-requests", partial["pull-requests"])
    for connection, what in ((closing, "pull requests that close the issue"), (crossrefs, "cross-references")):
        if connection["totalCount"] > len(connection["nodes"]):
            skipped("pull-requests", f"only the newest {PAGE} of {connection['totalCount']} {what} were read")
    pulls = {}
    for node in closing["nodes"]:
        if node and node["repository"]["nameWithOwner"] == repo:
            pulls[node["number"]] = ("closes", node)
    for item in crossrefs["nodes"]:
        node = (item or {}).get("source") or {}
        if node.get("__typename") == "PullRequest" and node["repository"]["nameWithOwner"] == repo:
            pulls.setdefault(node["number"], ("mentions", node))
    for pull_number in sorted(pulls):
        relation, node = pulls[pull_number]
        emit("PR", f"#{pull_number}", node["state"].lower(), relation, node["headRefName"], node["title"])

    comments = issue["comments"]
    if "comments" in partial:
        skipped("comments", partial["comments"])
    if comments["totalCount"] > len(comments["nodes"]):
        skipped("comments", f"only the newest {PAGE} of {comments['totalCount']} comments were searched for mentions")
    texts = [issue.get("body")] + [comment.get("body") for comment in comments["nodes"]]
    print_plan_state(issue, [c for c in comments["nodes"] if c], login.strip() if login_reason is None and login.strip() else None)
    print_refs(owner, name, [n for n in mentioned_numbers(texts, number) if n not in pulls])
    print_branches(repo, number)
    print_manifests()
    print_worktree()


if __name__ == "__main__":
    main()
