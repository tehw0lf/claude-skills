#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Check a caller against the live orchestrator: `with:` keys against its input list, `run <name>`
values (package.json scripts for npm/yarn, script paths for other tools, both under root_dir),
and `permissions:` against every scope the call tree requests.
usage: validate-caller.py [<repo-dir>] [<inputs-file>] [<permissions-file>]
       defaults: . , /tmp/valid_inputs.txt , /tmp/required_permissions.txt
Exits 1 on any INVALID key, BROKEN script reference or MISSING permission. actionlint catches none
of them: it does not look into a remote reusable workflow. A missing permission is the worst of the
three -- the run ends in startup_failure with no job and no log."""
import json, os, sys, pathlib
import yaml

repo = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
valid = set(pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/valid_inputs.txt").read_text().split())
perms_file = pathlib.Path(sys.argv[3] if len(sys.argv) > 3 else "/tmp/required_permissions.txt")
if not perms_file.exists():
    sys.exit(f"{perms_file} missing -- run scripts/fetch-permissions.py first")
required = dict(line.split() for line in perms_file.read_text().splitlines() if line.strip())
# Found by what it calls, not by name: callers written before this skill name the job `build`.
jobs = yaml.safe_load((repo / ".github/workflows/build.yml").read_text())["jobs"]
ORCHESTRATOR = "tehw0lf/workflows/.github/workflows/build-test-publish.yml@"  # any branch, tag or SHA
callers = [j for j in jobs.values() if str(j.get("uses", "")).startswith(ORCHESTRATOR)]
if len(callers) != 1:
    sys.exit(f"expected one job in build.yml calling build-test-publish.yml, found {len(callers)}")
job = callers[0]
w = job.get("with", {})
granted = job.get("permissions") or {}
# The orchestrator runs every command inside root_dir, so that is where package.json and any
# script path are looked up.
tool = w.get("tool", "none")
root = repo / w.get("root_dir", ".")
pkg = root / "package.json"
scripts = json.loads(pkg.read_text()).get("scripts", {}) if pkg.exists() else {}

bad = False
for k, v in w.items():
    ok = k in valid
    bad |= not ok
    print(("ok      " if ok else "INVALID "), k)
    if not (isinstance(v, str) and v.startswith("run ")):
        continue
    words = v[4:].split()
    if not words:
        bad = True
        print("  BROKEN", f"{k}: {v!r} (run without a command)")
        continue
    n = words[0]
    if tool in ("npm", "yarn"):
        # `npm run <name>` only runs a package.json script.
        ok = n in scripts
        bad |= not ok
        print(("  ok    " if ok else "  BROKEN"), f"{k}: {v} (script '{n}' {'exists' if ok else 'missing'})")
    elif "/" in n:
        # Any other tool's `run` executes a command, e.g. `uv run ./scripts/fetch.sh`. Only a
        # path can be checked here; a bare name comes from the tool's environment.
        f = root / n
        ok = f.is_file() and os.access(f, os.X_OK)
        bad |= not ok
        print(("  ok    " if ok else "  BROKEN"), f"{k}: {v} (file '{n}' {'executable' if ok else 'missing or not executable'})")
    else:
        print("  -     ", f"{k}: {v} (a {tool} command, not checked)")

RANK = {"none": 0, "read": 1, "write": 2}
if granted in ("read-all", "write-all"):
    granted = {scope: granted.removesuffix("-all") for scope in required}
elif not isinstance(granted, dict):
    granted = {}  # anything else grants nothing, so every required scope is flagged
for scope, level in required.items():
    ok = RANK.get(granted.get(scope, "none"), 0) >= RANK[level]
    bad |= not ok
    print(("ok      " if ok else "MISSING "), f"permissions.{scope}: {level}")
sys.exit(1 if bad else 0)
