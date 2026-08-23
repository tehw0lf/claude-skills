---
name: prune-branches
description: Delete local git branches whose content already landed on the default branch. Detects squash-merged branches that `git branch -d` refuses to remove, previews every candidate with a verdict, and only deletes after confirmation. Optionally switches HEAD back to the default branch and fast-forwards it. Use when the user says "prune branches", "delete merged branches", "clean up branches", "tote branches löschen" or similar.
argument-hint: [all] [--yes]
allowed-tools: Bash, TodoWrite
---

# Prune Merged Branches

Delete local branches that are fully merged, including squash-merged ones. Never delete a branch that still holds unmerged content.

## Arguments

- no argument — operate on the repository containing the current working directory
- `all` — scan every git repository under the coding workspace (default `~/Nextcloud/Coding`, override with `$CODING_ROOT`)
- `--yes` — skip the confirmation prompt (still skips anything the check flags as unsafe)

## Why this is not just `git branch -d`

`git branch -d` only recognises branches reachable from the default branch. Squash-merged and rebase-merged PRs leave the local branch unreachable, so `-d` refuses and the branch accumulates forever. This skill compares *content* instead of ancestry.

## Steps

### 1. Refresh remote state first

**This step is mandatory and must never be skipped.** A stale `origin/<default>` makes merged branches look unmerged and can make an unmerged branch look safe. Every judgement below depends on fresh refs.

```bash
git fetch --prune origin
```

For `all`, iterate repositories with `find "$CODING_ROOT" -maxdepth 3 -type d -name .git -print0` and read them with `while IFS= read -r -d ''` — workspace paths contain spaces, and a plain `for` loop over unquoted `find` output silently breaks on them.

### 2. Determine the default branch

Do not assume `main`:

```bash
base=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')
base="${base:-main}"
```

### 3. Collect candidates

Branches whose upstream is gone (the PR was merged and the remote branch deleted):

```bash
git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads/ | grep '\[gone\]' | awk '{print $1}'
```

Never treat the default branch itself as a candidate, and never delete the branch of a repository whose working tree is dirty without saying so.

### 4. Classify each candidate

Apply these tests in order and stop at the first that matches:

1. **`ahead: 0`** — `git rev-list --count "origin/$base..$b"` returns 0. The branch has no commits of its own; it is a stale pointer at an old state. **SAFE.**

   This test comes first on purpose. Such a branch usually still shows a large diff against `origin/$base`, but that diff is everything `$base` gained since — not work at risk. Judging it by the diff alone produces a false "unmerged" verdict.

2. **Identical tree** — `git diff --quiet "origin/$base" "$b"` succeeds. **SAFE.**

3. **Squash-merge detection** — replay the branch's cumulative diff as a single commit on the merge base and ask whether `$base` already contains an equivalent patch:

   ```bash
   mb=$(git merge-base "origin/$base" "$b")
   tmp=$(git commit-tree "$(git rev-parse "$b^{tree}")" -p "$mb" -m probe)
   git cherry "origin/$base" "$tmp" | grep -q '^-' && echo SAFE_SQUASHED
   ```

   `git cherry` marks a commit `-` when an equivalent patch exists upstream. This is a heuristic, not proof.

4. **Otherwise** — treat as **NEEDS REVIEW**, never as "delete anyway".

### 5. Verify anything flagged NEEDS REVIEW

Do not stop at the verdict and do not hand the user a bare list. For each such branch, show what is actually at stake:

```bash
git log --oneline "origin/$base..$b"
git diff --stat "$(git merge-base origin/$base $b)" "$b"
```

Then diff each touched file against `origin/$base` and read the result. A branch is still safe when its changes are present in `$base` in a *newer* form — an earlier PR whose follow-up refined it. It is genuinely unmerged only when `$base` lacks the change or contradicts it. Report which of the two it is, with the concrete evidence.

### 6. Present the preview

Print one table for the whole run, grouped by repository:

```
repo                 branch                          ahead  verdict
TypeScript/numveil   fix/pin-action-shas             1      SAFE (identical)
TypeScript/btrain    fix/csp-script-hashes           3      SAFE (squash-merged)
workflows            chore/remove-nx-cloud           0      SAFE (no own commits)
JavaScript/color     feature/wip-thing               2      NEEDS REVIEW
```

State the totals and stop for confirmation, unless `--yes` was passed. Branches marked NEEDS REVIEW are excluded from deletion by default — list them separately as skipped and say why.

### 7. Delete

Only after confirmation:

```bash
# if HEAD sits on a doomed branch, leave it first
git checkout "$base"
git branch -D "$b"
```

`-D` is required — `-d` rejects squash-merged branches, which is the whole reason this skill exists. That is also why steps 4–6 carry the safety burden: nothing may reach this point unverified.

### 8. Bring the default branch up to date

For each touched repository with a clean working tree:

```bash
git pull --ff-only
```

Skip dirty repositories and say so rather than stashing. Never merge or rebase here — if the fast-forward is refused, the repository has diverged and needs a human.

### 9. Report

Summarise per repository: how many branches were deleted, which were skipped for review, which repositories were left alone because they were dirty, and where `main` was updated. If a heuristic produced a verdict you later corrected by reading the diff, say that plainly — it tells the user how much to trust the next run.

## Rules

- Never delete a branch that is not the current repository's default branch and has content not present in the default branch.
- Never skip `git fetch --prune`.
- Never use `git branch -d` as the safety mechanism; the classification is the safety mechanism.
- Never stash, reset, or discard uncommitted work to make a repository eligible.
- When a repository has no remote or no `origin/HEAD`, report it and move on instead of guessing a base branch.
