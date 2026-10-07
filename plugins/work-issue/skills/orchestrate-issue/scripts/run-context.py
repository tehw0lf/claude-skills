#!/usr/bin/env python3
"""Print what the orchestrate-issue skill needs before it spawns a planner or worker. Read-only: writes nothing on GitHub, clones nothing, changes no checkout.

usage: run-context.py [--label <name>] [--checkout owner/repo=<path>]... [<owner/repo> | 'owner/repo#n']...

  owner/repo       label mode: every open issue of that repository that carries the label
                   (default `auto-work`), oldest first
  'owner/repo#n'   an explicit issue; naming it is the consent (quote the "#")
  no argument      the repository of the current directory, in label mode
  --checkout       a local checkout the caller names for a repository; used as given

Needs `gh` with the `repo` scope. Does not apply the per-run limit (it depends on the context check
of each issue) and does not read the issues themselves: that is issue-context.py.

Output, one tab-separated line per fact:
  REPO      <owner/repo>  <default branch or ->
              owner/repo is the name GitHub stores
  CHECKS    <owner/repo>  <required status check names, comma-separated, or ->  <ruleset|protection|both|->
              the checks the default branch requires, from the active rulesets
              (repos/<o>/<r>/rules/branches/<default>) and from classic branch protection. `-` with
              source `-` means none is required, or no source could be read (see # SKIPPED)
  CHECKOUT  <owner/repo>  <path or ->  <given|found|none>  <clean|dirty|->  <branch or (detached) or ->
              one line per local checkout whose `origin` is the repository, found under $CODING_ROOT
              (default ~/Nextcloud/Coding) up to two directory levels below it; `none` when there is
              no candidate. A path given with --checkout is the only line
  ISSUE     <owner/repo#n>  <ref|label>  <labeler or ->  <permission or ->  <createdAt or ->
              explicit references first, in the given order, then labelled issues oldest first.
              labeler: the actor of the newest `labeled` event for the label; permission: that
              actor's role on the repository (admin, maintain, write, triage, read). Both are `-`
              for an explicit reference. An issue whose labeler cannot be determined shows `-` and a
              # SKIPPED line
Status lines start with "#":
  # SKIPPED <source>: <reason>   a source that could not be read; its lines are missing, not empty.
                                 Sources: rules, protection, issues, labeler, permission, checkouts

Exits non-zero, with the reason on stderr and nothing on stdout, for a malformed argument, `gh`
missing, or a repository that cannot be resolved. Every `gh` and `git` call is given 60 seconds.
"""
import json
import os
import re
import subprocess
import sys

TIMEOUT = 60
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
    print("\t".join(clean(str(field)) or "-" for field in fields))


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
        if arg in ("--label", "--checkout"):
            if index + 1 >= len(argv):
                die(f"{arg} needs a value")
            value = argv[index + 1]
            index += 2
            if arg == "--label":
                if not re.fullmatch(r"[^\s,]+", value):
                    die(f"not a label name: {value!r}")
                label = value
            else:
                match = re.fullmatch(r"([\w.-]+/[\w.-]+)=(.+)", value)
                if not match:
                    die(f"not owner/repo=<path>: {value!r}")
                given[match.group(1).lower()] = match.group(2)
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
    ruleset, protection = set(), set()
    rules, reason = api(f"repos/{repo}/rules/branches/{default}", "--paginate")
    if rules is None:
        skipped("rules", reason)
    else:
        for rule in rules:
            if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
                for check in (rule.get("parameters") or {}).get("required_status_checks") or []:
                    if check.get("context"):
                        ruleset.add(check["context"])
    branch, reason = api(f"repos/{repo}/branches/{default}")
    if not isinstance(branch, dict):
        skipped("protection", reason)
    else:
        status = (branch.get("protection") or {}).get("required_status_checks") or {}
        for check in status.get("checks") or []:
            if check.get("context"):
                protection.add(check["context"])
        protection.update(c for c in status.get("contexts") or [] if c)
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
    root = os.environ.get("CODING_ROOT") or os.path.expanduser("~/Nextcloud/Coding")
    candidates = find_checkouts(repo, root)
    if not candidates:
        emit("CHECKOUT", repo, "-", "none", "-", "-")
    for path in candidates:
        state, branch = checkout_state(path)
        emit("CHECKOUT", repo, path, "found", state, branch)


def labeler(repo, number, label):
    """(actor login, role) of the newest `labeled` event for the label, or (None, None) with a SKIPPED line."""
    events, reason = api(f"repos/{repo}/issues/{number}/events", "--paginate")
    if events is None:
        skipped("labeler", f"{repo}#{number}: {reason}")
        return None, None
    actor = None
    for event in events:
        if event.get("event") == "labeled" and (event.get("label") or {}).get("name") == label:
            actor = (event.get("actor") or {}).get("login")
    if not actor:
        skipped("labeler", f"{repo}#{number}: no `labeled` event for {label}")
        return None, None
    perm, reason = api(f"repos/{repo}/collaborators/{actor}/permission")
    if not isinstance(perm, dict):
        skipped("permission", f"{repo}#{number}: {actor}: {reason}")
        return actor, None
    return actor, perm.get("role_name") or perm.get("permission")


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
            emit("CHECKS", full, ",".join(names), source)
        else:
            skipped("rules", f"{full} has no default branch")
        print_checkouts(full, given.get(full.lower()))
    for name, number in explicit:
        emit("ISSUE", f"{resolved[name.lower()][0]}#{number}", "ref", "-", "-", "-")
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
            if "pull_request" in issue:
                continue
            actor, role = labeler(full, issue["number"], label)
            emit("ISSUE", f"{full}#{issue['number']}", "label", actor, role, issue.get("created_at"))


if __name__ == "__main__":
    main()
