# TODO

Open work, grouped into bundles. One bundle is one pull request.

## Rules for this file

- **This file is not extended.** It lists exactly the bundles below. A new idea or a new gap is a GitHub issue, not a line here.
- **Finished work is deleted from this file.** When a bundle is merged, delete its whole section in the same pull request or the next one. No done list, no history: git and the closed issues hold that. When no bundle is left, delete this file.
- **Follow-ups, and their follow-ups, are handled as before:** every follow-up finding of a review becomes a GitHub issue and is worked through the plugin chain (planner, worker, independent review). They are tracked as issues and do not go into this file.
- **How a bundle is worked:** (1) run `work-issue:issue-planner` again for each issue of the bundle against the current `origin/main`, because the plans below were written on the base of 2026-11-02 and the files have moved since; (2) one worker for the whole bundle, with every plan named in its prompt, one branch, one pull request with a `Closes #<n>` line per issue, one patch bump of the touched plugin as the last commit; (3) independent review (`pr-review:pr-reviewer`, round 1 on Opus) because the pull request arises from issues; (4) merge once the review of the current head is clean and the required check is green.
- Work the bundles in the order below.

## Bundles

### 1. Steps 6 to 8 of `work-issue` (plugin `work-issue`)

#403 first: step 7 restores from the index, which drops the bump itself. #402 must narrow the stop that #374 plans, otherwise every run in a repository that does not track its lockfile ends there.

- #374 bump as its own commit when the change touches the manifest or lockfile (plan 6587377205)
- #403 step 7 may undo the bump (plan 6587492427)
- #402 lockfile that the bump creates untracked (plan 6587510558)
- #406 untracked `package-lock.json` for npm (no plan yet)

### 2. Branch update, merge and resume (plugin `work-issue`)

Order inside the bundle: #377, then #396, then #404.

- #377 version check before the merge and on a branch update (plan 6587248099)
- #396 resolve a version-only conflict in the branch update (plan 6587383376)
- #404 hand a conflicting own PR back to the worker (plan 6587509597)
- #405 `pending-check` PR that became conflicting (no plan yet)

### 3. Finish mode (plugin `work-issue`)

- #391 validation that cannot run in finish mode (plan 6587339804)
- #398 bump check of `finish-branch.md` (plan 6587362326)

### 4. `repo-commands.py` fallback rows (plugin `work-issue`)

Both plans edit the same docstring sentence and the same sentence in `docs/coupling.md`. #397 needs a real Composer run: PHP and Composer from the Arch packages, only extracted into scratch, checksums and signatures checked, no global install, no Docker.

- #376 FALLBACK rows for bun, Maven, Gradle without wrapper, Poetry, PDM, Pipenv and requirements-only (plan 6587255911)
- #397 Composer rows (plan 6587408075)

### 5. `nx-migrator` (plugin `nx-migrate`)

Order inside the bundle: #394, then #395, then #400. #400 replaces the stop that #395 adds and changes step 10 of the `nx-migrate` skill and the docstring of `bump-subpackages.sh`.

- #394 report-only `gh pr checks` call without checks (plan 6587347546)
- #395 version and `BEHIND` check before the merge (plan 6587360376)
- #400 branch update and second bump (plan 6587532935)
