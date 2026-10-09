#!/usr/bin/env python3
"""Print the commands a repository itself uses to install, format, lint, test, e2e-test and build, with their source. Reads files only; runs no tool and writes nothing.

usage: repo-commands.py <checkout root>

Why a script: the run uses the repository's own way of working, in this order of sources: (1) the inputs of
the CI caller of the reusable workflows, (2) what the repository documents (CLAUDE.md, README, package.json
and composer.json scripts, Makefile, build configuration), (3) the usual commands of the language. The
mechanical parts of that (reading the caller's `with:` block, composing the command the way CI runs it,
listing scripts and targets, recognising the ecosystem) are done here. Choosing a command from the prose of
a doc file and matching it to a kind is judgment: the script only names the files (DOC lines) and SKILL.md
says how to read them.

Directories covered: the checkout root and the `root_dir` of every caller. Output, one tab-separated line
per fact on stdout (<dir> is relative to the checkout root, "." for the root):
  CALLER   <workflow file> <job> <dir> <tool>
             a job whose `uses:` is tehw0lf/workflows/.github/workflows/build-test-publish.yml@<ref>.
             <tool> is "none" when the input is absent and "unknown" when it cannot be read as a plain
             value (a SKIPPED line follows): CI may run commands for it, the file has to be read.
  CI       <dir> <kind> <command> <workflow file>
             the command CI runs for that kind, composed as test-and-build.yml of tehw0lf/workflows does
             (see below), in CI's order. kind: install, format, lint, test, e2e, build. build is the
             `build_branch` input (build_main and post_build_script run only on push, so a pull request
             does not run them). A line is printed only for an input that is set.
  TOOLCHAIN <dir> <what> <workflow file>
             a toolchain CI provisions (lua, jdk, go, rust) that this run does not install; a command that
             needs one that is missing is "cannot run".
  SCRIPT   <dir> <invocation> <body>
             a script of package.json (invocation `npm run x`, `yarn x`, `pnpm run x` or `bun run x`,
             the manager taken from the lockfile, else `packageManager`, else npm) or of composer.json
             (`composer run-script x`). <body> is the script text on one line.
  MAKE     <dir> <invocation>
             a Makefile target (`make test`), special targets starting with "." left out.
  PROJECT  <dir> <ecosystem> <manifest> <lockfiles | wrapper | ->
             one line per ecosystem found: npm, yarn, pnpm, bun, nx, uv, poetry, pdm, pipenv,
             requirements, python (a pyproject.toml of none of those), cargo, go, gradle (fifth field
             `gradlew` or `-`), maven (`mvnw` or `-`), composer. A directory with none prints
             `PROJECT <dir> none`. Two JS lockfiles print the manager `ambiguous`: a note, not a stop.
  DOC      <dir> <file>
             CLAUDE.md, README*, CONTRIBUTING*, DEVELOPMENT* in the directory.
  WORKFLOW <file>
             a workflow file under .github/workflows that holds no caller; its plain `run:` steps are
             documentation of the CI, read by the skill.
  FALLBACK <dir> <kind> <command>
             the usual command of the ecosystem, for a kind no other source names. Rows:
               npm   install `npm ci` (package-lock.json or npm-shrinkwrap.json present) else `npm install`
               yarn  `yarn install`        pnpm  `pnpm install`
               nx    `npx nx run-many -t lint` / `-t test` / `-t build`, plus `-t e2e` when an e2e target
                     is declared
               uv    `uv sync` / `uv run ruff check` / `uv run pytest` / `uv build`
               cargo `cargo fmt --check` / `cargo clippy -- -D warnings` / `cargo test` / `cargo build`
               go    `go vet ./...` / `go test ./...` / `go build ./...`
               gradle `./gradlew build` (wrapper only), kind build
             Only rows that were run against the real tool are listed. bun, Maven, Composer, Gradle
             without a wrapper, Poetry, PDM, Pipenv, requirements-only and plain pyproject projects have
             no row: their commands come from the sources above.
  SKIPPED  <source> <reason>
             an input that is not a plain scalar (a `${{ }}` expression, a block or multi-line scalar), a
             `with:` that is not a block mapping (a flow mapping), a
             root_dir outside the checkout, or a file that cannot be read. The skill reads that file
             itself. Not a stop.

CI composition (a copy of test-and-build.yml of tehw0lf/workflows; a change there is mirrored here): the
command is `<tool> <input>` for install, format, lint, test, e2e and build_branch, in that order, with
these exceptions:
  - tool npm always installs with `npm ci`, tool yarn with `yarn install --frozen-lockfile`; a set
    `install` input is ignored for both.
  - tool cargo also runs, between e2e and build, `cargo fmt --all -- --check` (kind format, unless
    enable_rustfmt is false) and `cargo clippy --all-targets --all-features <clippy_args>` (kind lint,
    unless enable_clippy is false; `--features "<cargo_features>"` instead of `--all-features` when
    cargo_features is set; clippy_args defaults to `-- -D warnings`).
  - tool absent or `none` runs none of them.
Toolchain defaults: java_version 25 (tool ./gradlew or mvn), go_version (empty: the go.mod version),
rust_version stable, lua_version 5.4.8 (enable_lua).

The `with:` block is read by indentation, without PyYAML, so the script needs no `uv run`.
Exit codes: 0, 2 usage error (no or too many arguments, not a directory).
"""
import fnmatch
import json
import os
import re
import sys

BTP = re.compile(r"^tehw0lf/workflows/\.github/workflows/build-test-publish\.yml@")
JS_LOCKFILES = (
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
    ("yarn.lock", "yarn"),
    ("pnpm-lock.yaml", "pnpm"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
)
RUN = {"npm": "npm run {}", "yarn": "yarn {}", "pnpm": "pnpm run {}", "bun": "bun run {}"}
DOC_PATTERNS = ("CLAUDE.md", "README*", "CONTRIBUTING*", "DEVELOPMENT*")
OUT = []


def emit(*fields):
    OUT.append("\t".join(re.sub(r"\s+", " ", str(f)).strip() if i else str(f)
                         for i, f in enumerate(fields)))


def skipped(source, reason):
    emit("SKIPPED", source, reason)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        skipped(path, "cannot be read: %s" % e.__class__.__name__)
        return None


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def blank(line):
    s = line.strip()
    return not s or s.startswith("#")


def parse_value(raw, has_deeper):
    """Return ("scalar", text) or ("complex", None) or ("expr", None) for the text after `key:`."""
    s = raw.strip()
    if s[:1] in ("|", ">"):
        return ("complex", None)
    if s[:1] == '"':
        m = re.match(r'"((?:[^"\\]|\\.)*)"\s*(?:#.*)?$', s)
        if not m:
            return ("complex", None)
        text = m.group(1)
        try:
            text = json.loads('"%s"' % text)
        except ValueError:
            text = text.replace('\\"', '"').replace("\\\\", "\\")
    elif s[:1] == "'":
        m = re.match(r"'((?:[^']|'')*)'\s*(?:#.*)?$", s)
        if not m:
            return ("complex", None)
        text = m.group(1).replace("''", "'")
    else:
        text = re.split(r"\s#", s, maxsplit=1)[0].strip()
        if has_deeper:
            return ("complex", None)
    if has_deeper:
        return ("complex", None)
    if "${{" in text:
        return ("expr", None)
    return ("scalar", text)


def children(lines, start, parent_indent):
    """Direct `key: value` children of the block after lines[start]: [(key, raw, has_deeper, idx)]."""
    out = []
    i = start + 1
    child = None
    while i < len(lines):
        line = lines[i]
        if blank(line):
            i += 1
            continue
        ind = indent_of(line)
        if ind <= parent_indent:
            break
        if child is None:
            child = ind
        if ind == child:
            m = re.match(r"([A-Za-z0-9_.\-]+)\s*:(?:\s+(.*))?$", line.strip())
            if m:
                out.append([m.group(1), m.group(2) or "", False, i])
        elif ind > child and out:
            out[-1][2] = True
        i += 1
    return out


def parse_callers(rel, text):
    lines = text.splitlines()
    callers = []
    for i, line in enumerate(lines):
        if re.match(r"jobs:\s*(#.*)?$", line):
            for job, _raw, _deeper, jidx in children(lines, i, 0):
                props = children(lines, jidx, indent_of(lines[jidx]))
                d = {k: (raw, deeper, idx) for k, raw, deeper, idx in props}
                if "uses" not in d:
                    continue
                kind, val = parse_value(d["uses"][0], False)
                if kind != "scalar" or not BTP.match(val):
                    continue
                inputs = {}
                if "with" in d and d["with"][0].strip() and not d["with"][0].strip().startswith("#"):
                    inputs = None
                elif "with" in d:
                    for k, raw, deeper, _ in children(lines, d["with"][2], indent_of(lines[d["with"][2]])):
                        inputs[k] = parse_value(raw, deeper)
                callers.append((job, inputs))
            break
    return callers


def safe_dir(root, d):
    d = d.strip().rstrip("/")
    d = re.sub(r"^(\./)+", "", d) or "."
    full = os.path.realpath(os.path.join(root, d))
    r = os.path.realpath(root)
    if full != r and not full.startswith(r + os.sep):
        return None
    return d


class Caller:
    def __init__(self, rel, job, inputs, root):
        self.rel, self.job, self.inputs, self.root = rel, job, inputs, root
        self.bad = set()

    def get(self, name, default=""):
        if self.inputs is None:  # `with:` is not a block mapping (flow mapping, alias)
            if "with" not in self.bad:
                self.bad.add("with")
                skipped("%s:%s" % (self.rel, self.job), "`with:` is not a block mapping, read the file")
            return None
        kind, val = self.inputs.get(name, ("scalar", default))
        if kind == "scalar":
            return val
        if name not in self.bad:
            self.bad.add(name)
            skipped("%s:%s" % (self.rel, self.job),
                    "input %s is %s, read the file" % (name, "an expression" if kind == "expr" else "not a plain scalar"))
        return None

    def flag(self, name, default):
        v = self.get(name, "true" if default else "false")
        if v is None:
            return None
        return v.strip().lower() == "true"


def ci_lines(c, d):
    tool = c.get("tool", "none")
    if tool is None or tool in ("", "none"):
        return
    ci = []

    def add(kind, cmd):
        ci.append((kind, cmd))

    install = c.get("install")
    if tool == "npm":
        add("install", "npm ci")
    elif tool == "yarn":
        add("install", "yarn install --frozen-lockfile")
    elif install:
        add("install", "%s %s" % (tool, install))
    for kind in ("format", "lint", "test", "e2e"):
        v = c.get(kind)
        if v:
            add(kind, "%s %s" % (tool, v))
    if tool == "cargo":
        fmt, clippy = c.flag("enable_rustfmt", True), c.flag("enable_clippy", True)
        if fmt:
            add("format", "cargo fmt --all -- --check")
        if clippy:
            args = c.get("clippy_args", "-- -D warnings")
            feats = c.get("cargo_features", "")
            if args is not None and feats is not None:
                add("lint", ("cargo clippy --all-targets --features \"%s\" %s" % (feats, args)
                             if feats else "cargo clippy --all-targets --all-features %s" % args).strip())
    v = c.get("build_branch")
    if v:
        add("build", "%s %s" % (tool, v))
    for kind, cmd in ci:
        emit("CI", d, kind, cmd, c.rel)
    # toolchains
    if c.flag("enable_lua", False):
        emit("TOOLCHAIN", d, "lua %s" % (c.get("lua_version", "5.4.8") or "5.4.8"), c.rel)
    if tool in ("./gradlew", "mvn"):
        emit("TOOLCHAIN", d, "jdk %s" % (c.get("java_version", "25") or "25"), c.rel)
    elif tool == "go":
        emit("TOOLCHAIN", d, "go %s" % (c.get("go_version", "") or "from go.mod"), c.rel)
    elif tool == "cargo":
        emit("TOOLCHAIN", d, "rust %s" % (c.get("rust_version", "stable") or "stable"), c.rel)


def js_manager(path, files):
    found = sorted({m for f, m in JS_LOCKFILES if f in files})
    if len(found) > 1:
        return "ambiguous", [f for f, _ in JS_LOCKFILES if f in files]
    if found:
        return found[0], [f for f, _ in JS_LOCKFILES if f in files]
    try:
        with open(os.path.join(path, "package.json"), encoding="utf-8") as f:
            pm = json.load(f).get("packageManager")
        if isinstance(pm, str) and pm.split("@")[0] in RUN:
            return pm.split("@")[0], []
    except (OSError, ValueError, AttributeError):
        pass
    return "npm", []


def has_e2e_target(path):
    cands = [os.path.join(path, n) for n in ("nx.json", "package.json")]
    for dp, dns, fns in os.walk(path):
        dns[:] = [x for x in dns if x not in ("node_modules", ".git", "dist", "tmp") and not x.startswith(".")]
        if dp[len(path):].count(os.sep) > 4:
            dns[:] = []
        if "project.json" in fns:
            cands.append(os.path.join(dp, "project.json"))
    for p in cands:
        try:
            with open(p, encoding="utf-8") as f:
                if re.search(r'"[^"]*e2e[^"]*"\s*:\s*\{', f.read()):
                    return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def project(root, d):
    path = os.path.normpath(os.path.join(root, d))
    try:
        files = set(os.listdir(path))
    except OSError as e:
        skipped(d, "directory cannot be listed: %s" % e.__class__.__name__)
        return
    found = []

    def line(eco, manifest, extra, fallbacks=()):
        found.append(True)
        emit("PROJECT", d, eco, manifest, extra or "-")
        for kind, cmd in fallbacks:
            emit("FALLBACK", d, kind, cmd)

    for pat in DOC_PATTERNS:
        for f in sorted(files):
            if fnmatch.fnmatch(f.lower(), pat.lower()) and os.path.isfile(os.path.join(path, f)):
                emit("DOC", d, f)

    if "package.json" in files:
        mgr, locks = js_manager(path, files)
        fb = []
        if mgr == "npm":
            fb = [("install", "npm ci" if {"package-lock.json", "npm-shrinkwrap.json"} & files else "npm install")]
        elif mgr in ("yarn", "pnpm"):
            fb = [("install", "%s install" % mgr)]
        line(mgr, "package.json", ",".join(locks), fb)
        text = read(os.path.join(path, "package.json"))
        if text is not None and mgr in RUN | {"ambiguous": ""}:
            try:
                scripts = json.loads(text).get("scripts") or {}
                if isinstance(scripts, dict):
                    run = RUN.get(mgr, RUN["npm"])
                    for name, body in scripts.items():
                        emit("SCRIPT", d, run.format(name), body)
            except (ValueError, AttributeError):
                skipped(os.path.join(d, "package.json"), "is not valid JSON")
        if "nx.json" in files:
            fb = [(k, "npx nx run-many -t %s" % k) for k in ("lint", "test", "build")]
            if has_e2e_target(path):
                fb.append(("e2e", "npx nx run-many -t e2e"))
            line("nx", "nx.json", "", fb)
    if "pyproject.toml" in files:
        text = read(os.path.join(path, "pyproject.toml")) or ""
        uv = "uv.lock" in files or "[tool.uv" in text
        uvfb = [("install", "uv sync"), ("lint", "uv run ruff check"), ("test", "uv run pytest"), ("build", "uv build")]
        poetry = "poetry.lock" in files or "[tool.poetry" in text
        pdm = "pdm.lock" in files or "[tool.pdm" in text
        if uv:
            line("uv", "pyproject.toml", "uv.lock" if "uv.lock" in files else "", uvfb)
        if poetry:
            line("poetry", "pyproject.toml", "poetry.lock" if "poetry.lock" in files else "")
        if pdm:
            line("pdm", "pyproject.toml", "pdm.lock" if "pdm.lock" in files else "")
        if not (uv or poetry or pdm):
            line("python", "pyproject.toml", "")
    if "Pipfile" in files:
        line("pipenv", "Pipfile", "Pipfile.lock" if "Pipfile.lock" in files else "")
    reqs = sorted(f for f in files if fnmatch.fnmatch(f, "requirements*.txt"))
    if reqs:
        line("requirements", ",".join(reqs), "")
    if "Cargo.toml" in files:
        line("cargo", "Cargo.toml", "Cargo.lock" if "Cargo.lock" in files else "", [
            ("format", "cargo fmt --check"), ("lint", "cargo clippy -- -D warnings"),
            ("test", "cargo test"), ("build", "cargo build")])
    if "go.mod" in files:
        line("go", "go.mod", "go.sum" if "go.sum" in files else "", [
            ("lint", "go vet ./..."), ("test", "go test ./..."), ("build", "go build ./...")])
    gradle = [f for f in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts") if f in files]
    if gradle:
        wrapper = "gradlew" in files
        line("gradle", ",".join(gradle), "gradlew" if wrapper else "",
             [("build", "./gradlew build")] if wrapper else [])
    if "pom.xml" in files:
        wrapper = "mvnw" in files
        line("maven", "pom.xml", "mvnw" if wrapper else "")
    if "composer.json" in files:
        line("composer", "composer.json", "composer.lock" if "composer.lock" in files else "")
        text = read(os.path.join(path, "composer.json"))
        if text is not None:
            try:
                scripts = json.loads(text).get("scripts") or {}
                if isinstance(scripts, dict):
                    for name, body in scripts.items():
                        emit("SCRIPT", d, "composer run-script %s" % name,
                             body if isinstance(body, str) else json.dumps(body))
            except (ValueError, AttributeError):
                skipped(os.path.join(d, "composer.json"), "is not valid JSON")
    if not found:
        emit("PROJECT", d, "none")
    for mk in ("Makefile", "makefile", "GNUmakefile"):
        if mk in files:
            text = read(os.path.join(path, mk))
            seen = []
            for m in re.finditer(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:(?![=:])", text or "", re.M):
                if m.group(1) not in seen:
                    seen.append(m.group(1))
                    emit("MAKE", d, "make %s" % m.group(1))
            break


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        sys.stderr.write("usage: repo-commands.py <checkout root>\n")
        return 2
    root = argv[1]
    dirs = ["."]
    wfdir = os.path.join(root, ".github", "workflows")
    others = []
    try:
        names = sorted(f for f in os.listdir(wfdir) if f.endswith((".yml", ".yaml")))
    except OSError:
        names = []
    for name in names:
        rel = ".github/workflows/%s" % name
        text = read(os.path.join(wfdir, name))
        if text is None:
            continue
        callers = parse_callers(rel, text)
        if not callers:
            others.append(rel)
            continue
        for job, inputs in callers:
            c = Caller(rel, job, inputs, root)
            rd = c.get("root_dir", ".")
            d = safe_dir(root, rd) if rd is not None else "."  # unreadable root_dir: the checkout root
            if d is None:
                skipped("%s:%s" % (rel, job), "root_dir %s is outside the checkout" % rd)
                continue
            tool = c.get("tool", "none")
            emit("CALLER", rel, job, d, "unknown" if tool is None else (tool or "none"))
            ci_lines(c, d)
            if d not in dirs:
                dirs.append(d)
    for d in dirs:
        project(root, d)
    for rel in others:
        emit("WORKFLOW", rel)
    sys.stdout.write("\n".join(OUT) + ("\n" if OUT else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
