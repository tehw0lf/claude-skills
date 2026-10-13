#!/usr/bin/env python3
"""Print what has to be known before work on one GitHub issue starts. Writes nothing, on GitHub or locally.

usage: issue-context.py [--comments] <n | '#n' | 'owner/repo#n'>     (quote a "#": unquoted it starts a shell comment)

Run it inside the checkout of the repository the issue belongs to. A bare number is resolved against
the repository `gh` sees in the current directory. With owner/repo#n that repository is read on
GitHub, and the CHECKOUT line says whether the current directory is a checkout of it; the BRANCH
(local), MANIFEST and WORKTREE lines always describe the current directory. Needs `gh` with the
`repo` scope. Does not print the issue's text: read the body with
`gh issue view <n> --json body -q .body` (without --json, gh prints the body only to a terminal), and
the comments with `--comments`. Do not read the comments with `gh issue view --json comments`: it returns
every comment, also those of accounts without write access, which `--comments` leaves out.

--comments prints only the comments facts below (no other line, the status lines excepted) and exits.

Output, one tab-separated line per fact, in this order:
  ISSUE     <owner/repo#n>  <open|closed>  <author>  <human|bot>  <labels, comma-separated or ->  <assignees or ->
              owner/repo is the name GitHub stores, whatever case or former name was typed
  TITLE     <title>
              for the report to the user; the title is not an input for planning or working
  DEFAULT   <default branch or ->
  CHECKOUT  <owner/repo of the current directory or ->  <match|mismatch|unknown>
              unknown: the current directory is not a checkout `gh` can resolve
  ACCOUNT   <login `gh` is authenticated as>
  PR        <#n>  <open|closed|merged>  <closes|mentions>  <head branch>  <title>
              a pull request of the same repository that closes the issue (GitHub's own link) or
              mentions it
  QUESTIONS <comment id>  <createdAt>  <open|answered>
              the newest question comment written by ACCOUNT: its first line is
              `<!-- work-issue:questions -->`. answered: a later comment by an account with admin, maintain or
              write permission (ACCOUNT included); an edit of the issue text is not an answer, the script
              cannot tell who made it. Comments whose first line is either marker never count as answers or
              as owner comments, whoever wrote them: ACCOUNT is usually the owner's own login
  PLAN      <comment id>  <createdAt>  <base sha>  <current|stale>  <reason or ->
              the newest plan comment written by ACCOUNT: its first line is
              `<!-- work-issue:plan base=<sha> -->`. stale: the issue text was edited after the plan
              (reason issue-edited), a comment by an account with admin, maintain or write permission came
              after it (owner-comment), or questions were posted after it (newer-questions). Whether the code
              moved since <base sha> is not checked here: that needs the plan's file list
  RESUME    <#n>  <head sha>  <plan comment id the PR body links or ->  <mergeable|conflicting|unknown>
            <head of the newest verdict or ->  <verdict count>  <head of the newest stop marker or ->
            <its reason or ->  <answered or ->
              one line per open pull request that closes the issue, was written by ACCOUNT and has its
              head branch in this repository. The plan id is the PLAN comment's when the body links it as
              https://github.com/<repo>/issues/<n>#issuecomment-<id> outside code, else the first such link; an id
              anywhere else in the text is no link. A verdict is a comment by ACCOUNT whose first line is
              `## Independent local review`, its head the `Reviewed head:` line. A stop marker is a comment by
              ACCOUNT whose first line is `<!-- work-issue:stopped head=<sha> reason=<kind> -->` (written by
              issue-worker when it stops after the PR exists). answered: a comment after that marker by an
              account with admin, maintain or write permission that is neither a verdict nor a marker
  REF       <#n>  <issue|pr>  <open|closed|merged>  <title>
              an issue or pull request of the same repository that the issue's body or the comments of
              trusted accounts mention as #n outside code spans, in order of first mention
  BRANCH    <remote|local>  <name>
              a branch with a path segment that starts with the issue number: <n>-…, <n>_…,
              issue-<n>…, or the number alone (fix/12-crash, 12-crash, feat/issue-12)
  MANIFEST  <path>  <version or ->
              package.json, pyproject.toml, Cargo.toml in the current directory and one or two levels
              below it; node_modules, dist, build, target, coverage, vendor and hidden directories
              are skipped
  WORKTREE  <clean|dirty>  <current branch or (detached)>
  COMMENT   <comment id>  <author>  <createdAt>  <body as one JSON string>      (--comments only)
              one line per comment of ACCOUNT or of an account with admin, maintain or write permission.
              The body is a JSON string with every non-ASCII character escaped: it cannot start a line
              of its own or pass for a line kind
  OMITTED   <n>                                                                  (--comments only)
              the number of comments left out because their author is neither ACCOUNT nor such an account
              (read access only, no relation to the repository, a deleted account, or a permission that could
              not be read: # SKIPPED permission). Whether they matter is the owner's call: ask
Status lines start with "#":
  # SKIPPED <source>: <reason>     a source that could not be read, or was read only in part; its
                                   lines are missing or incomplete, not empty. Sources: query (GitHub
                                   answered with an error next to the data), labels, assignees,
                                   account, pull-requests, resume, comments, permission (the role of an
                                   author could not be read, so that author is not trusted), plan (ACCOUNT
                                   is unknown, so plan and question comments were not evaluated), refs,
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
          nodes { databaseId body createdAt author { login } }
        }
        closedByPullRequestsReferences(last: %(page)d, includeClosedPrs: true) {
          totalCount
          nodes { number state title headRefName repository { nameWithOwner } author { login } headRepository { nameWithOwner } }
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

PULL_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      body headRefOid mergeable
      comments(last: %(page)d) {
        totalCount
        nodes { databaseId body createdAt author { login } }
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
    print("\t".join("-" if field is None else clean(str(field)) or "-" for field in fields))


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
TRUSTED_ROLES = {"admin", "maintain", "write"}
_roles = {}
_unreadable = set()


def role_of(repo, login):
    """(role, None) or (None, reason): the login's permission on the repository (admin, maintain, write,
    triage, read, none); cached per repository and login."""
    key = (repo.lower(), login.lower())
    if key not in _roles:
        out, reason = run(["gh", "api", f"repos/{repo}/collaborators/{login}/permission"])
        role = None
        if reason is None and out:
            try:
                data = json.loads(out)
                role = data.get("role_name") or data.get("permission") if isinstance(data, dict) else None
            except ValueError:
                reason = "unexpected answer"
        _roles[key] = (role, None) if isinstance(role, str) and role else (None, reason or "no role in the answer")
    return _roles[key]


def is_trusted(repo, login, account):
    """True for the account the agents act as and for a login with admin, maintain or write permission.
    Anyone can comment on a public issue; `author_association` says how someone relates to the repository,
    not what they may do (a member of an organisation can have read access only), so the permission is read.
    A login without a readable role is not trusted and is reported once as # SKIPPED permission."""
    if not login:
        return False
    if account and login.lower() == account.lower():
        return True
    role, reason = role_of(repo, login)
    if role is None:
        if login.lower() not in _unreadable:
            _unreadable.add(login.lower())
            skipped("permission", f"{login}: {reason}")
        return False
    return role in TRUSTED_ROLES


def print_plan_state(repo, issue, comments, login):
    """PLAN and QUESTIONS lines. Anyone can comment on an issue, so only comments written by `login`
    (the account the agents act as) count as plan or questions, and only comments without a marker by an
    account with admin, maintain or write permission count as answers. Returns the comment id of the newest plan, or None."""
    if not login:
        skipped("plan", "the account is unknown, so plan and question comments cannot be attributed")
        return None
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
            if (c.get("createdAt") or "") > since
            and not is_marker(c)
            and is_trusted(repo, (c.get("author") or {}).get("login"), login)
        ]

    if questions:
        state = "answered" if owner_comments_after(questions["createdAt"]) else "open"
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
        return comment["databaseId"]
    return None

STOP_MARKER = re.compile(r"<!--\s*work-issue:stopped\s+head=([0-9a-f]{7,40})\s+reason=([a-z-]+)\s*-->")
VERDICT_HEADING = "## Independent local review"
REVIEWED_HEAD = re.compile(r"^Reviewed head:\s*`?([0-9a-f]{7,40})`?\s*$", re.MULTILINE)
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


def print_resume(repo, owner, name, number, pulls, login, plan_id):
    """RESUME lines: one per open pull request that closes the issue and that this account wrote in this
    repository (not a fork). Whether it is the account's own work on the current plan is for the caller to
    compare; the lines only carry the facts. Anyone can comment on a PR, so verdicts and stop markers count
    only from `login`, and only comments without a marker by an account with admin, maintain or write permission count as an answer."""
    candidates = [
        (number, node)
        for number, (relation, node) in sorted(pulls.items())
        if relation == "closes"
        and node["state"] == "OPEN"
        and ((node.get("author") or {}).get("login") or "").lower() == (login or "\0").lower()
        and ((node.get("headRepository") or {}).get("nameWithOwner") or "").lower() == repo.lower()
    ]
    # the plan is linked as https://github.com/<repo>/issues/<n>#issuecomment-<id>, outside code: an id that
    # only appears somewhere in the text (a code span, a bare number, another repository) is no link
    plan_link = re.compile(rf"https?://github\.com/{re.escape(repo)}/(?:issues|pull)/{number}#issuecomment-(\d+)", re.IGNORECASE)
    for pull_number, _node in candidates:
        data, reason = graphql(PULL_QUERY, owner=owner, name=name, number=pull_number)
        pull = ((data or {}).get("repository") or {}).get("pullRequest")
        comments = (pull or {}).get("comments") or {}
        if not isinstance(pull, dict) or not isinstance(comments.get("nodes"), list) or not isinstance(pull.get("headRefOid"), str):
            skipped("resume", f"#{pull_number}: {reason or 'incomplete answer'}")
            continue
        if reason:
            skipped("resume", f"#{pull_number}: {reason}")
            continue
        nodes = [c for c in comments["nodes"] if c]
        if comments.get("totalCount", len(nodes)) > len(nodes):
            skipped("resume", f"#{pull_number}: only the newest {PAGE} of {comments['totalCount']} comments were read")
            continue
        linked = plan_link.findall(CODE.sub(" ", pull.get("body") or ""))
        plan = str(plan_id) if plan_id is not None and str(plan_id) in linked else (linked[0] if linked else None)
        verdict_head, verdicts, stop = None, 0, None
        for comment in nodes:  # oldest first: the last match is the newest
            if ((comment.get("author") or {}).get("login") or "").lower() != login.lower():
                continue
            body = (comment.get("body") or "").lstrip()
            first = body.split("\n", 1)[0].strip()
            if first == VERDICT_HEADING:
                verdicts += 1
                found = REVIEWED_HEAD.search(body)
                verdict_head = found.group(1) if found else None
            else:
                marker = STOP_MARKER.fullmatch(first)
                if marker:
                    stop = (comment.get("createdAt") or "", marker.group(1), marker.group(2))
        answered = False
        if stop:
            for comment in nodes:
                body = (comment.get("body") or "").lstrip()
                first = body.split("\n", 1)[0].strip()
                if (
                    (comment.get("createdAt") or "") > stop[0]
                    and first != VERDICT_HEADING
                    and not STOP_MARKER.fullmatch(first)
                    and is_trusted(repo, (comment.get("author") or {}).get("login"), login)
                ):
                    answered = True
        emit(
            "RESUME", f"#{pull_number}", pull["headRefOid"], plan, (pull.get("mergeable") or "UNKNOWN").lower(),
            verdict_head, verdicts, stop[1] if stop else None, stop[2] if stop else None, "answered" if answered else None,
        )


def print_comments(repo, comments, account):
    """COMMENT lines for the comments of accounts that are trusted (see is_trusted) and one OMITTED line
    with the number of the others. A body is one JSON string (ASCII-escaped): a raw tab, newline or
    separator in somebody's text cannot start a line of its own or pass for a line kind."""
    omitted = 0
    for comment in comments["nodes"]:
        login = (comment.get("author") or {}).get("login")
        if is_trusted(repo, login, account):
            print("\t".join([
                "COMMENT", str(comment["databaseId"]), clean(login), clean(comment.get("createdAt")),
                json.dumps(comment.get("body") or "", ensure_ascii=True),
            ]))
        else:
            omitted += 1
    print(f"OMITTED\t{omitted}")


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
    comments_only = "--comments" in sys.argv[1:]
    arguments = [a for a in sys.argv[1:] if a != "--comments"]
    if len(arguments) != 1:
        die("expected one argument: n, '#n' or 'owner/repo#n' (an unquoted # starts a shell comment), optionally --comments")
    typed, number = parse_argument(arguments[0])
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

    if comments_only:
        login, login_reason = run(["gh", "api", "user", "-q", ".login"])
        account = login.strip() if login_reason is None and login.strip() else None
        if not account:
            skipped("account", login_reason or "no login")
        if "comments" in partial:
            skipped("comments", partial["comments"])
        if issue["comments"]["totalCount"] > len(issue["comments"]["nodes"]):
            skipped("comments", f"only the newest {PAGE} of {issue['comments']['totalCount']} comments were read")
        print_comments(repo, issue["comments"], account)
        return

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
    account = login.strip() if login_reason is None and login.strip() else None
    # a mention steers what the planner reads next, so only the body and trusted comments are searched
    texts = [issue.get("body")] + [
        comment.get("body") for comment in comments["nodes"]
        if is_trusted(repo, (comment.get("author") or {}).get("login"), account)
    ]
    plan_id = print_plan_state(repo, issue, [c for c in comments["nodes"] if c], account)
    if account:
        print_resume(repo, owner, name, number, pulls, account, plan_id)
    elif any(relation == "closes" for relation, _ in pulls.values()):
        skipped("resume", "the account is unknown, so its own pull requests cannot be recognised")
    print_refs(owner, name, [n for n in mentioned_numbers(texts, number) if n not in pulls])
    print_branches(repo, number)
    print_manifests()
    print_worktree()


if __name__ == "__main__":
    main()
