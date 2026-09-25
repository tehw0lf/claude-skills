#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Check a caller's `with:` keys against the live input list and its `run <name>` values against package.json.
usage: validate-caller.py [<repo-dir>] [<inputs-file>]   defaults: . and /tmp/valid_inputs.txt
Exits 1 on any INVALID key or BROKEN script reference. actionlint cannot catch either: it does not
validate inputs against a remote reusable workflow."""
import json, sys, pathlib
import yaml

repo = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
valid = set(pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/valid_inputs.txt").read_text().split())
w = yaml.safe_load((repo / ".github/workflows/build.yml").read_text())["jobs"]["build_and_publish"].get("with", {})
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
sys.exit(1 if bad else 0)
