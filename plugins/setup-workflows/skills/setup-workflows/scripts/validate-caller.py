#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Check a caller against the live orchestrator: `with:` keys against its input list, `run <name>`
values against package.json, and `permissions:` against every scope the call tree requests.
usage: validate-caller.py [<repo-dir>] [<inputs-file>] [<permissions-file>]
       defaults: . , /tmp/valid_inputs.txt , /tmp/required_permissions.txt
Exits 1 on any INVALID key, BROKEN script reference or MISSING permission. actionlint catches none
of them: it does not look into a remote reusable workflow. A missing permission is the worst of the
three -- the run ends in startup_failure with no job and no log."""
import json, sys, pathlib
import yaml

repo = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
valid = set(pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/valid_inputs.txt").read_text().split())
perms_file = pathlib.Path(sys.argv[3] if len(sys.argv) > 3 else "/tmp/required_permissions.txt")
if not perms_file.exists():
    sys.exit(f"{perms_file} missing -- run scripts/fetch-permissions.py first")
required = dict(line.split() for line in perms_file.read_text().splitlines() if line.strip())
job = yaml.safe_load((repo / ".github/workflows/build.yml").read_text())["jobs"]["build_and_publish"]
w = job.get("with", {})
granted = job.get("permissions") or {}
pkg = repo / "package.json"
scripts = json.loads(pkg.read_text()).get("scripts", {}) if pkg.exists() else {}

bad = False
for k, v in w.items():
    ok = k in valid
    bad |= not ok
    print(("ok      " if ok else "INVALID "), k)
    if isinstance(v, str) and v.startswith("run "):
        n = v[4:].split()[0]
        ok = n in scripts
        bad |= not ok
        print(("  ok    " if ok else "  BROKEN"), f"{k}: {v} (script '{n}' {'exists' if ok else 'missing'})")

RANK = {"none": 0, "read": 1, "write": 2}
if not isinstance(granted, dict):
    granted = {}  # write-all / read-all are not used here; treat as unlisted so they get flagged
for scope, level in required.items():
    ok = RANK.get(granted.get(scope, "none"), 0) >= RANK[level]
    bad |= not ok
    print(("ok      " if ok else "MISSING "), f"permissions.{scope}: {level}")
sys.exit(1 if bad else 0)
