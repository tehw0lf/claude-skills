#!/usr/bin/env bash
# Classify the local branches of one or more git repositories. Read-only: deletes nothing.
# usage: classify.sh <repo>...        one repository per argument
#        classify.sh --all [<root>]   every repo under <root> (default $CODING_ROOT or ~/Nextcloud/Coding)
#
# Fetches with --prune first; a failed fetch or missing origin/HEAD skips that repo (never classify stale refs).
# Output, tab-separated, one line per branch that is not plainly tracked:
#   repo  branch  ahead  verdict  sha
# verdicts: SAFE (no own commits) | SAFE (identical) | SAFE (squash-merged) | NEEDS REVIEW
#           KEPT (<rule>) | INELIGIBLE (no upstream)
# Per-repo status lines: "# repo  base=<b>  tree=clean|dirty" or "# repo  SKIPPED: <reason>".
set -uo pipefail

keep_global="${XDG_CONFIG_HOME:-$HOME/.config}/prune-branches-keep"

matches_keepfile() { # <file> <branch>
  [ -r "$1" ] || return 1
  local pat
  while IFS= read -r pat || [ -n "$pat" ]; do
    pat="${pat%%#*}"; pat="${pat#"${pat%%[![:space:]]*}"}"; pat="${pat%"${pat##*[![:space:]]}"}"
    [ -n "$pat" ] || continue
    # shellcheck disable=SC2053  # unquoted on purpose: glob match
    [[ "$2" == $pat ]] && return 0
  done < "$1"
  return 1
}

keep_rule() { # <branch> -> prints rule name if kept
  local b="$1" gitdir
  gitdir=$(git rev-parse --git-dir)
  if matches_keepfile "$keep_global" "$b"; then echo "global keep-file"; return; fi
  if matches_keepfile "$gitdir/prune-branches-keep" "$b"; then echo "repo keep-file"; return; fi
  case "$b" in *keep/*|*archive/*|*wip/*|*working-state*) echo "name pattern"; return;; esac
  if [ "$(git config --bool "branch.$b.pruneKeep" 2>/dev/null)" = true ]; then echo "pruneKeep config"; return; fi
  if git notes show "$b" >/dev/null 2>&1; then echo "git note"; return; fi
}

classify_repo() { # <repo-path>
  local repo="$1" base b up track ahead verdict kept mb tmp tree
  cd "$repo" || { printf '# %s\tSKIPPED: cannot cd\n' "$repo"; return; }
  git remote get-url origin >/dev/null 2>&1 || { printf '# %s\tSKIPPED: no origin remote\n' "$repo"; return; }
  git fetch --prune --quiet origin 2>/dev/null || { printf '# %s\tSKIPPED: fetch failed\n' "$repo"; return; }
  base=$(git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null) \
    || { printf '# %s\tSKIPPED: no origin/HEAD (try: git remote set-head origin -a)\n' "$repo"; return; }
  base="${base#refs/remotes/origin/}"
  tree=clean; [ -z "$(git status --porcelain)" ] || tree=dirty
  printf '# %s\tbase=%s\ttree=%s\n' "$repo" "$base" "$tree"

  while IFS='|' read -r b up track; do
    [ "$b" = "$base" ] && continue
    ahead=$(git rev-list --count "origin/$base..$b" 2>/dev/null || echo "?")
    if [ -z "$up" ]; then
      verdict="INELIGIBLE (no upstream)"
    elif [[ "$track" != *gone* ]]; then
      continue   # still tracked: not a candidate
    elif kept=$(keep_rule "$b") && [ -n "$kept" ]; then
      verdict="KEPT ($kept)"
    elif [ "$ahead" = 0 ]; then
      verdict="SAFE (no own commits)"
    elif git diff --quiet "origin/$base" "$b" 2>/dev/null; then
      verdict="SAFE (identical)"
    else
      mb=$(git merge-base "origin/$base" "$b")
      tmp=$(git commit-tree "$(git rev-parse "$b^{tree}")" -p "$mb" -m probe)
      if git cherry "origin/$base" "$tmp" | grep -q '^-'; then
        verdict="SAFE (squash-merged)"
      else
        verdict="NEEDS REVIEW"
      fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$repo" "$b" "$ahead" "$verdict" "$(git rev-parse --short "$b")"
  done < <(git for-each-ref --format='%(refname:short)|%(upstream:short)|%(upstream:track)' refs/heads/)
}

[ $# -ge 1 ] || { sed -n '2,4p' "$0" >&2; exit 2; }

if [ "$1" = --all ]; then
  root="${2:-${CODING_ROOT:-$HOME/Nextcloud/Coding}}"
  # paths contain spaces: NUL-delimited, never a plain for-loop over find
  while IFS= read -r -d '' gitdir; do
    (classify_repo "$(dirname "$gitdir")")
  done < <(find "$root" -maxdepth 3 -type d -name .git -print0)
else
  for r in "$@"; do (classify_repo "$r"); done
fi
