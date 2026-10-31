#!/usr/bin/env python3
"""Say whether a PR changes dependencies, or only the version of the root npm package.

Usage: dependency-diff.py <base ref> <head ref>      (run in the checkout)

Compares the PR's diff (`git diff --name-status <base>...<head>`, merge base to
head) for files whose name is on the dependency-file list of pr-reviewer.md
(package.json, package-lock.json, npm-shrinkwrap.json, yarn.lock,
pnpm-lock.yaml, pnpm-workspace.yaml, bun.lock, bun.lockb, pyproject.toml,
uv.lock, requirements*.txt, go.mod, go.sum, Cargo.toml, Cargo.lock,
composer.json, composer.lock), by file name anywhere in the tree.

Output, one line each:
  DEP<TAB><kind><TAB><path>    kind: version-only | changed | added | deleted
  RESULT<TAB><none|version-only|changed>

version-only is limited to the root package.json (equal as parsed JSON after
removing the top-level "version") and the root package-lock.json (equal after
removing the top-level "version" and packages[""].version), and the same
equality must hold between the base ref itself and the head, so that a base that
has moved on in its dependencies since the merge base is "changed". Every other change
to a listed file, and every added or deleted one, is "changed" (a rename counts
as a deleted and an added file). RESULT is none when no listed file changed,
version-only when every DEP line is version-only, otherwise changed.

Exit 1 with the reason on stderr when a git call fails or a root JSON file
cannot be parsed; the caller then treats the dependencies as changed.
"""
import fnmatch
import json
import subprocess
import sys

NAMES = ["package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
         "pnpm-lock.yaml", "pnpm-workspace.yaml", "bun.lock", "bun.lockb", "pyproject.toml", "uv.lock", "requirements*.txt", "go.mod", "go.sum",
         "Cargo.toml", "Cargo.lock", "composer.json", "composer.lock"]


def fail(msg):
    print("dependency-diff.py: " + msg, file=sys.stderr)
    sys.exit(1)


def git(*args):
    p = subprocess.run(["git", *args], capture_output=True)
    if p.returncode != 0:
        fail("git %s failed: %s" % (" ".join(args), p.stderr.decode(errors="replace").strip()))
    return p.stdout


def listed(path):
    return any(fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], n) for n in NAMES)


def load(rev, path):
    try:
        return json.loads(git("show", "%s:%s" % (rev, path)))
    except ValueError as e:
        fail("%s at %s is not valid JSON: %s" % (path, rev, e))


def strip(path, doc):
    if not isinstance(doc, dict):
        fail("%s is not a JSON object" % path)
    doc = dict(doc)
    doc.pop("version", None)
    if path == "package-lock.json":
        pk = doc.get("packages")
        if isinstance(pk, dict) and isinstance(pk.get(""), dict):
            root = dict(pk[""])
            root.pop("version", None)
            doc["packages"] = dict(pk, **{"": root})
    return doc


def main():
    if len(sys.argv) != 3:
        fail("usage: dependency-diff.py <base ref> <head ref>")
    base, head = sys.argv[1:]
    out = git("diff", "--name-status", "-z", "--no-renames", "%s...%s" % (base, head))
    parts = out.decode("utf-8", "surrogateescape").split("\0")
    lines = []
    for i in range(0, len(parts) - 1, 2):
        status, path = parts[i], parts[i + 1]
        if not listed(path):
            continue
        if status.startswith("A"):
            kind = "added"
        elif status.startswith("D"):
            kind = "deleted"
        elif path in ("package.json", "package-lock.json"):
            mb = git("merge-base", base, head).decode().strip()
            h = strip(path, load(head, path))
            same = strip(path, load(mb, path)) == h and strip(path, load(base, path)) == h
            kind = "version-only" if same else "changed"
        else:
            kind = "changed"
        lines.append((kind, path))
    for kind, path in lines:
        print("DEP\t%s\t%s" % (kind, path))
    if not lines:
        result = "none"
    elif all(k == "version-only" for k, _ in lines):
        result = "version-only"
    else:
        result = "changed"
    print("RESULT\t" + result)


main()
