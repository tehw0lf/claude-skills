#!/bin/sh
# Runs the Git sequences of the stop and resume clean-ups of work-issue and nx-migrate in throwaway
# repositories and prints one tab-separated line per check: <scenario> <check> <value>.
# Usage: sh scenarios.sh <empty work directory>
# Needs only sh, git, sed, grep, tr, ls, cat, printf, mkdir; everything is written under the work directory.
# The expected values live in tests/test_git_cleanup.py.

for t in git sed grep tr ls cat mkdir; do
    command -v "$t" >/dev/null 2>&1 || { echo "missing tool: $t" >&2; exit 3; }
done
[ -n "$1" ] && [ -d "$1" ] && [ -z "$(ls -A "$1" 2>/dev/null)" ] || { echo "usage: sh scenarios.sh <empty work directory>" >&2; exit 2; }
w=$(cd "$1" && pwd) || exit 2

export LC_ALL=C LANGUAGE=
# Isolate Git from the machine's configuration (hooks, aliases, init.defaultBranch, identity).
mkdir "$w/home" || exit 2
export HOME="$w/home" XDG_CONFIG_HOME="$w/home/.config" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

out() { printf '%s\t%s\t%s\n' "$1" "$2" "$3"; }
# rc <scenario> <check> <command...>: the exit code; rcz: 0 or "nonzero"
rc() { _s=$1; _c=$2; shift 2; "$@" >/dev/null 2>&1; out "$_s" "$_c" "$?"; }
rcz() { _s=$1; _c=$2; shift 2; if "$@" >/dev/null 2>&1; then out "$_s" "$_c" 0; else out "$_s" "$_c" nonzero; fi; }
exists() { if [ -e "$3" ]; then out "$1" "$2" yes; else out "$1" "$2" no; fi; }
hasref() { if git rev-parse --verify -q "$3" >/dev/null 2>&1; then out "$1" "$2" yes; else out "$1" "$2" no; fi; }
state() { out "$1" "$2" "$(git status --porcelain --untracked-files=all --ignored 2>/dev/null | tr '\n' '|')"; }
branch() { out "$1" "$2" "$(git symbolic-ref --short -q HEAD || echo detached)"; }

# mk <name>: a bare origin and a clone with one commit on main, pushed; the working directory is the clone.
mk() {
    s=$1; d=$w/$1
    mkdir "$d" && git init -q --bare "$d/origin.git" && git --git-dir="$d/origin.git" symbolic-ref HEAD refs/heads/main || exit 2
    git init -q "$d/repo" && cd "$d/repo" && git symbolic-ref HEAD refs/heads/main || exit 2
    git remote add origin "$d/origin.git" && echo base > f.txt && git add f.txt && git commit -q -m base || exit 2
    git push -q origin main && git fetch -q origin || exit 2
}
# other: clones the origin into $d/other and changes into it; the caller commits and pushes from there
other() {
    git clone -q "$d/origin.git" "$d/other" && cd "$d/other" || exit 2
}

dirty_tracked() {
    mk dirty-tracked
    rc $s switch-run git switch --no-overwrite-ignore -c run refs/remotes/origin/main
    echo changed > f.txt
    rc $s reset git reset --hard
    rc $s switch-back git switch main
    out $s content "$(cat f.txt)"
    branch $s branch
    state $s status
}

untracked() {
    mk untracked
    echo ignored.txt > .gitignore && git add .gitignore && git commit -q -m ign && git push -q origin main && git fetch -q origin || exit 2
    echo secret > ignored.txt
    rc $s switch-run git switch --no-overwrite-ignore -c run refs/remotes/origin/main
    echo new > new.txt; echo mod >> f.txt
    git diff --binary HEAD > "$d/abandoned.diff" 2>/dev/null; out $s diff-saved $?
    rc $s reset git reset --hard
    rc $s switch-back git switch main
    rc $s add-n git add -N .
    git diff --binary HEAD >> "$d/abandoned.diff" 2>/dev/null
    out $s diff-has-tracked-edit "$(grep -c '^+++ b/f.txt' "$d/abandoned.diff")"
    out $s diff-has-new-file "$(grep -c '^+++ b/new.txt' "$d/abandoned.diff")"
    out $s diff-has-ignored-file "$(grep -c 'ignored.txt' "$d/abandoned.diff")"
    git reset --hard >/dev/null 2>&1 && git clean -fd >/dev/null 2>&1; out $s reset-clean $?
    exists $s new-file new.txt
    out $s ignored-content "$(cat ignored.txt)"
    out $s tracked-content "$(cat f.txt)"
    rc $s branch-delete git branch -D run
    hasref $s run-branch refs/heads/run
    state $s status
}

ignored_target_tracks() {
    mk ignored-target-tracks
    echo cfg.txt > .gitignore && git add .gitignore && git commit -q -m ign && git push -q origin main && git fetch -q origin || exit 2
    other
    echo remote > cfg.txt && git add -f cfg.txt && git commit -q -m cfg && git push -q origin HEAD:main && git push -q origin HEAD:refs/heads/feat || exit 2
    cd "$d/repo" && git fetch -q origin && echo local > cfg.txt || exit 2
    before=$(git rev-parse HEAD)
    rcz $s switch-refused git switch --no-overwrite-ignore -c run refs/remotes/origin/main
    hasref $s switch-run-branch refs/heads/run
    out $s switch-content "$(cat cfg.txt)"
    rcz $s merge-refused git merge --ff-only --no-overwrite-ignore refs/remotes/origin/main
    if [ "$(git rev-parse HEAD)" = "$before" ]; then out $s merge-head unmoved; else out $s merge-head moved; fi
    out $s merge-content "$(cat cfg.txt)"
    rcz $s resume-switch-refused git switch --no-overwrite-ignore --track refs/remotes/origin/feat
    hasref $s resume-branch refs/heads/feat
    out $s resume-content "$(cat cfg.txt)"
    branch $s branch
    rc $s control-switch git switch -c run2 refs/remotes/origin/main
    out $s control-content "$(cat cfg.txt)"
}

ignore_files() {
    mk ignore-files
    mkdir venv dx sx 's*' 'd*' && echo '*' > sx/.gitignore && echo '*' > venv/.gitignore && echo x > venv/lib.txt && echo '*' > dx/.gitignore || exit 2
    git ls-files --others --ignored --exclude-standard -- ':(glob)**/.gitignore' > "$d/before.txt" 2>/dev/null
    rc $s switch-run git switch --no-overwrite-ignore -c run refs/remotes/origin/main
    mkdir si sub && printf '*\n!keep.txt\n' > si/.gitignore && echo k > si/keep.txt && echo tmp > sub/.gitignore && echo tmp > 'd*/.gitignore' && printf '*\n!keep.txt\n' > 's*/.gitignore' && echo k > 's*/keep.txt' || exit 2
    git diff --binary HEAD > "$d/abandoned.diff" 2>/dev/null
    rc $s reset git reset --hard
    rc $s switch-back git switch main
    out $s created "$(git ls-files --others --exclude-standard -- ':(glob)**/.gitignore' | tr '\n' '|')"
    git ls-files --others --ignored --exclude-standard -- ':(glob)**/.gitignore' > "$d/after.txt" 2>/dev/null
    new=
    while IFS= read -r p; do
        found=0
        while IFS= read -r q; do [ "$p" = "$q" ] && found=1; done < "$d/before.txt"
        [ $found = 0 ] && new="$new$p|"
    done < "$d/after.txt"
    out $s self-ignoring-new "$new"
    rc $s check-ignore-dir git check-ignore --no-index -q -- si
    rc $s mark git add -N -- ':(literal)sub/.gitignore' ':(literal)d*/.gitignore'
    git diff --binary HEAD >> "$d/abandoned.diff" 2>/dev/null
    rc $s reset-1 git reset --hard
    rc $s mark-self-ignoring git add -N -f -- ':(literal)si/.gitignore' ':(literal)s*/.gitignore'
    git diff --binary HEAD >> "$d/abandoned.diff" 2>/dev/null
    rc $s reset-2 git reset --hard
    out $s diff-has-sub "$(grep -c '^+++ b/sub/.gitignore' "$d/abandoned.diff")"
    out $s diff-has-star "$(grep -c '^+++ b/d\*/.gitignore' "$d/abandoned.diff")"
    out $s diff-has-self-ignoring "$(grep -c '^+++ b/si/.gitignore' "$d/abandoned.diff")"
    out $s diff-has-preexisting "$(grep -c -e 'venv/' -e 'dx/' "$d/abandoned.diff")"
    exists $s removed-sub sub/.gitignore
    exists $s removed-star 'd*/.gitignore'
    exists $s removed-self-ignoring si/.gitignore
    exists $s kept-venv venv/.gitignore
    exists $s kept-dx dx/.gitignore
    exists $s removed-star-self-ignoring 's*/.gitignore'
    exists $s kept-sx sx/.gitignore
    rc $s add-n-all git add -N .
    git diff --binary HEAD >> "$d/abandoned.diff" 2>/dev/null
    git reset --hard >/dev/null 2>&1 && git clean -fd >/dev/null 2>&1; out $s reset-clean $?
    exists $s removed-keep si/keep.txt
    exists $s kept-venv-lib venv/lib.txt
    rc $s branch-delete git branch -D run
    state $s status
}

ignore_files_nodiff() {
    mk ignore-files-nodiff
    mkdir dx 'd*' && echo '*' > dx/.gitignore || exit 2
    rc $s switch-run git switch --no-overwrite-ignore -c run refs/remotes/origin/main
    echo tmp > 'd*/.gitignore'
    rc $s reset git reset --hard
    rc $s switch-back git switch main
    out $s created "$(git ls-files --others --exclude-standard -- ':(glob)**/.gitignore' | tr '\n' '|')"
    rc $s clean git clean -f -- ':(literal)d*/.gitignore'
    exists $s removed-star 'd*/.gitignore'
    exists $s kept-dx dx/.gitignore
    state $s status
}

bump_patch() {
    mk bump-patch
    i=1; : > s.txt; while [ $i -le 11 ]; do echo "line$i" >> s.txt; i=$((i + 1)); done; echo version=1 >> s.txt
    echo a1 > a.txt; printf 'name\nversion=1\n' > v.txt
    git add a.txt s.txt v.txt && git commit -q -m files && git push -q origin main && git fetch -q origin || exit 2
    rc $s switch-run git switch --no-overwrite-ignore -c run refs/remotes/origin/main
    out $s stash-create-clean "$(git stash create)"
    echo a2 > a.txt; sed '1s/.*/CHANGED/' s.txt > "$d/t" && cat "$d/t" > s.txt
    before=$(git stash create)
    sed '12s/.*/version=2/' s.txt > "$d/t" && cat "$d/t" > s.txt; printf 'name\nversion=2\n' > v.txt
    after=$(git stash create)
    out $s bump-files "$(git diff --name-only "$before" "$after" | tr '\n' '|')"
    git diff --binary "$before" "$after" > "$d/bump.patch" 2>/dev/null; out $s patch-saved $?
    printf 'name\nversion=rewritten\n' > v.txt
    rc $s restore-bump git restore --source="$after" -- ':(literal)v.txt'
    out $s restore-content "$(sed -n 2p v.txt)"
    rc $s stage git add -- ':(literal)a.txt' ':(literal)s.txt' ':(literal)v.txt'
    rc $s apply-reverse git apply --cached -R "$d/bump.patch"
    out $s index-files "$(git diff --cached --name-only | tr '\n' '|')"
    out $s index-s "$(git show :s.txt | sed -n '1p;12p' | tr '\n' '|')"
    out $s index-v "$(git show :v.txt | sed -n 2p)"
    out $s tree-s "$(sed -n '1p;12p' s.txt | tr '\n' '|')"
    out $s tree-v "$(sed -n 2p v.txt)"
    git commit -q -m change && git add -- ':(literal)s.txt' ':(literal)v.txt' && git commit -q -m bump || exit 2
    out $s change-commit "$(git show --name-only --format= HEAD~1 | tr '\n' '|')"
    out $s bump-commit "$(git show --name-only --format= HEAD | tr '\n' '|')"
    state $s status
}

switch_back() {
    mk switch-back
    other
    echo second > g.txt && git add g.txt && git commit -q -m second && git push -q origin HEAD:main && git push -q origin HEAD:refs/heads/feat || exit 2
    cd "$d/repo" || exit 2
    rc $s switch-run git switch --no-overwrite-ignore -c run refs/remotes/origin/main
    git fetch -q origin
    rc $s switch-default git switch main
    branch $s branch
    rc $s merge git merge --ff-only --no-overwrite-ignore refs/remotes/origin/main
    if [ "$(git rev-parse main)" = "$(git rev-parse refs/remotes/origin/main)" ]; then out $s main-updated yes; else out $s main-updated no; fi
    exists $s new-file g.txt
    git branch feat refs/remotes/origin/feat >/dev/null 2>&1
    out $s ahead-equal "$(git rev-list --count refs/remotes/origin/feat..refs/heads/feat -- 2>/dev/null)"
    git switch -q feat && echo more > h.txt && git add h.txt && git commit -q -m more || exit 2
    out $s ahead-one "$(git rev-list --count refs/remotes/origin/feat..refs/heads/feat -- 2>/dev/null)"
    rc $s switch-detach git switch --detach refs/remotes/origin/main
    branch $s detached
    rc $s switch-start git switch main
    branch $s start
}

# starting branch `start` ignores .npmrc (origin/main does not); the file holds a token
mk_start() {
    mk "$1"
    git switch -q -c start && echo .npmrc > .gitignore && git add .gitignore && git commit -q -m ign || exit 2
    echo token > .npmrc
}

order_hazard() {
    mk_start order-hazard
    rc $s switch-run git switch --no-overwrite-ignore -c run refs/remotes/origin/main
    out $s run-sees "$(git status --porcelain --untracked-files=all -- .npmrc | tr '\n' '|')"
    echo new > new.txt
    git diff --binary HEAD > "$d/abandoned.diff" 2>/dev/null
    rc $s reset git reset --hard
    rc $s switch-back git switch start
    rc $s add-n git add -N .
    git diff --binary HEAD >> "$d/abandoned.diff" 2>/dev/null
    out $s diff-has-new-file "$(grep -c '^+++ b/new.txt' "$d/abandoned.diff")"
    out $s diff-has-npmrc "$(grep -c 'npmrc' "$d/abandoned.diff")"
    git reset --hard >/dev/null 2>&1 && git clean -fd >/dev/null 2>&1; out $s reset-clean $?
    out $s npmrc-content "$(cat .npmrc 2>/dev/null)"
    exists $s new-file new.txt
    rc $s branch-delete git branch -D run
    branch $s branch
    state $s status
    # control: the reversed order (add -N and clean on the run's branch) loses the file
    rc $s control-switch-run git switch --no-overwrite-ignore -c run2 refs/remotes/origin/main
    rc $s control-add-n git add -N .
    git diff --binary HEAD > "$d/control.diff" 2>/dev/null
    out $s control-diff-has-npmrc "$(grep -c '^+++ b/.npmrc' "$d/control.diff")"
    git reset --hard >/dev/null 2>&1 && git clean -fd >/dev/null 2>&1; out $s control-reset-clean $?
    exists $s control-npmrc .npmrc
}

order_hazard_resume() {
    mk_start order-hazard-resume
    rc $s switch-pr git switch --no-overwrite-ignore --track refs/remotes/origin/main -c pr
    echo new > new.txt
    rc $s reset git reset --hard
    rc $s switch-back git switch start
    rc $s clean git clean -fd
    out $s npmrc-content "$(cat .npmrc 2>/dev/null)"
    exists $s new-file new.txt
    state $s status
}

resume_switch() {
    mk resume-switch
    echo ignored.txt > .gitignore && git add .gitignore && git commit -q -m ign && git push -q origin main && git fetch -q origin || exit 2
    other
    echo g > g.txt && git add g.txt && git commit -q -m g && git push -q origin HEAD:refs/heads/feat && git push -q origin HEAD:refs/heads/feat2 || exit 2
    cd "$d/repo" && git fetch -q origin && echo secret > ignored.txt || exit 2
    rc $s track git switch --no-overwrite-ignore --track refs/remotes/origin/feat
    branch $s track-branch
    out $s track-upstream "$(git rev-parse --abbrev-ref 'feat@{upstream}' 2>/dev/null)"
    out $s track-content "$(cat ignored.txt)"
    rc $s back git switch main
    git branch feat2 refs/remotes/origin/main >/dev/null 2>&1
    out $s ahead "$(git rev-list --count refs/remotes/origin/feat2..refs/heads/feat2 -- 2>/dev/null)"
    rc $s local-switch git switch --no-overwrite-ignore feat2
    rc $s local-merge git merge --ff-only --no-overwrite-ignore refs/remotes/origin/feat2
    branch $s local-branch
    if [ "$(git rev-parse HEAD)" = "$(git rev-parse refs/remotes/origin/feat2)" ]; then out $s local-head remote; else out $s local-head other; fi
    out $s local-content "$(cat ignored.txt)"
    state $s status
}

for f in dirty_tracked untracked ignored_target_tracks ignore_files ignore_files_nodiff bump_patch switch_back order_hazard order_hazard_resume resume_switch; do
    ( "$f" )
done
