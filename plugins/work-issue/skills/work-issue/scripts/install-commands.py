#!/usr/bin/env python3
"""Print the pinned, lockfile-faithful install command of a project directory. Reads file names and package.json only; runs no tool and writes nothing.

usage: install-commands.py <directory>

<directory> is the root of one project: the directory whose manifest and lockfile the install uses. For
a workspace that is the workspace root, not a member. Why a script: the repository's own install command
may carry a flag that hides a conflict (`npm install --legacy-peer-deps`); the pinned command does not,
and a plain re-install that rewrites the lockfile would put the run on a tree nobody asked for.

Ecosystems (recognised by file name in <directory>; every one found gets its own line):
  npm    package.json + package-lock.json or npm-shrinkwrap.json (no other JS lockfile; `packageManager`
         absent or `npm@...`)
           install  npm ci
           re-lock  npm install --package-lock-only --ignore-scripts
                    (to add or change a dependency by npm: `npm install <pkg>@<range> --package-lock-only
                    --ignore-scripts`; then the install)
  uv     pyproject.toml + uv.lock
           install  uv sync --locked     (with ENV UV_LOCKED=1)
           re-lock  uv lock              (run WITHOUT UV_LOCKED: it makes `uv lock` check only)
  cargo  Cargo.toml + Cargo.lock
           install  cargo fetch --locked
           re-lock  cargo fetch
  go     go.mod (go.mod is its own lock; go.sum is listed when present)
           install  go mod download
           re-lock  go mod tidy          (after `go get <module>@<version>` or a hand edit of go.mod)
The re-lock command is for a run that itself changes dependencies; it is followed by the install.
Recognised but not pinned (SKIPPED, "no pinned install for this ecosystem yet"): pnpm-lock.yaml, yarn.lock,
bun.lock, bun.lockb or a `packageManager` naming pnpm, yarn or bun; composer.json; poetry.lock, pdm.lock,
Pipfile, Pipfile.lock, requirements*.txt (without a pyproject.toml + uv.lock pair); pom.xml; build.gradle,
build.gradle.kts, settings.gradle, settings.gradle.kts.

Output, one tab-separated line per finding on stdout:
  PIN      <ecosystem>  <lockfiles, comma-separated>  <install command>  <re-lock command>
  ENV      <NAME=value>
             environment for the install and for validation of the PIN line before it (uv: UV_LOCKED=1,
             which makes `uv run` fail on a stale uv.lock instead of re-locking it)
  SKIPPED  <source>  <reason>
             recognised but not pinned, a manifest without lockfile, more than one lockfile (or a
             `packageManager` that names another manager than the lockfile's: "ambiguous"), or a
             package.json that cannot be read. The caller stops.
  NONE     no manifest the script knows; printed only when there is no other line. Nothing is installed
             by this script's rules.
Exit codes: 0 no SKIPPED line, 1 at least one SKIPPED line, 2 usage error (no or too many arguments, not
a directory).
"""
import fnmatch
import json
import os
import re
import sys

NOT_YET = "no pinned install for this ecosystem yet"
JS_LOCKFILES = (
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
)
JS_MANAGERS = ("npm", "pnpm", "yarn", "bun")

skipped = False


def clean(text):
    """One line, no tabs: the output format is tab-separated and line-oriented."""
    return re.sub(r"[\t\r\n]+", " ", str(text)).strip()


def emit(*fields):
    print("\t".join(clean(f) for f in fields))


def skip(source, reason):
    global skipped
    skipped = True
    emit("SKIPPED", source, reason)


def pin(ecosystem, lockfiles, install, relock, env=()):
    emit("PIN", ecosystem, ",".join(lockfiles), install, relock)
    for item in env:
        emit("ENV", item)


def javascript(directory, names):
    if "package.json" not in names:
        return False
    try:
        with open(os.path.join(directory, "package.json"), encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        skip("package.json", "cannot be read: %s" % (getattr(error, "strerror", None) or error))
        return True
    if not isinstance(data, dict):
        skip("package.json", "top level is not an object")
        return True
    declared = data.get("packageManager")
    manager = None
    if declared is not None:
        if not isinstance(declared, str):
            skip("package.json", "packageManager is not a string")
            return True
        manager = declared.split("@", 1)[0].strip()
        if manager not in JS_MANAGERS:
            skip("package.json", "packageManager names an unknown manager: %s" % declared)
            return True
    found = [(name, owner) for name, owner in JS_LOCKFILES if name in names]
    if len(found) > 1:
        skip("package.json", "ambiguous: more than one lockfile (%s)" % ", ".join(n for n, _ in found))
    elif not found:
        if manager in ("pnpm", "yarn", "bun"):
            skip("package.json", "packageManager names %s: %s" % (manager, NOT_YET))
        else:
            skip("package.json", "no lockfile (package-lock.json or npm-shrinkwrap.json); for a workspace member pass the workspace root")
    else:
        name, owner = found[0]
        if manager and manager != owner:
            skip("package.json", "ambiguous: packageManager names %s, the lockfile is %s" % (manager, name))
        elif owner != "npm":
            skip(name, NOT_YET)
        else:
            pin("npm", [name], "npm ci", "npm install --package-lock-only --ignore-scripts")
    return True


def python(names):
    others = [n for n in ("poetry.lock", "pdm.lock", "Pipfile", "Pipfile.lock") if n in names]
    requirements = sorted(n for n in names if fnmatch.fnmatchcase(n, "requirements*.txt"))
    found = False
    if "pyproject.toml" in names:
        found = True
        if "uv.lock" in names and others:
            skip("pyproject.toml", "ambiguous: more than one lockfile (uv.lock, %s)" % ", ".join(others))
        elif "uv.lock" in names:
            pin("uv", ["uv.lock"], "uv sync --locked", "uv lock", env=("UV_LOCKED=1",))
        elif others:
            for name in others:
                skip(name, NOT_YET)
        else:
            skip("pyproject.toml", "no lockfile (uv.lock); for a workspace member pass the workspace root")
        return True
    for name in others + requirements:
        found = True
        skip(name, NOT_YET)
    return found


def cargo(names):
    if "Cargo.toml" not in names:
        return False
    if "Cargo.lock" in names:
        pin("cargo", ["Cargo.lock"], "cargo fetch --locked", "cargo fetch")
    else:
        skip("Cargo.toml", "no lockfile (Cargo.lock); for a workspace member pass the workspace root")
    return True


def golang(names):
    if "go.mod" not in names:
        return False
    locks = ["go.mod"] + (["go.sum"] if "go.sum" in names else [])
    pin("go", locks, "go mod download", "go mod tidy")
    return True


def other(names):
    found = False
    for name in ("composer.json", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"):
        if name in names:
            found = True
            skip(name, NOT_YET)
    return found


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print("usage: install-commands.py <directory>", file=sys.stderr)
        return 2
    directory = argv[1]
    try:
        names = {n for n in os.listdir(directory) if os.path.isfile(os.path.join(directory, n))}
    except OSError as error:
        skip("directory", getattr(error, "strerror", None) or error)
        return 1
    seen = [javascript(directory, names), python(names), cargo(names), golang(names), other(names)]
    if not any(seen):
        emit("NONE")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
