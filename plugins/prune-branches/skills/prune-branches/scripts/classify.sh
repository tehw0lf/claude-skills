#!/usr/bin/env bash
# Classify the local branches of one or more git repositories. Read-only: deletes nothing.
# usage: classify.sh                  the repository containing the working directory; only when the working
#                                     directory is not inside any repository (no .git in it or an ancestor),
#                                     every repo under $CODING_ROOT (default ~/Nextcloud/Coding)
#        classify.sh <repo>...        one repository per argument (never widened to all repositories)
#        classify.sh --all [<root>]   every repo under <root> (default $CODING_ROOT or ~/Nextcloud/Coding)
#
# The first output line names the scope: "# scope=current<TAB><toplevel>", "# scope=paths",
# "# scope=all<TAB><root><TAB>reason=explicit" or
# "# scope=all<TAB><root><TAB>reason=not-in-repository<TAB>cwd=<cwd>".
# A working directory inside a repository that git cannot read prints "SKIPPED: inside a repository git cannot
# read" and does not fall back to all repositories.
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
  if git notes show "refs/heads/$b" >/dev/null 2>&1; then echo "git note"; return; fi
}

classify_repo() { # <repo-path>
  local repo="$1" base b up track ahead verdict kept mb tmp tree
  [ -n "$repo" ] || { printf '# \tSKIPPED: empty path\n'; return; }
  cd "$repo" || { printf '# %s\tSKIPPED: cannot cd\n' "$repo"; return; }
  git rev-parse --git-dir >/dev/null 2>&1 || { printf '# %s\tSKIPPED: not a git repository\n' "$repo"; return; }
  git remote get-url origin >/dev/null 2>&1 || { printf '# %s\tSKIPPED: no origin remote\n' "$repo"; return; }
  git fetch --prune --quiet origin 2>/dev/null || { printf '# %s\tSKIPPED: fetch failed\n' "$repo"; return; }
  base=$(git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null) \
    || { printf '# %s\tSKIPPED: no origin/HEAD (try: git remote set-head origin -a)\n' "$repo"; return; }
  base="${base#refs/remotes/origin/}"
  tree=clean; [ -z "$(git status --porcelain --untracked-files=normal)" ] || tree=dirty
  printf '# %s\tbase=%s\ttree=%s\n' "$repo" "$base" "$tree"

  # lstrip=2 prints the plain branch name even when a tag has the same name (refname:short would print heads/<name>).
  # A bare name resolves to the tag first, so every revision built from $b is written as refs/heads/$b.
  while IFS='|' read -r b up track; do
    [ "$b" = "$base" ] && continue
    ahead=$(git rev-list --count "refs/remotes/origin/$base..refs/heads/$b" 2>/dev/null || echo "?")
    if [ -z "$up" ]; then
      verdict="INELIGIBLE (no upstream)"
    elif [[ "$track" != *gone* ]]; then
      continue   # still tracked: not a candidate
    elif kept=$(keep_rule "$b") && [ -n "$kept" ]; then
      verdict="KEPT ($kept)"
    elif [ "$ahead" = 0 ]; then
      verdict="SAFE (no own commits)"
    elif git diff --quiet "refs/remotes/origin/$base" "refs/heads/$b" 2>/dev/null; then
      verdict="SAFE (identical)"
    else
      mb=$(git merge-base "refs/remotes/origin/$base" "refs/heads/$b")
      tmp=$(git commit-tree "$(git rev-parse "refs/heads/$b^{tree}")" -p "$mb" -m probe)
      if git cherry "refs/remotes/origin/$base" "$tmp" | grep -q '^-'; then
        verdict="SAFE (squash-merged)"
      else
        verdict="NEEDS REVIEW"
      fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$repo" "$b" "$ahead" "$verdict" "$(git rev-parse --short "refs/heads/$b")"
  done < <(git for-each-ref --format='%(refname:lstrip=2)|%(upstream:short)|%(upstream:track)' refs/heads/)
}

scan_all() { # <root>
  # paths contain spaces: NUL-delimited, never a plain for-loop over find
  local gitdir
  while IFS= read -r -d '' gitdir; do
    (classify_repo "$(dirname "$gitdir")")
  done < <(find "$1" -maxdepth 3 -type d -name .git -print0)
}

in_repository() { # 0 when .git (directory or file) exists in $PWD or any ancestor
  local d="$PWD"
  while :; do
    [ -e "$d/.git" ] && return 0
    [ "$d" = / ] && return 1
    d=$(dirname "$d")
  done
}

root_default="${CODING_ROOT:-$HOME/Nextcloud/Coding}"

if [ $# -eq 0 ]; then
  if in_repository; then
    if top=$(git rev-parse --show-toplevel 2>/dev/null) && [ -n "$top" ]; then
      printf '# scope=current\t%s\n' "$top"
      (classify_repo "$top")
    else
      printf '# scope=current\t%s\n' "$PWD"
      printf '# %s\tSKIPPED: inside a repository git cannot read\n' "$PWD"
    fi
  else
    printf '# scope=all\t%s\treason=not-in-repository\tcwd=%s\n' "$root_default" "$PWD"
    scan_all "$root_default"
  fi
elif [ "$1" = --all ]; then
  root="${2:-$root_default}"
  printf '# scope=all\t%s\treason=explicit\n' "$root"
  scan_all "$root"
else
  printf '# scope=paths\n'
  for r in "$@"; do (classify_repo "$r"); done
fi
