#!/usr/bin/env python3
"""Report settings that make an npm, Yarn Berry or pnpm install hide peer-dependency conflicts; writes nothing.

usage: check-install-config.py <directory>

<directory> is the directory the install runs in (the one holding package.json and any .npmrc; for pnpm
the one holding pnpm-workspace.yaml). Checked:
  - <directory>/.npmrc: every `legacy-peer-deps` and `force` entry whose value is not exactly `false`,
    every `strict-peer-dependencies` entry whose value is not exactly `true`, every `auto-install-peers`
    entry whose value is not exactly `false`, and every `extension-file` entry
  - `npm config get legacy-peer-deps` and `npm config get force`, run with <directory> as working
    directory (so project .npmrc, user and global config, npm_config_* environment variables are all
    seen): reported when the value npm prints is not exactly `false`
  - <directory>/package.json: `overrides`, `resolutions`, `packageExtensions`, `pnpm.overrides`,
    `pnpm.packageExtensions` and `pnpm.peerDependencyRules`. npm reads overrides from the root
    package.json only, so no other package.json is looked at
  - <directory>/.npm-extension.mjs and .npm-extension.cjs: reported when the file exists
  - <directory>/.yarnrc.yml: the top-level keys `packageExtensions` and `logFilters`
  - <directory>/pnpm-workspace.yaml: the top-level keys `overrides`, `packageExtensions` and
    `peerDependencyRules`, `strictPeerDependencies` (not exactly `true`) and `autoInstallPeers`
    (not exactly `false`)
  An absent key is not reported, whatever the package manager's default is: the check looks at the
  settings only. `npm config get` is run for `legacy-peer-deps` and `force` only, and `extension-file` is
  read from the .npmrc only (npm refuses to print it).

npm is started with --logs-max=0 --no-update-notifier --color=false, so it writes no debug log, runs
no update check and prints no escape sequences (a color=always setting would otherwise prefix its
stderr lines with them, and the first `npm error` line could not be found); none of the options is
one of the checked keys, so the reported values do not change.

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
  OVERRIDE  <overrides|resolutions|packageExtensions|pnpm.overrides|pnpm.packageExtensions|pnpm.peerDependencyRules>
            <package or selector, - for a field that is not an object>  <value, compact JSON>
              one line per top-level entry of a non-empty field; an empty object prints nothing; a
              field that is present but not an object (also null) is printed as one line
  EXTENSION <.npm-extension.mjs|.npm-extension.cjs>
              an npm extension file exists; it is code, its effect cannot be read without running it
  YAML      <.yarnrc.yml|pnpm-workspace.yaml>  <key>  <entry, - for a scalar or a list item>  <value as written, one line>
              one line per top-level entry of a non-empty checked collection key, one line for a
              checked boolean key whose value is not the safe one (a missing value prints as empty);
              `{}` and `[]` print nothing. The file is read by a narrow scan of top-level keys, not by a
              YAML library
  SKIPPED   <source>  <reason>
              a source that could not be read: package.json, .npmrc, npm, npm config ls, .npm-extension.mjs,
              .npm-extension.cjs, .yarnrc.yml, pnpm-workspace.yaml (also a YAML file that uses a construct
              the scan cannot follow: flow-style document, indented document, several documents, merge key,
              anchor or alias, tab indentation, a backslash escape in a double-quoted key, a line that is no key). Its
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

KEYS = ("legacy-peer-deps", "force")  # the keys `npm config get` is asked for
# .npmrc keys and the one value that does not hide a conflict; None: any entry is reported
NPMRC_SAFE = {
    "legacy-peer-deps": "false",
    "force": "false",
    "strict-peer-dependencies": "true",
    "auto-install-peers": "false",
    "extension-file": None,
}
EXTENSION_FILES = (".npm-extension.mjs", ".npm-extension.cjs")
# YAML file -> {top-level key: None for a collection (one line per entry), or the safe scalar value}
YAML_KEYS = {
    ".yarnrc.yml": {"packageExtensions": None, "logFilters": None},
    "pnpm-workspace.yaml": {
        "overrides": None,
        "packageExtensions": None,
        "peerDependencyRules": None,
        "strictPeerDependencies": "true",
        "autoInstallPeers": "false",
    },
}
PNPM_FIELDS = ("overrides", "packageExtensions", "peerDependencyRules")
NPM_FLAGS = ("--logs-max=0", "--no-update-notifier", "--color=false")
OVERRIDE_FIELDS = ("overrides", "resolutions", "packageExtensions")  # the pnpm.* fields are handled separately


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
        if key not in NPMRC_SAFE:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if separator and NPMRC_SAFE[key] is not None and value == NPMRC_SAFE[key]:
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
    if isinstance(pnpm, dict):
        fields.extend(("pnpm." + name, pnpm[name]) for name in PNPM_FIELDS if name in pnpm)
    for name, value in fields:
        if isinstance(value, dict):
            for selector, wanted in value.items():
                emit("OVERRIDE", name, selector, json.dumps(wanted, separators=(",", ":"), ensure_ascii=False))
        else:
            emit("OVERRIDE", name, "-", json.dumps(value, separators=(",", ":"), ensure_ascii=False))
    return True


def check_extension_files(directory):
    """Returns True when every extension file entry could be looked at."""
    ok = True
    for name in EXTENSION_FILES:
        try:
            os.lstat(os.path.join(directory, name))
        except FileNotFoundError:
            continue
        except OSError as error:
            emit("SKIPPED", name, why(error))
            ok = False
            continue
        emit("EXTENSION", name)
    return ok


class Unsupported(Exception):
    """A construct the narrow YAML scan cannot follow."""


KEY_LINE = re.compile(r"""^(?:"([^"]*)"|'([^']*)'|([^\s#:'"\-\[\]{}&*!|>%@`,?][^:#]*?))\s*:(?:\s+(.*))?$""")
ALIAS = re.compile(r"(?:^|[\s:\[{,-])[&*][A-Za-z0-9_-]")


def strip_comment(text):
    text = text.strip()
    if text.startswith("#"):
        return ""
    if text and text[0] in "\"'":
        end = text.find(text[0], 1)
        return text if end < 0 else text[: end + 1]
    return re.split(r"\s#", text, maxsplit=1)[0].strip()


def unquote(text):
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def yaml_entries(lines, wanted):
    """Returns [(key, entry, value)] for the wanted top-level keys; raises Unsupported."""
    content = [(n, l) for n, l in enumerate(lines, 1) if l.strip() and not l.lstrip().startswith("#")]
    if content and content[0][1].lstrip()[0] in "{[":
        raise Unsupported("flow-style document")
    seen_start = False
    top = []  # (line number, key, inline value) of every top-level key
    block_of = {}  # line number of a top-level key -> its indented lines
    current = None
    for number, line in content:
        if line.startswith(("---", "...")):
            if line.rstrip() == "---" and not seen_start:
                continue
            raise Unsupported("line %d: several documents or a directive" % number)
        seen_start = True
        if line[0] in " \t":
            if current is None:
                raise Unsupported("line %d: indented before the first top-level key" % number)
            block_of[current].append((number, line))
            continue
        if current is not None and line.startswith("-"):
            block_of[current].append((number, line))  # a list at the key's own indentation
            continue
        match = KEY_LINE.match(line.rstrip())
        if not match:
            raise Unsupported("line %d is not a top-level key" % number)
        key = next(g for g in match.groups()[:3] if g is not None)
        if match.group(1) is not None and "\\" in key:
            raise Unsupported("line %d: escape in a double-quoted key" % number)
        if key == "<<":
            raise Unsupported("line %d: merge key" % number)
        top.append((number, key, strip_comment(match.group(4) or "")))
        current = number
        block_of[current] = []
    found = []
    for number, key, inline in top:
        if key not in wanted:
            continue
        safe = wanted[key]
        block = block_of[number]
        for line_number, line in block:
            if re.match(r"\s*[^\s].*?<<\s*:", line) or ALIAS.search(strip_comment(line)):
                raise Unsupported("line %d: anchor, alias or merge key under %s" % (line_number, key))
            if re.match(r" *\t", line):
                raise Unsupported("line %d: tab indentation under %s" % (line_number, key))
        if ALIAS.search(" " + inline) or inline[:1] in ("!", "|", ">"):
            raise Unsupported("line %d: %s uses an anchor, alias, tag or block scalar" % (number, key))
        if safe is not None:  # a scalar setting
            if block:
                raise Unsupported("line %d: %s is not a scalar" % (number, key))
            if unquote(inline) != safe:
                found.append((key, "-", inline))
            continue
        if inline:
            if block:
                raise Unsupported("line %d: %s has a value and a block" % (number, key))
            if inline not in ("{}", "[]"):
                found.append((key, "-", inline))
            continue
        level = None
        entry = None
        for line_number, line in block:
            indent = len(line) - len(line.lstrip(" "))
            if level is None:
                level = indent
            if indent < level:
                raise Unsupported("line %d: inconsistent indentation under %s" % (line_number, key))
            if indent > level:
                if entry is None:
                    raise Unsupported("line %d: inconsistent indentation under %s" % (line_number, key))
                entry[2].append(line.strip())
                continue
            text = line.strip()
            if text.startswith("- ") or text == "-":
                name, rest = "-", text[1:].strip()
            else:
                match = KEY_LINE.match(text)
                if not match:
                    raise Unsupported("line %d: no entry under %s" % (line_number, key))
                name = next(g for g in match.groups()[:3] if g is not None)
                rest = (match.group(4) or "").strip()
            entry = [name, key, [rest] if rest else []]
            found.append(entry)
    return [f if isinstance(f, tuple) else (f[1], f[0], " ".join(f[2])) for f in found]


def check_yaml(directory):
    """Returns True when every YAML file was read (or does not exist)."""
    ok = True
    for name, wanted in YAML_KEYS.items():
        try:
            with open(os.path.join(directory, name), encoding="utf-8-sig") as handle:
                lines = handle.read().splitlines()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError) as error:
            emit("SKIPPED", name, why(error))
            ok = False
            continue
        try:
            found = yaml_entries(lines, wanted)
        except Unsupported as error:
            emit("SKIPPED", name, error)
            ok = False
            continue
        for key, entry, value in found:
            emit("YAML", name, key, entry, value)
    return ok


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print(__doc__.split("\n", 3)[2], file=sys.stderr)
        print("error: expected exactly one argument, an existing directory", file=sys.stderr)
        return 2
    directory = argv[1]
    results = [
        check_npmrc(directory),
        check_npm_config(directory),
        check_package_json(directory),
        check_extension_files(directory),
        check_yaml(directory),
    ]
    sys.stdout.flush()
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
