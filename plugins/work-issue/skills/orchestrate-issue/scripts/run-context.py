#!/usr/bin/env python3
"""Print what the orchestrate-issue skill needs before it spawns a planner or worker. Read-only: writes nothing on GitHub, clones nothing, changes no checkout.

usage: run-context.py [--label <name>] [--checkout owner/repo=<path>]... [<owner/repo> | 'owner/repo#n']...

  owner/repo       label mode: every open issue of that repository that carries the label
                   (default `auto-work`), oldest first
  'owner/repo#n'   an explicit issue; naming it is the consent (quote the "#")
  no argument      the repository of the current directory, in label mode
  --max <n>        accepted and ignored (the limit is applied by the skill)
  --checkout       a local checkout the caller names for a repository; used as given

Needs `gh` with the `repo` scope. Does not apply the per-run limit (it depends on the context check
of each issue) and does not read the issues themselves: that is issue-context.py.

Output, one tab-separated line per fact:
  REPO      <owner/repo>  <default branch or ->
              owner/repo is the name GitHub stores
  CHECKS    <owner/repo>  <required status check names, tab-separated, or ->  <ruleset|protection|both|->
              the checks the default branch requires, from the active rulesets
              (repos/<o>/<r>/rules/branches/<default>) and from classic branch protection. Names are
              tab-separated because a name can contain a comma. `-` with source `-` means none is
              required. When either source could not be read (# SKIPPED rules / protection) the line
              is always `- -`: a list from one source may be incomplete
  CHECKOUT  <owner/repo>  <path or ->  <given|found|none>  <clean|dirty|->  <branch or (detached) or ->
              one line per local checkout whose `origin` is the repository, found under $CODING_ROOT
              (default ~/Nextcloud/Coding) up to two directory levels below it; `none` when there is
              no candidate. A path given with --checkout is the only line
  ISSUE     <owner/repo#n>  <ref|label>  <labeler or ->  <permission or ->  <createdAt or ->  <edit>
              explicit references first, in the given order, then labelled issues oldest first.
              labeler: the actor of the newest `labeled` event for the label; permission: that
              actor's role on the repository (admin, maintain, write, triage, read). Both are `-`
              for an explicit reference. An issue whose labeler cannot be determined shows `-` and a
              # SKIPPED line. An issue named explicitly and also carrying the label is printed once,
              as `ref`.
              edit: `none` when neither the body nor the title was edited after the label event;
              `trusted` when every such edit was made by an account with admin, maintain or write;
              `untrusted` when any was made by someone else or by a deleted account; `-` for a
              reference or when the history could not be read in full (userContentEdits(first: 100)
              plus the `renamed` events; a history longer than 100 is # SKIPPED edits)
Status lines start with "#":
  # SKIPPED <source>: <reason>   a source that could not be read; its lines are missing, not empty.
                                 Sources: rules, protection, issues, labeler, permission, edits, checkouts

Exits non-zero, with the reason on stderr and nothing on stdout, for a malformed argument, `gh`
missing, or a repository that cannot be resolved. Every `gh` and `git` call is given 60 seconds.
"""
import json
import os
import re
import subprocess
import sys

TIMEOUT = 60
EDIT_PAGE = 100
DEFAULT_LABEL = "auto-work"
SKIP_DIRS = {"node_modules", "dist", "build", "target", "coverage", "vendor"}


def die(message):
    print(f"run-context: {message}", file=sys.stderr)
    sys.exit(1)


def run(args, cwd=None):
    """Return (stdout, None) on success, (stdout or None, reason) otherwise."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, cwd=cwd)
    except FileNotFoundError:
        return None, f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return None, f"no answer within {TIMEOUT} seconds"
    except OSError as error:
        return None, str(error)
    if done.returncode != 0:
        reason = (done.stderr.strip() or done.stdout.strip() or f"exit {done.returncode}").splitlines()[-1]
        return done.stdout or None, reason
    return done.stdout, None


def api(path, *extra):
    """Return (parsed JSON, None) or (None, reason). --paginate output is slurped into one list."""
    out, reason = run(["gh", "api", path, *extra])
    if reason is not None:
        return None, reason
    try:
        return json.loads(out), None
    except ValueError:
        pass
    try:  # --paginate prints one JSON array per page, back to back
        decoder, pos, items = json.JSONDecoder(), 0, []
        while pos < len(out):
            while pos < len(out) and out[pos].isspace():
                pos += 1
            if pos >= len(out):
                break
            page, pos = decoder.raw_decode(out, pos)
            items.extend(page if isinstance(page, list) else [page])
        return items, None
    except ValueError:
        return None, "unexpected answer"


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def emit(*fields):
    print("\t".join("-" if field is None else clean(str(field)) or "-" for field in fields))


def skipped(source, reason):
    print(f"# SKIPPED {source}: {clean(reason)}")


def parse_args(argv):
    label, given, repos, explicit = DEFAULT_LABEL, {}, [], []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            sys.exit(0)
        if arg in ("--label", "--checkout", "--max"):
            if index + 1 >= len(argv):
                die(f"{arg} needs a value")
            value = argv[index + 1]
            index += 2
            if arg == "--max":
                if not value.isdigit():
                    die(f"--max needs a number: {value!r}")
            elif arg == "--label":
                if not value.strip() or "," in value:
                    die(f"not a label name: {value!r}")
                label = value
            else:
                match = re.fullmatch(r"([\w.-]+/[\w.-]+)=(.+)", value)
                if not match:
                    die(f"not owner/repo=<path>: {value!r}")
                given[match.group(1).lower()] = os.path.abspath(os.path.expanduser(match.group(2)))
            continue
        issue = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", arg)
        repo = re.fullmatch(r"[\w.-]+/[\w.-]+", arg)
        if issue:
            explicit.append((issue.group(1), int(issue.group(2))))
        elif repo:
            repos.append(arg)
        else:
            die(f"not owner/repo or owner/repo#n: {arg!r}")
        index += 1
    return label, given, repos, explicit


def local_repository():
    out, reason = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if reason is None and out and out.strip():
        return out.strip()
    die(f"no repository argument and the current directory is not a repository `gh` resolves: {reason}")


def repo_info(name):
    data, reason = api(f"repos/{name}")
    if not isinstance(data, dict) or "full_name" not in data:
        die(f"cannot resolve {name}: {reason or 'no answer'}")
    return data["full_name"], data.get("default_branch") or None


def required_checks(repo, default):
    """Return (names, source) with source in ruleset, protection, both or '-'."""
    ruleset, protection, incomplete = set(), set(), False
    rules, reason = api(f"repos/{repo}/rules/branches/{default}", "--paginate")
    if rules is None:
        skipped("rules", reason)
        incomplete = True
    else:
        for rule in rules:
            if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
                for check in (rule.get("parameters") or {}).get("required_status_checks") or []:
                    if check.get("context"):
                        ruleset.add(check["context"])
    branch, reason = api(f"repos/{repo}/branches/{default}")
    if not isinstance(branch, dict):
        skipped("protection", reason)
        incomplete = True
    else:
        status = (branch.get("protection") or {}).get("required_status_checks") or {}
        for check in status.get("checks") or []:
            if check.get("context"):
                protection.add(check["context"])
        protection.update(c for c in status.get("contexts") or [] if c)
    if incomplete:
        return [], "-"
    names = sorted(ruleset | protection)
    source = "both" if ruleset and protection else "ruleset" if ruleset else "protection" if protection else "-"
    return names, source


def origin_repository(path):
    out, reason = run(["git", "-C", path, "remote", "get-url", "origin"])
    if reason is not None or not out:
        return None
    match = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?\s*$", out.strip())
    return match.group(1).lower() if match else None


def checkout_state(path):
    status, reason = run(["git", "-C", path, "--no-optional-locks", "status", "--porcelain"])
    if reason is not None:
        return "-", "-"
    branch, _ = run(["git", "-C", path, "branch", "--show-current"])
    return ("dirty" if status.strip() else "clean"), ((branch or "").strip() or "(detached)")


def find_checkouts(repo, root):
    found = []
    if not os.path.isdir(root):
        skipped("checkouts", f"{root} is not a directory")
        return found
    base_depth = root.rstrip(os.sep).count(os.sep)
    for current, dirs, _files in os.walk(root):
        depth = current.rstrip(os.sep).count(os.sep) - base_depth
        if ".git" in dirs or os.path.isfile(os.path.join(current, ".git")):
            if origin_repository(current) == repo.lower():
                found.append(current)
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")) if depth < 2 else []
    return found


def print_checkouts(repo, given):
    if given:
        state, branch = checkout_state(given)
        emit("CHECKOUT", repo, given, "given", state, branch)
        return
    root = os.path.abspath(os.path.expanduser(os.environ.get("CODING_ROOT") or "~/Nextcloud/Coding"))
    candidates = find_checkouts(repo, root)
    if not candidates:
        emit("CHECKOUT", repo, "-", "none", "-", "-")
    for path in candidates:
        state, branch = checkout_state(path)
        emit("CHECKOUT", repo, path, "found", state, branch)


_roles = {}


def role_of(repo, login):
    """(role, None) or (None, reason); cached per repository and login."""
    key = (repo.lower(), login.lower())
    if key not in _roles:
        perm, reason = api(f"repos/{repo}/collaborators/{login}/permission")
        _roles[key] = (perm.get("role_name") or perm.get("permission"), None) if isinstance(perm, dict) else (None, reason)
    return _roles[key]


def editors_after(repo, number, events, since):
    """Logins (None for a deleted account) of every edit of the body or title made after `since`.
    Raises ValueError when the history cannot be read in full."""
    owner, name = repo.split("/", 1)
    out, reason = run([
        "gh", "api", "graphql", "-f",
        "query=query($o:String!,$n:String!,$i:Int!){repository(owner:$o,name:$n){issue(number:$i)"
        "{userContentEdits(first: %d){totalCount nodes{editedAt editor{login}}}}}}" % EDIT_PAGE,
        "-f", f"o={owner}", "-f", f"n={name}", "-F", f"i={number}",
    ])
    try:
        history = json.loads(out)["data"]["repository"]["issue"]["userContentEdits"]
        nodes, total = history["nodes"], history["totalCount"]
        if not isinstance(nodes, list) or not isinstance(total, int):
            raise TypeError
    except (TypeError, ValueError, KeyError):
        raise ValueError(reason or "unexpected answer")
    if total > len(nodes):
        raise ValueError(f"only {len(nodes)} of {total} edits were read")
    found = [(node.get("editedAt") or "", (node.get("editor") or {}).get("login")) for node in nodes if node]
    # title edits are not part of userContentEdits: the `renamed` events carry them
    found += [
        (event.get("created_at") or "", (event.get("actor") or {}).get("login"))
        for event in events
        if event.get("event") == "renamed"
    ]
    return [login for when, login in found if when > since]


def labeler(repo, number, label):
    """(actor, role, edit state) for the newest `labeled` event; Nones with a SKIPPED line when unreadable."""
    events, reason = api(f"repos/{repo}/issues/{number}/events", "--paginate")
    if events is None:
        skipped("labeler", f"{repo}#{number}: {reason}")
        return None, None, None
    actor, since = None, None
    for event in events:
        if event.get("event") == "labeled" and (event.get("label") or {}).get("name") == label:
            actor, since = (event.get("actor") or {}).get("login"), event.get("created_at")
    if not actor:
        skipped("labeler", f"{repo}#{number}: no `labeled` event for {label}")
        return None, None, None
    role, reason = role_of(repo, actor)
    if role is None:
        skipped("permission", f"{repo}#{number}: {actor}: {reason}")
    try:
        editors = editors_after(repo, number, events, since)
    except ValueError as error:
        skipped("edits", f"{repo}#{number}: {error}")
        return actor, role, None
    state = "none" if not editors else "trusted"
    for editor in set(editors):
        if not editor:
            return actor, role, "untrusted"
        editor_role, reason = role_of(repo, editor)
        if editor_role is None:
            skipped("permission", f"{repo}#{number}: {editor}: {reason}")
            return actor, role, None
        if editor_role not in ("admin", "maintain", "write"):
            return actor, role, "untrusted"
    return actor, role, state


def main():
    label, given, repos, explicit = parse_args(sys.argv[1:])
    if not repos and not explicit:
        repos = [local_repository()]
    resolved = {}
    for name in [r for r in repos] + [r for r, _ in explicit]:
        if name.lower() not in resolved:
            resolved[name.lower()] = repo_info(name)
    for full, default in resolved.values():
        emit("REPO", full, default)
        if default:
            names, source = required_checks(full, default)
            print("\t".join(["CHECKS", full, "\t".join(clean(n) for n in names) or "-", source]))
        else:
            skipped("rules", f"{full} has no default branch")
        print_checkouts(full, given.get(full.lower()))
    for name, number in explicit:
        emit("ISSUE", f"{resolved[name.lower()][0]}#{number}", "ref", "-", "-", "-", "-")
    printed = {(resolved[n.lower()][0].lower(), i) for n, i in explicit}
    for name in repos:
        full = resolved[name.lower()][0]
        issues, reason = api(
            f"repos/{full}/issues", "--paginate", "-X", "GET",
            "-f", f"labels={label}", "-f", "state=open", "-f", "sort=created", "-f", "direction=asc", "-f", "per_page=100",
        )
        if issues is None:
            skipped("issues", f"{full}: {reason}")
            continue
        for issue in issues:
            if "pull_request" in issue or (full.lower(), issue["number"]) in printed:
                continue
            printed.add((full.lower(), issue["number"]))
            actor, role, edit = labeler(full, issue["number"], label)
            emit("ISSUE", f"{full}#{issue['number']}", "label", actor, role, issue.get("created_at"), edit)


if __name__ == "__main__":
    main()
