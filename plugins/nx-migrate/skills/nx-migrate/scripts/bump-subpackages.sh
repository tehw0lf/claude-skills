#!/usr/bin/env bash
# Patch-bump every libs/*/ and apps/*/ package that has a "version" and anything changed in its directory.
# usage: bump-subpackages.sh   run in the Nx workspace root (nx.json and package.json there; the root may
#                              be a subdirectory of the git root), before the migration commit (step 10)
#
# Base: HEAD. Step 1 required a clean tree and nothing is committed before step 11, so what differs
# from HEAD is exactly what the run changed.
# "Changed" = a tracked difference from HEAD (staged or not) or an untracked, non-ignored file in the
# package directory. Untracked build or test output that is not git-ignored therefore counts as a
# change: ignore such output in .gitignore, or the package gets a (harmless) bump.
#
# Bumped:  a package that is in HEAD with a string "version", and is changed.
# Skipped, each with a "# note" line on stderr (never silent): a symlinked package directory, a package
#   that is not in HEAD (new or renamed in this run: it has no earlier version), a package whose
#   HEAD manifest has no "version" (none is added), and workspace globs other than exactly libs/* and apps/* (libs/**, libs/group/*, packages/* ...), and a
#   directory name containing a tab or newline (it would break the output format).
# Skipped without a note: a package without "version" in both HEAD and the working tree.
# Already bumped by an earlier run (working-tree version differs from HEAD): not bumped again, but
#   reported again, and the lockfile is synced again, so a second run (e.g. after a failed
#   npm install) is safe.
# No package scripts run: `npm version` and the lockfile sync use --ignore-scripts, so a library's
#   preversion/version/postversion (or a root install script) cannot act during the migration.
# Directory names are matched literally (git --literal-pathspecs), never as glob patterns.
# Versions follow `npm version patch`: 1.2.3 -> 1.2.4, 2.0.0-rc.1 -> 2.0.0 (as for the root bump).
#
# Output on stdout, tab-separated, one line per bumped package:  <dir>  <old version>  <new version>
# Preconditions (exit 1, each with its own message): git, jq and npm on PATH; HEAD resolvable; nx.json
#   present and non-empty; package.json one valid JSON object with a usable "workspaces" field (array of
#   strings, or an object with a "packages" array of strings); libs and apps, where they exist, real
#   directories (not symlinks, not files). If neither libs/ nor apps/ exists there is nothing to scan (note, exit 0).
# Exit status: 0 = done (possibly nothing to bump); 1 = failure, with a message on stderr (git, jq or
#   npm missing or failing, HEAD unresolvable, no package.json, an unreadable or invalid manifest, an
#   empty or non-object manifest, non-string "version", npm not changing the version). Nothing is skipped silently on an error.
set -uo pipefail

die() { echo "bump-subpackages: $*" >&2; exit 1; }
note() { echo "# note: $*" >&2; }

for tool in git jq npm; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool not found"
done
git rev-parse --verify -q HEAD >/dev/null || die "cannot resolve HEAD (not a git repository, or no commit yet)"
# Version of a manifest read from stdin: empty output = no version. Fails (message on stdout) on an
# empty or whitespace-only file, several documents, a non-object, or a version that is not a string.
manifest_version() {
  jq -s -r 'if length == 0 then error("is empty")
            elif length > 1 then error("holds more than one JSON document")
            elif (.[0] | type) != "object" then error("is not a JSON object")
            elif (.[0] | has("version")) and (.[0].version | type) != "string" then error("has a version that is not a string")
            else (.[0].version // empty) end' 2>&1 | sed 's/^jq: error (at [^)]*): //'
}

# Preconditions. Each failure is exit 1 with its own message, before anything is bumped.
# (b) the workspace root: nx.json (step 1 of the skill requires it) next to package.json.
[ -e nx.json ] && [ ! -f nx.json ] && die "nx.json is not a file"
[ -f nx.json ] || die "no nx.json in $(pwd): run it in the root of the Nx workspace, not inside a package"
[ -s nx.json ] || die "nx.json is empty"
# (a) the root manifest: one valid JSON object, and a usable "workspaces" field.
[ -f package.json ] || die "no package.json in $(pwd): run it in the root of the Nx workspace"
msg=$(manifest_version < package.json) || die "package.json $msg"
ok=$(jq -r 'if .workspaces == null then true
            elif (.workspaces | type) == "array" then all(.workspaces[]; type == "string")
            elif (.workspaces | type) == "object" and (.workspaces.packages | type) == "array"
              then all(.workspaces.packages[]; type == "string")
            else false end' package.json) || die "cannot read the workspaces of package.json"
[ "$ok" = true ] || die "package.json: workspaces must be an array of strings or an object with a packages array of strings"
# (c) libs and apps, where they exist, are real directories (a symlinked parent would hide every package).
scanned=0
for top in libs apps; do
  if [ -L "$top" ]; then die "$top is a symlink: the scan needs a real directory"; fi
  if [ -e "$top" ] && [ ! -d "$top" ]; then die "$top exists but is not a directory"; fi
  [ -d "$top" ] && scanned=1
done
[ "$scanned" -eq 1 ] || note "neither libs/ nor apps/ exists in $(pwd): nothing to scan"

# Workspace globs the scan does not cover: everything except exactly libs/* and apps/*
# (a leading ./ and a trailing / are ignored).
extra=$(jq -r '(.workspaces // []) | (if type == "array" then . else .packages end)
               | map(sub("^\\./"; "") | sub("/+$"; ""))
               | map(select((. == "libs/*" or . == "apps/*") | not)) | join(" ")' package.json) \
  || die "cannot read the workspaces of package.json"
[ -z "$extra" ] || note "workspace globs not scanned (only libs/* and apps/*): $extra"

bumped=0
for pkg in libs/*/package.json apps/*/package.json; do
  [ -f "$pkg" ] || continue   # the unmatched glob itself, or a directory without a manifest
  dir=$(dirname "$pkg")
  if [ -L "$dir" ] || [ -L "$pkg" ]; then note "$dir is a symlink, skipped"; continue; fi

  case "$dir" in *$'\t'* | *$'\n'*) note "$dir contains a tab or newline, skipped"; continue;; esac
  old=$(manifest_version < "$pkg") || die "$pkg ${old:-is invalid}"

  # The committed manifest. "HEAD:./path" is relative to the current directory, not to the git root.
  if ! git cat-file -e "HEAD:./$pkg" 2>/dev/null; then
    note "$pkg is not in HEAD (new or renamed in this run), skipped"
    continue
  fi
  head_json=$(git show "HEAD:./$pkg") || die "cannot read HEAD:$pkg"
  head_ver=$(printf '%s' "$head_json" | manifest_version) || die "the committed $pkg ${head_ver:-is invalid}"

  if [ -z "$head_ver" ]; then
    [ -z "$old" ] || note "$pkg has no version in HEAD, skipped (none is added or bumped)"
    continue
  fi
  [ -n "$old" ] || die "$pkg lost its version in this run (HEAD has $head_ver)"

  if [ "$old" != "$head_ver" ]; then   # bumped by an earlier run
    printf '%s\t%s\t%s\n' "$dir" "$head_ver" "$old"
    bumped=$((bumped + 1))
    continue
  fi

  tracked=$(git --literal-pathspecs diff --no-ext-diff --name-only HEAD -- "$dir") || die "git diff failed for $dir"
  untracked=$(git --literal-pathspecs ls-files --others --exclude-standard -- "$dir") || die "git ls-files failed for $dir"
  [ -n "$tracked$untracked" ] || continue

  npm version patch --no-git-tag-version --ignore-scripts --prefix "$dir" >/dev/null || die "npm version failed for $dir"
  new=$(jq -r '.version // empty' "$pkg") || die "cannot read $pkg after the bump"
  { [ -n "$new" ] && [ "$new" != "$old" ]; } || die "npm did not change the version of $dir"
  printf '%s\t%s\t%s\n' "$dir" "$old" "$new"
  bumped=$((bumped + 1))
done

# Sync the lockfile once, after all bumps.
if [ "$bumped" -gt 0 ]; then
  npm install --ignore-scripts >&2 || die "npm install failed (rerun the script after fixing it: it resyncs)"
fi
