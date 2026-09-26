#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Collect every permission scope the orchestrator can request, from the live workflows.
usage: fetch-permissions.py [<out-file>]   default: /tmp/required_permissions.txt   (lines: <scope> <level>)

A caller must grant at least what any workflow reachable from build-test-publish.yml requests,
whether or not that job runs: GitHub checks the whole call tree before starting, and a scope
the caller does not grant ends the run in startup_failure with no job and no log. A fixed list
in SKILL.md went stale exactly that way (npm-audit-autofix added pull-requests: write).
Exits non-zero if a workflow cannot be fetched -- never fall back to a remembered list."""
import base64, json, subprocess, sys, pathlib
import yaml

REPO = "tehw0lf/workflows"
RANK = {"none": 0, "read": 1, "write": 2}


def fetch(path: str) -> dict:
    out = subprocess.run(["gh", "api", f"repos/{REPO}/contents/{path}"], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"cannot fetch {path}: {out.stderr.strip()} -- stop")
    return yaml.safe_load(base64.b64decode(json.loads(out.stdout)["content"]))


def scopes(block) -> dict:
    if isinstance(block, dict):
        return {k: v for k, v in block.items() if v in RANK}
    if block == "write-all":
        sys.exit("a workflow requests write-all; list the scopes by hand -- stop")
    return {}


required: dict[str, str] = {}
seen: set[str] = set()
todo = [".github/workflows/build-test-publish.yml"]
while todo:
    path = todo.pop()
    if path in seen:
        continue
    seen.add(path)
    wf = fetch(path)
    blocks = [wf.get("permissions")]
    for job in (wf.get("jobs") or {}).values():
        blocks.append(job.get("permissions"))
        uses = job.get("uses", "")
        if uses.startswith("./"):
            todo.append(uses[2:])
    for block in blocks:
        for scope, level in scopes(block).items():
            if RANK[level] > RANK.get(required.get(scope, "none"), 0):
                required[scope] = level

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/required_permissions.txt")
out.write_text("".join(f"{s} {l}\n" for s, l in sorted(required.items())))
print(out.read_text(), end="")
print(f"{len(required)} scopes from {len(seen)} workflows -> {out}", file=sys.stderr)
