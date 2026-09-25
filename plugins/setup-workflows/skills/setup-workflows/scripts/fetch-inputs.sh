#!/usr/bin/env bash
# Fetch the live workflow_call input list of tehw0lf/workflows' orchestrator.
# usage: fetch-inputs.sh [<out-file>]   (default /tmp/valid_inputs.txt)
# Exits non-zero if the orchestrator cannot be fetched — never fall back to a remembered list.
set -euo pipefail
out="${1:-/tmp/valid_inputs.txt}"
src=/tmp/btp.yml
path=.github/workflows/build-test-publish.yml

gh api "repos/tehw0lf/workflows/contents/$path" --jq '.content' 2>/dev/null | base64 -d > "$src" 2>/dev/null \
  || curl -fsSL "https://raw.githubusercontent.com/tehw0lf/workflows/main/$path" -o "$src" \
  || { echo "cannot fetch $path via gh or curl — stop" >&2; exit 1; }

# The class must include digits: [a-z_]+ silently drops `e2e`.
sed -n '/^  workflow_call:/,/^jobs:/p' "$src" | grep -E '^      [a-z0-9_]+:' | sed 's/[ :]//g' > "$out"
[ -s "$out" ] || { echo "no inputs parsed from $src — stop" >&2; exit 1; }
cat "$out"
echo "$(wc -l < "$out" | tr -d ' ') inputs -> $out" >&2
