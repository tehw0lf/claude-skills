#!/usr/bin/env bash
# List the package directories of an Nx workspace: every project root of the Nx project graph, other
# than the workspace root ".", whose directory holds a regular package.json.
# usage: project-packages.sh [--all]
#        run in the Nx workspace root (nx.json there; the root may be a subdirectory of the git root)
#
# Source: `npx nx show projects --json` (the names), then `npx nx show project <name> --json` for the
# "root" of each. Nx computes the project graph for that, so the dependencies must be installed and the
# project configuration must be valid.
# --all: list every project root other than ".", whether or not its directory holds a package.json
#   (bump-subpackages.sh needs them to tell which project a changed file belongs to).
# Output on stdout: one root per line, relative to the workspace root, sorted (byte order) and unique.
# Not listed, silently: the workspace root "."; a root without a regular package.json (a project that is
#   not a package; a package.json that is a symlink or a directory counts as none).
# Skipped with a "# note:" line on stderr: a root with a tab or newline in its name (it would break the
#   line format of this output and of bump-subpackages.sh).
# Exit status: 0 = done (no output when no root qualifies); 1 = failure, with a message on stderr and
#   nothing on stdout: npx or jq missing, no nx.json, a failing Nx command or output that is not the
#   expected JSON (nothing is listed from a partial graph), a project name starting with "-", an empty
#   root, an absolute root or one with a ".." segment (the scripts never act outside the workspace).
set -uo pipefail

die() { echo "project-packages: $*" >&2; exit 1; }
note() { echo "# note: $*" >&2; }

all=0
case $# in
  0) ;;
  1) [ "$1" = --all ] || die "usage: project-packages.sh [--all]"
     all=1 ;;
  *) die "usage: project-packages.sh [--all]" ;;
esac
for tool in npx jq; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool not found"
done
[ -f nx.json ] || die "no nx.json in $(pwd): run it in the root of the Nx workspace, not inside a package"

list=$(npx nx show projects --json) || die "npx nx show projects --json failed (Nx cannot compute the project graph)"
printf '%s' "$list" | jq -e 'type == "array" and all(.[]; type == "string")' >/dev/null 2>&1 \
  || die "npx nx show projects --json did not print an array of strings"
# The names, NUL-separated so that no name can split into two.
names=()
while IFS= read -r -d '' name; do
  names+=("$name")
done < <(printf '%s' "$list" | jq -j '.[] + "\u0000"')

roots=()
for name in ${names[@]+"${names[@]}"}; do
  case "$name" in -*|"") die "project name '$name' is empty or starts with '-': not passed to nx";; esac
  json=$(npx nx show project "$name" --json) || die "npx nx show project $name --json failed"
  root=$(printf '%s' "$json" | jq -r 'if type == "object" and (.root | type) == "string" then .root else error("no root") end') \
    || die "npx nx show project $name --json did not print an object with a string root"
  root=${root#./}
  while [ "${root%/}" != "$root" ] && [ "$root" != "/" ]; do root=${root%/}; done
  [ -n "$root" ] || die "project $name has an empty root"
  [ "$root" != . ] || continue
  case "$root" in /*) die "project $name has the absolute root $root";; esac
  case "/$root/" in */../*|*/./*) die "project $name has the root $root, which leaves or repeats the workspace path";; esac
  case "$root" in *$'\t'* | *$'\n'*) note "the root of project $name contains a tab or newline, skipped"; continue;; esac
  if [ "$all" -eq 1 ] || { [ -f "$root/package.json" ] && [ ! -L "$root/package.json" ]; }; then
    roots+=("$root")
  fi
done

[ "${#roots[@]}" -eq 0 ] || printf '%s\n' "${roots[@]}" | LC_ALL=C sort -u
