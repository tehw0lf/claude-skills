#!/usr/bin/env python3
"""Print what the orchestrate-issue skill needs before it spawns a planner or worker. Read-only: writes nothing on GitHub, clones nothing, changes no checkout.

usage: run-context.py [--label <name>] [--owner <login>]... [--checkout owner/repo=<path>]... [<owner/repo> | 'owner/repo#n']...

  owner/repo       label mode: every open issue of that repository that carries the label
                   (default `auto-work`), oldest first; read from the label-filtered listing and
                   from the open-issue listing filtered by the issues' own labels (the first can lag
                   behind a label set just before the call), each issue once
  --owner <login>  label mode for an account or an organisation (repeatable; only this flag makes a name an
                   account, a bare word is an error): the repositories with an open issue
                   carrying the label are found by one issue search (`user:<owner>`, which also
                   matches an organisation; archived repositories are left out), then each of them is
                   read as with `owner/repo`. The search only finds repositories; whether an issue is
                   taken is decided per repository as before. The search index can lag behind a label
                   set just before the call, so such an issue is found by the next call
  'owner/repo#n'   an explicit issue; naming it is the consent (quote the "#")
  no argument      the repository of the current directory, in label mode (never an account)
  --max <n>        accepted and ignored (the limit is applied by the skill)
  --checkout       a local checkout the caller names for a repository; used as given

Needs `gh` with the `repo` scope. Does not apply the per-run limit (it depends on the context check
of each issue) and does not read the issues themselves: that is issue-context.py.

Output, one tab-separated line per fact:
  SCOPE     <owner>  <number of repositories found>
              one line per `--owner`, before the REPO lines
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
              explicit references first, in the given order, then labelled issues oldest first
              across all repositories (ties by repository, then number).
              labeler: the actor of the newest `labeled` event for the label; permission: that
              actor's role on the repository (admin, maintain, write, triage, read; a custom role is shown as
              its base permission: admin, write, read or none). Both are `-`
              for an explicit reference. An issue whose labeler cannot be determined, or whose label was
              removed again (an `unlabeled` event as new as or newer than the newest `labeled` one),
              shows `-` and a # SKIPPED line. An issue named explicitly and also carrying the label is printed once,
              as `ref`.
              edit: `none` when neither the body nor the title was ever edited (the revision that records the
              creation does not count; a never-edited issue has none);
              `trusted` when every edit, whenever it was made, was made by an account with admin, maintain or write;
              `untrusted` when any was made by someone else or by a deleted account; `-` for a
              reference or when the history could not be read in full (userContentEdits(first: 100)
              plus the `renamed` events; a history longer than 100, or an issue that reports a creation
              revision the history does not hold, is # SKIPPED edits)
Status lines start with "#":
  # SKIPPED <source>: <reason>   a source that could not be read; its lines are missing, not empty.
                                 (`issues` naming the unfiltered listing: only the check against a lagging
                                 label filter is missing; that listing pages through every open issue and
                                 pull request within the 60-second limit, so it can time out on a large repository)
                                 Sources: search, rules, protection, issues, labeler, permission, edits, checkouts
                                 (`search`: the repositories of an `--owner` come from a search; one that
                                 cannot be read gives no repository, one that reports itself incomplete
                                 or holds more than 1000 issues gives the repositories it returned)

Exits non-zero, with the reason on stderr and nothing on stdout, for a malformed argument, `gh`
missing, or a repository or owner that cannot be resolved. Every `gh` and `git` call is given 60 seconds.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

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
    label, given, repos, explicit, owners = DEFAULT_LABEL, {}, [], [], []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            sys.exit(0)
        if arg in ("--label", "--checkout", "--max", "--owner"):
            if index + 1 >= len(argv):
                die(f"{arg} needs a value")
            value = argv[index + 1]
            index += 2
            if arg == "--max":
                if not value.isdigit():
                    die(f"--max needs a number: {value!r}")
            elif arg == "--owner":
                if not re.fullmatch(r"[\w.-]+", value):
                    die(f"not an account or organisation name: {value!r}")
                owners.append(value)
            elif arg == "--label":
                if not value.strip() or "," in value or '"' in value:
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
    return label, given, repos, explicit, owners


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
    status, reason = run(["git", "-C", path, "--no-optional-locks", "status", "--porcelain", "--untracked-files=normal"])
    if reason is not None:
        return "-", "-"
    branch, _ = run(["git", "-C", path, "branch", "--show-current"])
    return ("dirty" if status.strip() else "clean"), ((branch or "").strip() or "(detached)")


_checkout_index = None


def checkout_index(root):
    """{repository (lower case): [path, ...]} for every checkout under root, up to two directory levels below it.
    Scanned once per run, however many repositories are read."""
    global _checkout_index
    if _checkout_index is not None:
        return _checkout_index
    _checkout_index = found = {}
    if not os.path.isdir(root):
        skipped("checkouts", f"{root} is not a directory")
        return found
    base_depth = root.rstrip(os.sep).count(os.sep)
    for current, dirs, _files in os.walk(root):
        depth = current.rstrip(os.sep).count(os.sep) - base_depth
        if ".git" in dirs or os.path.isfile(os.path.join(current, ".git")):
            origin = origin_repository(current)
            if origin:
                found.setdefault(origin, []).append(current)
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
    candidates = checkout_index(root).get(repo.lower(), [])
    if not candidates:
        emit("CHECKOUT", repo, "-", "none", "-", "-")
    for path in candidates:
        state, branch = checkout_state(path)
        emit("CHECKOUT", repo, path, "found", state, branch)


_roles = {}
BUILTIN_ROLES = {"admin", "maintain", "write", "triage", "read", "none"}


def role_of(repo, login):
    """(role, None) or (None, reason); cached per repository and login. The answer has `role_name` (the
    assigned role, which is the role's own name for a custom role) and `permission` (the base role: admin,
    write, read or none; maintain arrives as write and triage as read). A built-in `role_name` is used as
    it is; a custom one is judged by its base `permission`, so a custom role built on write counts as write."""
    key = (repo.lower(), login.lower())
    if key not in _roles:
        perm, reason = api(f"repos/{repo}/collaborators/{login}/permission")
        role = None
        if isinstance(perm, dict):
            name, base = perm.get("role_name"), perm.get("permission")
            role = name if isinstance(name, str) and name in BUILTIN_ROLES else base
        _roles[key] = (role, None) if isinstance(role, str) and role else (None, reason or "no role in the answer")
    return _roles[key]


def parse_time(value):
    """A timezone-aware datetime from an ISO 8601 string; ValueError for anything else."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or non-text timestamp {value!r}")
    try:
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise ValueError(f"unreadable timestamp {value!r}")
    if moment.tzinfo is None:
        raise ValueError(f"timestamp without a timezone {value!r}")
    return moment


def login_of(person):
    """The login of a GraphQL editor or REST actor; None for null (a deleted account), ValueError for a non-object."""
    if person is None:
        return None
    if not isinstance(person, dict):
        raise ValueError(f"unexpected account entry {person!r}")
    login = person.get("login")
    return login if isinstance(login, str) and login else None


def editors_of(repo, number, events):
    """Logins (None for a deleted or unnamed account) of every edit of the body or title the issue ever
    got, whenever it was made. The revision that records the creation (the issue's first revision, same
    instant as its creation, present once the issue was edited) is no edit. Raises ValueError when the
    history cannot be read in full, when a creation revision that the issue claims is missing, or when an
    entry cannot be interpreted: an unreadable entry is never taken to mean "no edit"."""
    owner, name = repo.split("/", 1)
    out, reason = run([
        "gh", "api", "graphql", "-f",
        "query=query($o:String!,$n:String!,$i:Int!){repository(owner:$o,name:$n){issue(number:$i)"
        "{createdAt includesCreatedEdit userContentEdits(first: %d){totalCount nodes{editedAt editor{login}}}}}}" % EDIT_PAGE,
        "-f", f"o={owner}", "-f", f"n={name}", "-F", f"i={number}",
    ])
    try:
        issue = json.loads(out)["data"]["repository"]["issue"]
        history = issue["userContentEdits"]
        nodes, total = history["nodes"], history["totalCount"]
        if not isinstance(nodes, list) or not isinstance(total, int) or isinstance(total, bool):
            raise TypeError
        if not isinstance(issue["includesCreatedEdit"], bool):
            raise TypeError
        created = parse_time(issue["createdAt"])
    except (TypeError, ValueError, KeyError):
        raise ValueError(reason or "unexpected answer")
    if total > len(nodes):
        raise ValueError(f"only {len(nodes)} of {total} edits were read")
    found = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError(f"unexpected edit entry {node!r}")
        found.append((parse_time(node.get("editedAt")), login_of(node.get("editor"))))
    if issue["includesCreatedEdit"]:
        # the creation record: newest first in the answer, so the last entry at the creation instant is the oldest
        records = [i for i, (when, _) in enumerate(found) if when == created]
        if not records:
            raise ValueError("the history lacks the creation revision that the issue reports")
        del found[records[-1]]
    # title edits are not part of userContentEdits: the `renamed` events carry them
    for event in events:
        if event.get("event") == "renamed":
            found.append((parse_time(event.get("created_at")), login_of(event.get("actor"))))
    return [login for _, login in found]


def labeler(repo, number, label):
    """(actor, role, edit state) for the newest `labeled` event; Nones with a SKIPPED line when unreadable
    or when an `unlabeled` event for the label is as new as or newer than that `labeled` event."""
    events, reason = api(f"repos/{repo}/issues/{number}/events", "--paginate")
    if events is None:
        skipped("labeler", f"{repo}#{number}: {reason}")
        return None, None, None
    if not isinstance(events, list) or not all(isinstance(event, dict) for event in events):
        skipped("labeler", f"{repo}#{number}: unexpected answer")
        return None, None, None
    actor, since, removed = None, None, None
    try:
        # the newest by time, whatever the order of the answer
        for event in events:
            kind = event.get("event")
            if kind in ("labeled", "unlabeled") and str((event.get("label") or {}).get("name")).casefold() == label.casefold():
                when = parse_time(event.get("created_at"))
                if kind == "labeled":
                    if since is None or when > since:
                        actor, since = login_of(event.get("actor")), when
                elif removed is None or when > removed:
                    removed = when
    except (ValueError, AttributeError) as error:
        skipped("labeler", f"{repo}#{number}: {error}")
        return None, None, None
    if not actor:
        skipped("labeler", f"{repo}#{number}: no usable `labeled` event for {label}")
        return None, None, None
    # a tie counts as removed: the order within one second cannot be read from the timestamps
    if removed is not None and removed >= since:
        skipped("labeler", f"{repo}#{number}: label {label} was removed at or after the newest labeled event")
        return None, None, None
    role, reason = role_of(repo, actor)
    if role is None:
        skipped("permission", f"{repo}#{number}: {actor}: {reason}")
    try:
        editors = editors_of(repo, number, events)
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


def has_label(issue, label):
    """True when the issue's own `labels` hold an object named `label`, ignoring case like GitHub's label filter."""
    labels = issue.get("labels") if isinstance(issue, dict) else None
    return isinstance(labels, list) and any(
        isinstance(l, dict) and isinstance(l.get("name"), str) and l["name"].casefold() == label.casefold() for l in labels
    )


def readable_labels(issue):
    """True for an object whose `labels` is a list of objects."""
    return isinstance(issue, dict) and isinstance(issue.get("labels"), list) and all(isinstance(l, dict) for l in issue["labels"])


def created_key(issue):
    """Sort key, oldest first, ties by number; an issue whose `created_at` cannot be read comes after the readable ones."""
    try:
        return (0, parse_time(issue.get("created_at")), issue["number"])
    except ValueError:
        return (1, datetime.min.replace(tzinfo=timezone.utc), 0)


def merge_issues(filtered, unfiltered, label):
    """Both listings as one list: each issue number once, oldest first (ties by number).

    `unfiltered` contributes only the entries that carry the label themselves (case-insensitive);
    an entry of it with unreadable `labels` is passed on as None, which the caller reports. An entry whose
    `created_at` cannot be read keeps its place in listing order after the readable ones. Entries
    that are not objects or have no integer number are passed on unchanged for the caller to report.
    """
    seen, merged, odd = set(), [], []
    candidates = list(filtered)
    for issue in unfiltered:
        if not readable_labels(issue):
            odd.append(None)  # unreadable is not "unlabelled": the caller reports it, never prints it as an issue
        elif has_label(issue, label):
            candidates.append(issue)
    for issue in candidates:
        if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
            odd.append(issue)
            continue
        if issue["number"] in seen:
            continue
        seen.add(issue["number"])
        merged.append(issue)

    return sorted(merged, key=created_key) + odd


def labelled_issues(full, label):
    """Open issues carrying the label, from the label-filtered and the unfiltered listing.

    The filtered listing can lag behind a label that was only just set, so the open issues are read
    without the filter as well. A listing that fails gives one `# SKIPPED issues` line; the other
    one is still used.
    """
    filtered, unfiltered = [], []
    for extra, target in ((["-f", f"labels={label}"], filtered), ([], unfiltered)):
        issues, reason = api(
            f"repos/{full}/issues", "--paginate", "-X", "GET", *extra,
            "-f", "state=open", "-f", "sort=created", "-f", "direction=asc", "-f", "per_page=100",
        )
        which = "unfiltered listing, labelled lines are complete without it" if target is unfiltered else "filtered listing"
        if issues is None:
            skipped("issues", f"{full}: {which}: {reason}")
        elif not isinstance(issues, list):
            skipped("issues", f"{full}: {which}: unexpected answer")
        else:
            target.extend(issues)
    return merge_issues(filtered, unfiltered, label)


SEARCH_LIMIT = 1000  # the search API returns at most this many results


def owner_repositories(owner, label):
    """Repositories of an account or organisation with an open issue carrying the label, from one issue
    search (`user:` matches organisations too). Dies when the owner does not exist. A search that cannot be read
    gives a `# SKIPPED search` line and no repository; one that reports itself incomplete or holds more
    results than the API returns gives the line and the repositories it returned."""
    info, reason = api(f"users/{owner}")
    if not isinstance(info, dict) or not info.get("login"):
        die(f"cannot resolve {owner}: {reason or 'no answer'}")
    login = info["login"]
    pages, reason = api(
        "search/issues", "--paginate", "-X", "GET",
        "-f", f'q=label:"{label}" is:issue is:open user:{login} archived:false', "-f", "per_page=100",
    )
    if pages is None:
        skipped("search", f"{login}: {reason}")
        return login, []
    if isinstance(pages, dict):  # a single page is one object, several are slurped into a list
        pages = [pages]
    names, seen, incomplete, total = [], set(), False, 0
    for page in pages:
        items = page.get("items") if isinstance(page, dict) else None
        if not isinstance(items, list):
            skipped("search", f"{login}: unexpected answer")
            return login, names
        incomplete = incomplete or page.get("incomplete_results") is not False
        total = max(total, page.get("total_count") if isinstance(page.get("total_count"), int) else 0)
        for item in items:
            url = item.get("repository_url") if isinstance(item, dict) else None
            match = re.search(r"/repos/([\w.-]+/[\w.-]+)$", url) if isinstance(url, str) else None
            if match and match.group(1).lower() not in seen:
                seen.add(match.group(1).lower())
                names.append(match.group(1))
    if incomplete:
        skipped("search", f"{login}: the search reported an incomplete result")
    elif total > SEARCH_LIMIT:
        skipped("search", f"{login}: {total} results, the search returns at most {SEARCH_LIMIT}")
    return login, names


def main():
    label, given, repos, explicit, owners = parse_args(sys.argv[1:])
    if not repos and not explicit and not owners:
        repos = [local_repository()]
    scopes = []
    for owner in owners:
        login, found = owner_repositories(owner, label)
        scopes.append((login, found))
        repos = repos + found
    unique, keys = [], set()
    for name in repos:  # a repository named and also found through an owner is read once
        if name.lower() not in keys:
            keys.add(name.lower())
            unique.append(name)
    repos = unique
    resolved = {}
    for name in repos + [r for r, _ in explicit]:
        if name.lower() not in resolved:
            resolved[name.lower()] = repo_info(name)
    for login, found in scopes:
        emit("SCOPE", login, len(found))
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
    labelled = []  # (sort key, repository, line); oldest first across all repositories
    for name in repos:
        full = resolved[name.lower()][0]
        for issue in labelled_issues(full, label):
            if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
                skipped("issues", f"{full}: an entry without an issue number or with unreadable labels")
                continue
            if "pull_request" in issue or (full.lower(), issue["number"]) in printed:
                continue
            printed.add((full.lower(), issue["number"]))
            try:
                actor, role, edit = labeler(full, issue["number"], label)
            except Exception as error:  # last resort: an unforeseen answer must not end the run mid-output
                skipped("labeler", f"{full}#{issue['number']}: {type(error).__name__}: {error}")
                actor = role = edit = None
            labelled.append((created_key(issue), full.lower(), (f"{full}#{issue['number']}", "label", actor, role, issue.get("created_at"), edit)))
    for _key, _repo, fields in sorted(labelled, key=lambda row: (row[0][0], row[0][1], row[1], row[0][2])):
        emit("ISSUE", *fields)


if __name__ == "__main__":
    main()
