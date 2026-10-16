#!/usr/bin/env python3
"""Report uv, Cargo, Go and Composer settings that hide a dependency conflict; reads files only, runs no tool.

usage: check-lock-overrides.py <directory>

<directory> is the directory the install runs in (the uv project or workspace root, the Cargo workspace
root, the Go module directory, the Composer root). Only these four files in that directory are read;
the same keys set elsewhere (.cargo/config.toml, go.work, Composer's global config, member manifests of
a workspace) are not.
  - pyproject.toml: `[tool.uv] override-dependencies` (uv ignores the declared requirements of an
    overridden package); `constraint-dependencies` is not read
  - Cargo.toml: every `[patch.<source>]` table and every `[replace]` entry (they swap the code behind a
    requirement)
  - go.mod: every `replace` directive, single-line and in `replace ( ... )` blocks (it swaps the code
    behind a requirement); `exclude` is not read
  - composer.json: `config.platform` (Composer stops checking dependencies against the real platform)
    and inline aliases, i.e. values in `require` or `require-dev` that contain ` as ` (a branch then
    satisfies a constraint it does not declare)
An absent key is not reported, and neither is an empty list, table or object of the right type.

Output, one tab-separated line per finding (fields have tabs and newlines replaced by a space), nothing
for a clean directory:
  UV        override-dependencies  <requirement>
  CARGO     patch.<source>         <crate>  <value, compact JSON>
  CARGO     replace                <package spec>  <value, compact JSON>
  GO        replace                <old path[ version]>  <new path[ version]>
  COMPOSER  config.platform        <name>  <value, compact JSON>
  COMPOSER  alias                  <require|require-dev>  <package>  <constraint as written>
  SKIPPED   <file>                 <reason>        a file that exists but could not be read or parsed
  SKIPPED   manifest               none of the four files exists (a call with the wrong directory)
A value of the wrong type (a key that must be a table, object or list, and is not) prints one line with `-`
in place of the entries and the compact JSON, also when that value is empty (`[]`, `""`, `0`).

Exit codes: 0 every file that exists was read (findings do not change it), 1 at least one SKIPPED line,
2 usage error.
"""
import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

FILES = ("pyproject.toml", "Cargo.toml", "go.mod", "composer.json")


def clean(text):
    """One line, no tabs: the output format is tab-separated and line-oriented."""
    return re.sub(r"[\t\r\n]+", " ", str(text)).strip()


def emit(*fields):
    print("\t".join(clean(f) for f in fields))


def js(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def why(error):
    return getattr(error, "strerror", None) or error


def load_toml(path):
    if tomllib is None:
        raise ValueError("tomllib unavailable (Python 3.11 or later needed)")
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    return data


def check_uv(path):
    data = load_toml(path)
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        emit("UV", "tool", "-", js(tool))
        return
    uv = tool.get("uv", {})
    if not isinstance(uv, dict):
        emit("UV", "tool.uv", "-", js(uv))
        return
    if "override-dependencies" not in uv:
        return
    value = uv["override-dependencies"]
    if isinstance(value, list):
        for item in value:
            emit("UV", "override-dependencies", item)
    else:
        emit("UV", "override-dependencies", "-", js(value))


def check_cargo(path):
    data = load_toml(path)
    patch = data.get("patch", {})
    if isinstance(patch, dict):
        for source, crates in patch.items():
            if isinstance(crates, dict):
                for crate, value in crates.items():
                    emit("CARGO", "patch." + source, crate, js(value))
            else:
                emit("CARGO", "patch." + source, "-", js(crates))
    else:
        emit("CARGO", "patch", "-", js(patch))
    replace = data.get("replace", {})
    if isinstance(replace, dict):
        for spec, value in replace.items():
            emit("CARGO", "replace", spec, js(value))
    else:
        emit("CARGO", "replace", "-", js(replace))


def go_tokens(line):
    """Tokens of a go.mod line: whitespace-separated, `//` comment removed, quoted strings unquoted."""
    tokens = []
    for match in re.finditer(r'"((?:[^"\\]|\\.)*)"|`([^`]*)`|(//.*)|([()])|([^\s()]+)', line):
        if match.group(3) is not None:
            break
        if match.group(1) is not None:
            tokens.append(match.group(1))
        elif match.group(2) is not None:
            tokens.append(match.group(2))
        elif match.group(4) is not None:
            tokens.append(match.group(4))
        else:
            word = match.group(5)
            if "//" in word:
                word = word.split("//", 1)[0]
                if word:
                    tokens.append(word)
                break
            tokens.append(word)
    return tokens


def go_directive(tokens):
    """The output fields of one replace directive; ValueError when it does not fit the grammar."""
    if "(" in tokens or ")" in tokens:
        raise ValueError("malformed replace directive: " + " ".join(tokens))
    if "=>" not in tokens:
        raise ValueError("replace directive without =>: " + " ".join(tokens))
    index = tokens.index("=>")
    old, new = tokens[:index], tokens[index + 1:]
    if not 1 <= len(old) <= 2 or not 1 <= len(new) <= 2:
        raise ValueError("malformed replace directive: " + " ".join(tokens))
    return ("GO", "replace", " ".join(old), " ".join(new))


def check_go(path):
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    findings = []
    in_block = False
    for number, line in enumerate(lines, 1):
        tokens = go_tokens(line)
        if not tokens:
            continue
        if in_block:
            if tokens == [")"]:
                in_block = False
            else:
                findings.append(tokens)
        elif tokens[0] == "replace":
            if tokens[1:] == ["("]:
                in_block = True
            else:
                findings.append(tokens[1:])
    if in_block:
        raise ValueError("replace ( block is never closed")
    # all directives are checked before any line is printed: a file that cannot be parsed gives no findings
    for fields in [go_directive(tokens) for tokens in findings]:
        emit(*fields)


def check_composer(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("top-level value is not an object")
    config = data.get("config", {})
    if not isinstance(config, dict):
        emit("COMPOSER", "config", "-", js(config))
    elif "platform" in config:
        platform = config["platform"]
        if isinstance(platform, dict):
            for name, value in platform.items():
                emit("COMPOSER", "config.platform", name, js(value))
        else:
            emit("COMPOSER", "config.platform", "-", js(platform))
    for section in ("require", "require-dev"):
        packages = data.get(section, {})
        if not isinstance(packages, dict):
            emit("COMPOSER", section, "-", js(packages))
            continue
        for package, constraint in packages.items():
            if isinstance(constraint, str) and re.search(r"\s+as\s+", constraint):
                emit("COMPOSER", "alias", section, package, constraint)


CHECKS = {
    "pyproject.toml": check_uv,
    "Cargo.toml": check_cargo,
    "go.mod": check_go,
    "composer.json": check_composer,
}


def main(argv):
    if len(argv) != 2:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    directory = Path(argv[1])
    if not directory.is_dir():
        print("not a directory: " + argv[1], file=sys.stderr)
        return 2
    skipped = False
    present = False
    for name in FILES:
        path = directory / name
        if not path.is_file():
            continue
        present = True
        try:
            CHECKS[name](path)
        except (OSError, UnicodeError, ValueError) as error:
            # json.JSONDecodeError and tomllib.TOMLDecodeError are ValueErrors
            emit("SKIPPED", name, why(error))
            skipped = True
    if not present:
        emit("SKIPPED", "manifest", "none of " + ", ".join(FILES))
        skipped = True
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
