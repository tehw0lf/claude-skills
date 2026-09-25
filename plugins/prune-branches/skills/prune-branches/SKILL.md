---
name: prune-branches
description: Prune local git branches whose content already landed on the default branch, including squash-merged ones `git branch -d` refuses. Previews every candidate with a verdict and deletes only after confirmation. Use when the user says "prune branches", "delete merged branches", "clean up branches", "tote branches löschen" or similar.
argument-hint: [all] [--yes]
allowed-tools: Bash, TodoWrite
---

# Prune Merged Branches

Never delete a branch that still holds unmerged content. `git branch -d` checks ancestry, which squash- and rebase-merges break; this skill compares *content*, so the **classification** is the safety mechanism and deletion uses `-D`.

## Arguments

- no argument — the repository containing the current working directory
- `all` — every git repository under `$CODING_ROOT` (default `~/Nextcloud/Coding`)
- `--yes` — skip the confirmation prompt only; eligibility is unchanged

## Steps

### 1. Classify

```bash
scripts/classify.sh "$(git rev-parse --show-toplevel)"   # or: scripts/classify.sh --all
```

The script is read-only. Per repository it runs `git fetch --prune origin`, takes the default branch from `origin/HEAD`, and emits `repo  branch  ahead  verdict  sha` for every `[gone]` branch plus every branch with no upstream. It skips a repository — never guessing — when the fetch fails or `origin`/`origin/HEAD` is missing; report those. Verdicts:

- `SAFE (no own commits | identical | squash-merged)` — eligible. `squash-merged` rests on `git cherry`, a heuristic.
- `NEEDS REVIEW` — never eligible without step 2.
- `KEPT (<rule>)` — never deleted, never counted. Rules: `$XDG_CONFIG_HOME/prune-branches-keep` (all repos) or `.git/prune-branches-keep` (one repo), one glob per line; names containing `keep/`, `archive/`, `wip/`, `working-state`; `git config branch.<name>.pruneKeep true`; a git note on the tip.
- `INELIGIBLE (no upstream)` — never pushed, so nothing proves its content exists elsewhere. Never deleted.

### 2. Verify every NEEDS REVIEW branch

Do not hand the user a bare list. For each:

```bash
git log --oneline "origin/$base..$b"
git diff --stat "$(git merge-base "origin/$base" "$b")" "$b"
```

Then diff each touched file against `origin/$base` and read it. Decide between: the change **is** in `$base` in a newer form (a follow-up refined it — safe in substance), or `$base` **lacks or contradicts** it (genuinely unmerged). Report which, with the evidence. Done when every NEEDS REVIEW branch has a reading backed by a diff, or is stated as unresolved.

### 3. Preview

One table for the whole run, grouped by repository:

```
repo                 branch                          ahead  verdict
TypeScript/numveil   fix/pin-action-shas             1      SAFE (identical)
TypeScript/btrain    fix/csp-script-hashes           3      SAFE (squash-merged)
workflows            chore/remove-nx-cloud           0      SAFE (no own commits)
JavaScript/color     feature/wip-thing               2      NEEDS REVIEW
```

State totals, list NEEDS REVIEW, KEPT and INELIGIBLE branches separately as skipped with the reason, flag dirty working trees, and stop for confirmation unless `--yes`.

### 4. Delete SAFE branches

```bash
git checkout "$base"     # only if HEAD sits on a doomed branch
git branch -D "$b"
```

### 5. Fast-forward the default branch

In each touched repository with a clean tree: `git pull --ff-only`. Skip dirty repositories and say so — never stash. If the fast-forward is refused, the repository has diverged: leave it for a human, never merge or rebase.

### 6. Report

Per repository: branches deleted (with SHA), skipped and why, repositories left alone (dirty, skipped by the script), and where the default branch was updated. If reading a diff overturned a verdict, say so plainly — it tells the user how far to trust the next run.

## Rules

- Never stash, reset, or discard uncommitted work to make a repository eligible.
- Never delete a branch the classification did not clear — not with `--yes`, not because it looks safe.
