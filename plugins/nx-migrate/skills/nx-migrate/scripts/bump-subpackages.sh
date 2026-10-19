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
# Bumped:  a package that is in HEAD with a non-empty string "version", and is changed.
# Skipped, each with a "# note:" line on stderr: a symlinked package directory; a directory name with a
#   tab or newline (it would break the output format); a package that is not in HEAD (new or renamed in
#   this run: no earlier version; a git-ignored package directory gets its own note); a package whose HEAD manifest has no "version" and whose directory
#   has changes (none is added); a directory with changes but no regular package.json (deleted, a
#   directory, a symlink); workspace globs other than exactly libs/* and apps/* (libs/**, libs/group/*,
#   packages/* ...; workspace entries with a leading ! are listed as exclusions that are not applied); neither libs/ nor apps/ existing.
# Skipped silently (the only silent skips, none of them hides a change): an unchanged package (with or
#   without "version"); an unchanged directory under libs/ or apps/ without a regular package.json (not
#   a package); a plain file or a dot-directory under libs/ or apps/ (the glob does not match them, as
#   for npm); a package directory that was deleted (it no longer exists, so it is not scanned).
# Already bumped by an earlier run (working-tree version differs from HEAD): not bumped again, but
#   reported again, and the lockfile is synced again, so a second run (e.g. after a failed
#   npm install) is safe. That is accepted only when the working-tree version ranks above HEAD's by
#   semantic-version precedence (build metadata ignored); a lower or equal version, or a version that
#   is not valid semver, is an error, and so is a version with a control character in any manifest.
# Internal pins: if a manifest of the workspace (the root and every workspace npm maps from the root
#   "workspaces": explicit entries, libs/**, other globs, ! exclusions) has a dependency spec in
#   dependencies, devDependencies, peerDependencies or optionalDependencies on a package that is going to
#   be bumped (or was bumped by an earlier run) that the HEAD version satisfies and the new version does
#   not ("1.0.0", "=1.0.0", ^0.0.1, <=0.0.1, "1.0.0" against 1.0.0+build ...), the script lists each one
#   and exits 1 before this run bumps anything. After the bump the dependent no longer resolves to the
#   workspace package: npm looks the spec up in the registry (a 404 if it was never published, otherwise
#   it installs the registry copy). The owner decides whether the spec becomes a range or the new version,
#   then the script is rerun. The decision is made by check-pins.js (next to this script) with npm's own
#   semver, npm-package-arg and @npmcli/map-workspaces, found from the npm on PATH; if they cannot be
#   loaded the script exits 1 before any bump. A spec that npm does not match by version (npm: aliases,
#   file:, git, tags) and * are not checked; a spec HEAD's version already misses gets a note. A package
#   outside the mapped workspaces, or without a "name", gets a note (its pins are not checked); so does a
#   manifest that cannot be read. The bump scan itself still covers exactly libs/*/ and apps/*/.
# Two passes: the first checks every package (manifests in the working tree and in HEAD, versions) and
#   writes nothing, so a failing check stops the script before the first bump. A committed version that
#   is not valid semver also stops here: this run cannot fix HEAD. The second pass bumps. If
#   `npm version` fails there (for another reason, e.g. an unwritable manifest), the message names the
#   packages bumped so far, says that the lockfile is not synced, and that a rerun after fixing the cause is safe.
# Formatting: `npm version` rewrites the whole package.json, not only "version". Observed with npm 12.2.0
#   on an indented manifest: arrays expanded to one element per line, \uXXXX escapes written as the
#   literal characters, a final newline added; a compact one-line manifest stayed compact. The diff of
#   a bumped manifest can therefore show more than the version line (the root bump in step 10 does the same).
# No package scripts run: `npm version` and the lockfile sync use --ignore-scripts, so a library's
#   preversion/version/postversion (or a root install script) cannot act during the migration.
# Directory names are matched literally (git --literal-pathspecs), never as glob patterns.
# Versions follow `npm version patch`: 1.2.3 -> 1.2.4, 2.0.0-rc.1 -> 2.0.0, 1.0.0+build -> 1.0.1 (as for the root bump).
#
# Output on stdout, tab-separated, one line per bumped package:  <dir>  <old version>  <new version>
# Preconditions (exit 1, each with its own message): git, jq, npm and node on PATH; HEAD resolvable; nx.json
#   present and not empty or whitespace-only; package.json one valid JSON object (its own "version" is not checked) whose "workspaces"
#   field, if present, is an array of strings or an object with a "packages" array of strings (null, a
#   string, a boolean or an object without "packages" is rejected; an absent field or [] is accepted on
#   purpose, because publishable libraries need not be npm workspaces and the scan uses libs/* and apps/*); libs and apps, where they exist, real
#   directories (not symlinks, not files). If neither libs/ nor apps/ exists there is nothing to scan (note, exit 0).
# Exit status: 0 = done (possibly nothing to bump); 1 = failure, with a message on stderr (git, jq, npm or
#   node missing or failing, HEAD unresolvable, no package.json, an unreadable or invalid manifest, an
#   empty or non-object manifest, a version removed in this run, empty or non-string "version" (a version must be a non-empty string) or one with a control character, an earlier bump that is not a semver increase, a dependency spec on a package that would be bumped that its new version does not satisfy, npm's modules for the pin check not loadable or the workspaces not mappable, npm not changing the version). Nothing is skipped silently on an error.
set -uo pipefail

die() { echo "bump-subpackages: $*" >&2; exit 1; }
note() { echo "# note: $*" >&2; }

for tool in git jq npm node; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool not found"
done
git rev-parse --verify -q HEAD >/dev/null || die "cannot resolve HEAD (not a git repository, or no commit yet)"
# Version of a manifest read from stdin: empty output = no version. Fails (message on stdout) on an
# empty or whitespace-only file, several documents, a non-object, or a version that is not a string.
manifest_version() {
  jq -s -r 'if length == 0 then error("is empty")
            elif length > 1 then error("holds more than one JSON document")
            elif (.[0] | type) != "object" then error("is not a JSON object")
            elif (.[0] | has("version")) and ((.[0].version | type) != "string" or .[0].version == "") then error("has a version that is not a non-empty string")
            elif (.[0] | has("version")) and (.[0].version | test("[\u0000-\u001f\u007f]")) then error("has a version with a control character")
            else (.[0].version // empty) end' 2>&1 | sed -e 's/^jq: error (at [^)]*): //' -e 's/^jq: //'
}

# Semantic-version precedence (semver.org 11; build metadata is ignored). `is_increase <old> <new>` exits
# 0 when <new> ranks above <old>; a leading "v" or "=" and surrounding whitespace are ignored, as `npm version` does, 1 when it ranks equal or lower, 2 when either is not a valid version.
is_increase() {
  jq -n -e --arg a "$1" --arg b "$2" '
    def key: sub("^[\\s=v]+"; "") | sub("\\s+$"; "")
      | capture("^(?<m>0|[1-9][0-9]*)\\.(?<n>0|[1-9][0-9]*)\\.(?<p>0|[1-9][0-9]*)(?:-(?<pre>(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\\+[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)?$")
      | select([.m, .n, .p | tonumber] | max <= 9007199254740991)
      | [(.m, .n, .p | tonumber),
         (if .pre == null then [1]
          else [0] + (.pre | split(".") | map(if test("^[0-9]+$") then [0, tonumber] else [1, .] end)) end)];
    (try ($a | key) catch null) as $x | (try ($b | key) catch null) as $y
    | if $x == null or $y == null then error("invalid") else $y > $x end' >/dev/null 2>&1
  case $? in 0) return 0;; 1) return 1;; *) return 2;; esac
}

# Preconditions. Each failure is exit 1 with its own message, before anything is bumped.
# (b) the workspace root: nx.json (step 1 of the skill requires it) next to package.json.
[ -e nx.json ] && [ ! -f nx.json ] && die "nx.json is not a file"
[ -f nx.json ] || die "no nx.json in $(pwd): run it in the root of the Nx workspace, not inside a package"
grep -q '[^[:space:]]' nx.json || die "nx.json is empty or holds only whitespace"
# (a) the root manifest: one valid JSON object (its own "version" is not read or checked: the script
#     never bumps the root), and a usable "workspaces" field when there is one.
[ -f package.json ] || die "no package.json in $(pwd): run it in the root of the Nx workspace"
msg=$(jq -s -r 'if length == 0 then error("is empty")
                elif length > 1 then error("holds more than one JSON document")
                elif (.[0] | type) != "object" then error("is not a JSON object")
                else "ok" end' package.json 2>&1 | sed -e 's/^jq: error (at [^)]*): //' -e 's/^jq: //') && [ "$msg" = ok ] \
  || die "package.json ${msg:-is invalid}"
ok=$(jq -r 'if has("workspaces") | not then true
            elif (.workspaces | type) == "array" then all(.workspaces[]; type == "string")
            elif (.workspaces | type) == "object" and (.workspaces.packages | type) == "array"
              then all(.workspaces.packages[]; type == "string")
            else false end' package.json) || die "cannot read the workspaces of package.json"
[ "$ok" = true ] || die "package.json: workspaces must be an array of strings or an object with a packages array of strings (null, a string, a boolean or an object without packages is not accepted)"
# (c) libs and apps, where they exist, are real directories (a symlinked parent would hide every package).
scanned=0
for top in libs apps; do
  if [ -L "$top" ]; then die "$top is a symlink: the scan needs a real directory"; fi
  if [ -e "$top" ] && [ ! -d "$top" ]; then die "$top exists but is not a directory"; fi
  [ -d "$top" ] && scanned=1
done
[ "$scanned" -eq 1 ] || note "neither libs/ nor apps/ exists in $(pwd): nothing to scan"

# Workspace globs the scan does not cover: everything except exactly libs/* and apps/*
# (a leading ./ and a trailing / are ignored). A leading ! is an exclusion, not a glob: it is listed
# apart, because the scan does not honour it.
extra=$(jq -r '(.workspaces // []) | (if type == "array" then . else .packages end)
               | map(sub("^\\./"; "") | sub("/+$"; ""))
               | map(select((startswith("!") or . == "libs/*" or . == "apps/*") | not)) | join(" ")' package.json) \
  || die "cannot read the workspaces of package.json"
[ -z "$extra" ] || note "workspace globs not scanned for packages to bump (only libs/* and apps/*; their pins are checked): $extra"
excluded=$(jq -r '(.workspaces // []) | (if type == "array" then . else .packages end)
                  | map(select(startswith("!"))) | join(" ")' package.json) \
  || die "cannot read the workspaces of package.json"
[ -z "$excluded" ] || note "workspace exclusions not applied to the scan (every package under libs/* and apps/* is scanned; the pin check applies them as npm does): $excluded"

# Pass 1 checks every package and writes nothing: a failing manifest check stops the script before the
# first bump. plan holds one record per package to report: "bump|already <TAB> dir <TAB> old <TAB> new".
plan=()
for dir in libs/*/ apps/*/; do
  dir=${dir%/}
  [ -d "$dir" ] || continue   # the unmatched glob itself: not a package directory (the only silent skip)
  pkg=$dir/package.json
  if [ -L "$dir" ]; then note "$dir is a symlink, skipped"; continue; fi
  case "$dir" in *$'\t'* | *$'\n'*) note "$dir contains a tab or newline, skipped"; continue;; esac
  tracked=$(git --literal-pathspecs diff --no-ext-diff --name-only HEAD -- "$dir") || die "git diff failed for $dir"
  untracked=$(git --literal-pathspecs ls-files --others --exclude-standard -- "$dir") || die "git ls-files failed for $dir"
  if [ -L "$pkg" ] || [ ! -f "$pkg" ]; then
    # No usable manifest: deleted, a directory, a dangling symlink, or never there.
    [ -z "$tracked$untracked" ] || note "$pkg is missing or not a regular file but $dir has changes, skipped"
    continue   # without changes this is simply not a package
  fi
  old=$(manifest_version < "$pkg") || die "$pkg ${old:-is invalid}"

  # The committed manifest. "HEAD:./path" is relative to the current directory, not to the git root.
  if ! git cat-file -e "HEAD:./$pkg" 2>/dev/null; then
    ignored=$(git --literal-pathspecs ls-files --others --ignored --exclude-standard -- "$pkg") || die "git ls-files failed for $pkg"
    if [ -n "$ignored" ]; then note "$pkg is git-ignored, skipped"
    else note "$pkg is not in HEAD (new or renamed in this run), skipped"; fi
    continue
  fi
  head_json=$(git show "HEAD:./$pkg") || die "cannot read HEAD:$pkg"
  head_ver=$(printf '%s' "$head_json" | manifest_version) || die "the committed $pkg ${head_ver:-is invalid}"

  if [ -z "$head_ver" ]; then   # unversioned in HEAD: nothing to bump, and none is added
    [ -z "$tracked$untracked" ] || note "$pkg has no version in HEAD and $dir has changes, skipped (none is added or bumped)"
    continue
  fi
  [ -n "$old" ] || die "$pkg lost its version in this run (HEAD has $head_ver)"

  if [ "$old" != "$head_ver" ]; then   # bumped by an earlier run: only an increase is reported as a bump
    is_increase "$head_ver" "$old"; rc=$?
    [ "$rc" -ne 2 ] || die "$pkg: the version $old or the one in HEAD ($head_ver) is not a valid semantic version, so the change is not checked as a bump"
    [ "$rc" -eq 0 ] || die "$pkg: the version $old is not higher than the one in HEAD ($head_ver): a bump only raises the version, fix it by hand"
    plan+=("already"$'\t'"$dir"$'\t'"$head_ver"$'\t'"$old")
    continue
  fi

  [ -n "$tracked$untracked" ] || continue
  is_increase "$old" "$old"; [ $? -ne 2 ] || die "$pkg: the committed version $old is not a valid semantic version, so npm version cannot bump it; this run bumped nothing. Report it: a version in HEAD cannot be fixed by this run (nothing is committed before step 11), so the owner has to correct it first"
  plan+=("bump"$'\t'"$dir"$'\t'"$old"$'\t')
done

# A dependency spec that the HEAD version of a package satisfies and its new version does not leaves the
# dependent without the workspace package once the package has moved (an exact pin, ^0.0.1, <=0.0.1,
# 1.0.0 against 1.0.0+build ...). check-pins.js decides that by npm's own rules (its bundled semver and
# @npmcli/map-workspaces), in the root manifest and every workspace npm maps. Look before the first bump.
if [ "${#plan[@]}" -gt 0 ]; then
  records=$(for rec in "${plan[@]}"; do printf '%s\n' "$rec"; done \
    | jq -R -s -c 'split("\n") | map(select(. != "") | split("\t")
                     | {kind: .[0], dir: .[1], head: .[2], work: .[3]})') \
    || die "cannot build the package records for the pin check"
  pinned=$(printf '%s' "$records" | node "$(dirname "${BASH_SOURCE[0]}")/check-pins.js" "$(command -v npm)") \
    || die "the pin check failed (see above); this run bumped nothing"
  if [ -n "$pinned" ]; then
    printf '%s\n' "$pinned" >&2
    die "a manifest has a dependency spec that the bumped package no longer satisfies: afterwards it no longer resolves to the workspace package (npm looks the version up in the registry); this run bumped nothing. Report it: the owner decides whether the spec becomes a range or the new version, then rerun"
  fi
fi

# Pass 2 bumps. A failure here leaves the packages bumped so far in the working tree and the lockfile
# unsynced: the message says so, and a rerun picks up where this one stopped.
bumped=0
done_dirs=""
fail_bump() {
  local m="$1"
  [ -z "$done_dirs" ] || m="$m; already bumped: ${done_dirs# }"
  die "$m; the lockfile is not synced; rerunning the script after fixing the cause is safe (a bumped package is recognised and not bumped again)"
}
for rec in ${plan[@]+"${plan[@]}"}; do
  IFS=$'\t' read -r kind dir old new <<<"$rec"
  if [ "$kind" = bump ]; then
    pkg=$dir/package.json
    npm version patch --no-git-tag-version --ignore-scripts --prefix "$dir" >/dev/null || fail_bump "npm version failed for $dir"
    new=$(jq -r '.version // empty' "$pkg") || fail_bump "cannot read $pkg after the bump"
    { [ -n "$new" ] && [ "$new" != "$old" ]; } || fail_bump "npm did not change the version of $dir"
  fi
  printf '%s\t%s\t%s\n' "$dir" "$old" "$new"
  done_dirs="$done_dirs $dir"
  bumped=$((bumped + 1))
done

# Sync the lockfile once, after all bumps.
if [ "$bumped" -gt 0 ]; then
  npm install --ignore-scripts >&2 || die "npm install failed (rerun the script after fixing it: it resyncs)"
fi
