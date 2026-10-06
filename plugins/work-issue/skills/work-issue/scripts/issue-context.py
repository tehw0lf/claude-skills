#!/usr/bin/env python3
"""Print what has to be known before work on one GitHub issue starts. Read-only, on GitHub and locally.

usage: issue-context.py <n | #n | owner/repo#n>

Run it inside the checkout of the repository the issue belongs to. A bare number is resolved against
the repository `gh` sees in the current directory; with owner/repo#n that repository is used for
everything read from GitHub, and the local lines still describe the current directory. Needs `gh`
with the `repo` scope. Does not print the issue's text: read that with `gh issue view <n> --comments`.

Output, one tab-separated line per fact, in this order:
  ISSUE     <owner/repo#n>  <open|closed>  <author>  <human|bot>  <labels, comma-separated or ->  <assignees or ->
  TITLE     <title>
  DEFAULT   <default branch>
  PR        <#n>  <open|closed|merged>  <closes|mentions>  <head branch>  <title>
              a pull request that closes the issue or mentions it; "closes" comes from GitHub's own link
  REF       <#n>  <issue|pr>  <open|closed|merged>  <title>
              an issue or pull request of the same repository that the issue's body or comments
              mention as #n (at most 30, in order of first mention)
  BRANCH    <remote|local>  <name>
              a branch whose name contains the issue number as a whole number
  MANIFEST  <path>  <version or ->
              package.json, pyproject.toml, Cargo.toml in the current directory and one or two levels
              below it (node_modules, dist, build, target, .git and hidden directories are skipped;
              at most 40)
  WORKTREE  <clean|dirty>  <current branch>
Status lines start with "#":
  # SKIPPED <source>: <reason>     a source that could not be read; its lines are missing, not empty

Exits non-zero, with the reason on stderr, when the argument is not an issue reference, the issue
cannot be read, or the number belongs to a pull request. Every `gh` call is given 60 seconds.
"""
import json
import os
import re
import subprocess
import sys

TIMEOUT = 60
SKIP_DIRS = {"node_modules", "dist", "build", "target", "coverage", "vendor"}
MANIFESTS = ("package.json", "pyproject.toml", "Cargo.toml")

QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    issueOrPullRequest(number: $number) {
      __typename
      ... on Issue {
        title state body
        author { __typename login }
        labels(first: 50) { nodes { name } }
        assignees(first: 20) { nodes { login } }
        comments(first: 100) { nodes { body } }
        closedByPullRequestsReferences(first: 30, includeClosedPrs: true) {
          nodes { number state title headRefName repository { nameWithOwner } }
        }
        timelineItems(itemTypes: [CROSS_REFERENCED_EVENT], first: 100) {
          nodes { ... on CrossReferencedEvent { source {
            __typename
            ... on PullRequest { number state title headRefName repository { nameWithOwner } }
          } } }
        }
      }
    }
  }
}
"""


def die(message):
    print(f"issue-context: {message}", file=sys.stderr)
    sys.exit(1)


def run(args):
    """Return (stdout, None) or (None, reason)."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return None, f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return None, f"no answer within {TIMEOUT} seconds"
    if done.returncode != 0:
        reason = (done.stderr.strip() or done.stdout.strip() or f"exit {done.returncode}").splitlines()[-1]
        return None, reason
    return done.stdout, None


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def emit(*fields):
    print("\t".join(clean(str(field)) or "-" for field in fields))


def parse_argument(argument):
    match = re.fullmatch(r"(?:([\w.-]+/[\w.-]+))?#?(\d+)", argument.strip())
    if not match:
        die(f"not an issue reference: {argument!r} (expected n, #n or owner/repo#n)")
    repo, number = match.group(1), int(match.group(2))
    if not repo:
        out, reason = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
        if out is None:
            die(f"cannot resolve the repository of the current directory: {reason}")
        repo = out.strip()
    return repo, number


def graphql(query, **variables):
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    out, reason = run(args)
    if out is None:
        return None, reason
    try:
        return json.loads(out)["data"], None
    except (ValueError, KeyError) as error:
        return None, f"unexpected answer: {error}"


def mentioned_numbers(texts, own):
    seen = []
    for text in texts:
        # "#12" not preceded by a word character or "/" (that would be another repository's ref)
        for found in re.findall(r"(?<![\w/])#(\d+)\b", text or ""):
            number = int(found)
            if number != own and number not in seen:
                seen.append(number)
    return seen[:30]


def print_refs(owner, name, numbers):
    if not numbers:
        return
    fields = " ".join(
        f"n{number}: issueOrPullRequest(number: {number}) {{ __typename "
        f"... on Issue {{ number state title }} ... on PullRequest {{ number state title }} }}"
        for number in numbers
    )
    data, reason = graphql(
        f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {fields} }} }}",
        owner=owner,
        name=name,
    )
    # a number that is neither an issue nor a pull request makes the whole query fail: ask one by one
    if data is None:
        for number in numbers:
            single, _ = graphql(
                "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {"
                " issueOrPullRequest(number: $number) { __typename ... on Issue { number state title }"
                " ... on PullRequest { number state title } } } }",
                owner=owner,
                name=name,
                number=number,
            )
            node = single and single["repository"]["issueOrPullRequest"]
            if node:
                print_ref(node)
        return
    for number in numbers:
        node = data["repository"].get(f"n{number}")
        if node:
            print_ref(node)


def print_ref(node):
    kind = "pr" if node["__typename"] == "PullRequest" else "issue"
    emit("REF", f"#{node['number']}", kind, node["state"].lower(), node["title"])


def print_branches(repo, number):
    pattern = re.compile(rf"(?<!\d){number}(?!\d)")
    out, reason = run(["gh", "api", f"repos/{repo}/branches", "--paginate", "-q", ".[].name"])
    if out is None:
        print(f"# SKIPPED remote-branches: {reason}")
    else:
        for branch in out.split():
            if pattern.search(branch):
                emit("BRANCH", "remote", branch)
    out, reason = run(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"])
    if out is None:
        print(f"# SKIPPED local-branches: {reason}")
    else:
        for branch in out.split():
            if pattern.search(branch):
                emit("BRANCH", "local", branch)


def manifest_version(path):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return "-"
    if path.endswith(".json"):
        try:
            return str(json.loads(text).get("version") or "-")
        except ValueError:
            return "-"
    # pyproject.toml / Cargo.toml: the first top-level `version = "..."` of [project] or [package]
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
    for path in sorted(found, key=lambda p: (p.count(os.sep), p))[:40]:
        emit("MANIFEST", path, manifest_version(path))


def print_worktree():
    status, reason = run(["git", "status", "--porcelain"])
    if status is None:
        print(f"# SKIPPED worktree: {reason}")
        return
    branch, _ = run(["git", "branch", "--show-current"])
    emit("WORKTREE", "dirty" if status.strip() else "clean", (branch or "").strip() or "(detached)")


def main():
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0 if len(sys.argv) == 2 else 1)
    repo, number = parse_argument(sys.argv[1])
    owner, name = repo.split("/", 1)

    data, reason = graphql(QUERY, owner=owner, name=name, number=number)
    if data is None:
        die(f"cannot read {repo}#{number}: {reason}")
    repository = data.get("repository") or {}
    issue = repository.get("issueOrPullRequest")
    if not issue:
        die(f"{repo}#{number} does not exist or is not readable")
    if issue["__typename"] != "Issue":
        die(f"{repo}#{number} is a pull request, not an issue")

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
    emit("TITLE", issue["title"])
    emit("DEFAULT", (repository.get("defaultBranchRef") or {}).get("name"))

    pulls = {}
    for node in issue["closedByPullRequestsReferences"]["nodes"]:
        if node and node["repository"]["nameWithOwner"] == repo:
            pulls[node["number"]] = ("closes", node)
    for item in issue["timelineItems"]["nodes"]:
        node = (item or {}).get("source") or {}
        if node.get("__typename") == "PullRequest" and node["repository"]["nameWithOwner"] == repo:
            pulls.setdefault(node["number"], ("mentions", node))
    for pull_number in sorted(pulls):
        relation, node = pulls[pull_number]
        emit("PR", f"#{pull_number}", node["state"].lower(), relation, node["headRefName"], node["title"])

    texts = [issue["body"]] + [comment["body"] for comment in issue["comments"]["nodes"]]
    print_refs(owner, name, [n for n in mentioned_numbers(texts, number) if n not in pulls])
    print_branches(repo, number)
    print_manifests()
    print_worktree()


if __name__ == "__main__":
    main()
