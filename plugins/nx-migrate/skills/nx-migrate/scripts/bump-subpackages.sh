#!/usr/bin/env bash
# Patch-bump every libs/*/ and apps/*/ package that has a "version" and anything changed in its directory.
# usage: bump-subpackages.sh          run in the workspace root, before the migration commit (step 10)
#
# "Changed" = tracked diff against HEAD or an untracked, non-ignored file; step 1 required a clean tree
# and nothing is committed before step 11, so this is exactly what the run changed.
# A package without "version" is left alone (none is added). Runs one `npm install` after all bumps.
# Output, tab-separated, one line per bumped package:  <dir>  <old version>  <new version>
# Exits non-zero when HEAD cannot be resolved, the root package.json is missing, or a command fails.
set -uo pipefail

git rev-parse --verify -q HEAD >/dev/null || { echo "bump-subpackages: cannot resolve HEAD" >&2; exit 1; }
[ -f package.json ] || { echo "bump-subpackages: no package.json in $(pwd)" >&2; exit 1; }

bumped=0
for pkg in libs/*/package.json apps/*/package.json; do
  [ -f "$pkg" ] || continue
  dir=$(dirname "$pkg")
  old=$(jq -r '.version // empty' "$pkg") || { echo "bump-subpackages: cannot read $pkg" >&2; exit 1; }
  [ -n "$old" ] || continue
  tracked=$(git diff --name-only HEAD -- "$dir") || { echo "bump-subpackages: git diff failed for $dir" >&2; exit 1; }
  untracked=$(git ls-files --others --exclude-standard -- "$dir") || { echo "bump-subpackages: git ls-files failed for $dir" >&2; exit 1; }
  [ -n "$tracked$untracked" ] || continue
  npm version patch --no-git-tag-version --prefix "$dir" >/dev/null || { echo "bump-subpackages: npm version failed for $dir" >&2; exit 1; }
  printf '%s\t%s\t%s\n' "$dir" "$old" "$(jq -r .version "$pkg")"
  bumped=$((bumped + 1))
done

# Sync the lockfile once, after all bumps.
[ "$bumped" -eq 0 ] || npm install >&2 || { echo "bump-subpackages: npm install failed" >&2; exit 1; }
