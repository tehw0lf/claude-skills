---
name: prune-branches
description: Prune local git branches whose content already landed on the default branch, including squash-merged ones `git branch -d` refuses. Previews every candidate with a verdict and deletes only after confirmation. Use when the user says "prune branches", "delete merged branches", "clean up branches", "tote branches löschen" or similar.
argument-hint: "[all] [--yes]"
allowed-tools: Bash, TodoWrite
---

# Prune Merged Branches

Never delete a branch that still holds unmerged content. `git branch -d` checks ancestry, which squash- and rebase-merges break; this skill compares *content*, so the **classification** is the safety mechanism and deletion uses `-D`.

## Arguments

- no argument — the repository containing the current working directory; only when the working directory is not inside any repository, every git repository under `$CODING_ROOT` (default `~/Nextcloud/Coding`), and the run says so
- `all` — every git repository under `$CODING_ROOT`, also from inside a repository
- `--yes` — skip the confirmation prompt only; eligibility is unchanged. It does not skip the confirmation when the scope is `all` with `reason=not-in-repository` (the fallback): there the user did not ask for all repositories, so the preview is always confirmed

## Steps

### 1. Classify

```bash
scripts/classify.sh            # no argument; or: scripts/classify.sh --all
```

The script decides the scope and names it in its first line, `# scope=current  <toplevel>` or `# scope=all  <root>  reason=explicit|not-in-repository`. Do not widen the scope yourself: a `SKIPPED: inside a repository git cannot read` line is reported, not answered with `--all`; an explicit path that is not a repository prints `SKIPPED: not a git repository`. The script is read-only. Per repository it runs `git fetch --prune origin`, takes the default branch from `origin/HEAD`, and emits `repo  branch  ahead  verdict  sha` for every `[gone]` branch plus every branch with no upstream. It skips a repository — never guessing — when the fetch fails or `origin`/`origin/HEAD` is missing; report those. Verdicts:

- `SAFE (no own commits | identical | squash-merged)` — eligible. `squash-merged` rests on `git cherry`, a heuristic.
- `NEEDS REVIEW` — never eligible without step 2.
- `KEPT (<rule>)` — never deleted, never counted. Rules: `$XDG_CONFIG_HOME/prune-branches-keep` (all repos) or `.git/prune-branches-keep` (one repo), one glob per line; names containing `keep/`, `archive/`, `wip/`, `working-state`; `git config branch.<name>.pruneKeep true`; a git note on the tip.
- `INELIGIBLE (no upstream)` — never pushed, so nothing proves its content exists elsewhere. Never deleted.

### 2. Verify every NEEDS REVIEW branch

Do not hand the user a bare list. For each:

```bash
git log --oneline "refs/remotes/origin/$base..refs/heads/$b"
git diff --stat "$(git merge-base "refs/remotes/origin/$base" "refs/heads/$b")" "refs/heads/$b"
```

Branches are written as `refs/heads/$b` because a bare name resolves to a same-named tag first, which would show the tag's history instead of the branch's.

Then diff each touched file against `refs/remotes/origin/$base` and read it. Decide between: the change **is** in `$base` in a newer form (a follow-up refined it — safe in substance), or `$base` **lacks or contradicts** it (genuinely unmerged). Report which, with the evidence. Done when every NEEDS REVIEW branch has a reading backed by a diff, or is stated as unresolved.

### 3. Preview

Start with the scope (the current repository, or all repositories under `<root>` and why: `--all` given, or the working directory is not inside a repository) and name every repository the script reported, including those without candidates and the skipped ones. Then one table for the whole run, grouped by repository:

```
repo                 branch                          ahead  verdict
TypeScript/numveil   fix/pin-action-shas             1      SAFE (identical)
TypeScript/btrain    fix/csp-script-hashes           3      SAFE (squash-merged)
workflows            chore/remove-nx-cloud           0      SAFE (no own commits)
JavaScript/color     feature/wip-thing               2      NEEDS REVIEW
```

State totals, list NEEDS REVIEW, KEPT and INELIGIBLE branches separately as skipped with the reason, flag dirty working trees, and stop for confirmation unless `--yes` (which never applies to a `reason=not-in-repository` scope).

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
