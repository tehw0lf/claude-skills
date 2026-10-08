#!/usr/bin/env python3
"""Report settings that make `npm install` hide peer-dependency conflicts. The script itself writes nothing.

usage: check-install-config.py <directory>

<directory> is the root of a repository (the one holding package.json and any .npmrc). Checked:
  - <directory>/.npmrc: every `legacy-peer-deps` and `force` entry whose value is not exactly `false`
  - `npm config get legacy-peer-deps` and `npm config get force`, run with <directory> as working
    directory (so project .npmrc, user and global config, npm_config_* environment variables are all
    seen): reported when the value npm prints is not exactly `false`
  - <directory>/package.json: `overrides`, `resolutions` and `pnpm.overrides`. npm reads overrides
    from the root package.json only, so no other package.json is looked at

npm is started with --logs-max=0 --no-update-notifier, so it writes no debug log and runs no update
check; neither option is one of the checked keys, so the reported values do not change.

"Not exactly `false`" is deliberate: npm prints the configured string as written (`1`, `0`, `False`)
and `true` for a bare key or an empty value; the script does not reproduce npm's own parsing of those
strings, anything but `false` is reported and the caller stops. That includes an .npmrc value with a
trailing comment (`false ; note`): it is reported although npm reads it as `false`.

Output, one tab-separated line per finding on stdout, nothing for a clean directory:
  NPMRC     <key>  <value as written, empty for a bare key>  .npmrc
              an entry of <directory>/.npmrc; reported even when the effective value is `false`
              (an environment variable can override it, anyone else installing here gets the file)
  CONFIG    <key>  <value printed by npm config get>  <project|user|global|builtin|env|cli|unknown>
              the effective value and the origin `npm config ls` names for it (project: the .npmrc of <directory>;
              user, global: the .npmrc of the user or of the npm prefix; builtin: the npmrc inside the
              npm installation; env: npm_config_* variables; cli: a command-line flag;
              unknown: no uncommented line was found for it)
  OVERRIDE  <overrides|resolutions|pnpm.overrides>  <package or selector, - for a field that is not an object>  <value, compact JSON>
              one line per top-level entry of a non-empty field; an empty object prints nothing; a
              field that is present but not an object (also null) is printed as one line
  SKIPPED   <source>  <reason>
              a source that could not be read: package.json, .npmrc, npm, npm config ls. Its
              findings are missing, not empty. The other sources are still checked. For npm the
              reason is npm's first `npm error` line; without one, its whole stderr; with an
              empty stderr, `exit <code>`.
Exit codes: 0 every source was read (with or without finding lines), 1 at least one SKIPPED line,
2 usage error (no or too many arguments, not a directory). Finding lines do not change the exit code:
the caller stops on any output line.
"""
import json
import os
import re
import subprocess
import sys

KEYS = ("legacy-peer-deps", "force")
NPM_FLAGS = ("--logs-max=0", "--no-update-notifier")
OVERRIDE_FIELDS = ("overrides", "resolutions")  # pnpm.overrides is handled separately


def clean(text):
    """One line, no tabs: the output format is tab-separated and line-oriented."""
    return re.sub(r"[\t\r\n]+", " ", str(text)).strip()


def why(error):
    """A reason without the path (the caller knows which file it asked for)."""
    return getattr(error, "strerror", None) or error


def emit(*fields):
    print("\t".join(clean(f) for f in fields))


def check_npmrc(directory):
    """Returns True when .npmrc was read (or does not exist)."""
    path = os.path.join(directory, ".npmrc")
    try:
        with open(path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return True
    except (OSError, UnicodeDecodeError) as error:
        emit("SKIPPED", ".npmrc", why(error))
        return False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped[0] in ";#":
            continue
        key, separator, value = stripped.partition("=")
        key = key.strip()
        if key not in KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if separator and value == "false":
            continue
        emit("NPMRC", key, value, ".npmrc")
    return True


def npm_origins(directory):
    """Maps key -> origin from `npm config ls` (uncommented lines only); None when it failed."""
    try:
        result = subprocess.run(
            ["npm", "config", "ls", *NPM_FLAGS], cwd=directory, capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    origins = {}
    origin = "unknown"
    for line in result.stdout.splitlines():
        header = re.match(r'^; "([^"]+)" config from', line)
        if header:
            origin = header.group(1)
            continue
        entry = re.match(r"^([A-Za-z0-9_@/.:-]+)\s*=", line)
        if entry and entry.group(1) in KEYS:
            origins[entry.group(1)] = origin
    return origins


def check_npm_config(directory):
    """Returns True when every effective value was read."""
    values = {}
    for key in KEYS:
        try:
            result = subprocess.run(
                ["npm", "config", "get", key, *NPM_FLAGS], cwd=directory, capture_output=True, text=True, timeout=120
            )
        except FileNotFoundError:
            emit("SKIPPED", "npm", "not found")
            return False
        except (OSError, subprocess.SubprocessError) as error:
            emit("SKIPPED", "npm", error)
            return False
        if result.returncode != 0:
            # npm may print warnings first (`npm warn using --force ...`): the reason is its first error line
            reason = (
                next((l.strip() for l in result.stderr.splitlines() if l.strip().startswith("npm error")), None)
                or clean(result.stderr)
                or "exit %d" % result.returncode
            )
            emit("SKIPPED", "npm", "npm config get %s failed: %s" % (key, reason))
            return False
        values[key] = result.stdout.strip()
    hits = {key: value for key, value in values.items() if value != "false"}
    if not hits:
        return True
    origins = npm_origins(directory)
    for key, value in hits.items():
        emit("CONFIG", key, value, (origins or {}).get(key, "unknown"))
    if origins is None:
        emit("SKIPPED", "npm config ls", "failed; the origin of the CONFIG lines is unknown")
        return False
    return True


def check_package_json(directory):
    """Returns True when package.json was read."""
    path = os.path.join(directory, "package.json")
    try:
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        emit("SKIPPED", "package.json", why(error))
        return False
    if not isinstance(data, dict):
        emit("SKIPPED", "package.json", "top-level value is not an object")
        return False
    fields = [(name, data[name]) for name in OVERRIDE_FIELDS if name in data]
    pnpm = data.get("pnpm")
    if isinstance(pnpm, dict) and "overrides" in pnpm:
        fields.append(("pnpm.overrides", pnpm["overrides"]))
    for name, value in fields:
        if isinstance(value, dict):
            for selector, wanted in value.items():
                emit("OVERRIDE", name, selector, json.dumps(wanted, separators=(",", ":"), ensure_ascii=False))
        else:
            emit("OVERRIDE", name, "-", json.dumps(value, separators=(",", ":"), ensure_ascii=False))
    return True


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print(__doc__.split("\n", 3)[2], file=sys.stderr)
        print("error: expected exactly one argument, an existing directory", file=sys.stderr)
        return 2
    directory = argv[1]
    results = [check_npmrc(directory), check_npm_config(directory), check_package_json(directory)]
    sys.stdout.flush()
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
