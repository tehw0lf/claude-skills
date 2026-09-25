#!/usr/bin/env python3
"""Report which script inputs package.json can back. usage: check-scripts.py [<repo-dir>]"""
import json, sys, pathlib

s = json.loads((pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".") / "package.json").read_text()).get("scripts", {})
for name in ("install", "format", "lint", "test", "e2e", "build"):
    print(f"  {name}: {'present' if name in s else 'ABSENT — do not pass run ' + name}")
