---
name: nx-migrate
description: Migrate an Nx monorepo to the latest Nx version, or to a given target version — applies migrations, resolves dependency conflicts, fixes lint/test/build errors, opens a PR. Use when the user says "nx migrate", "update nx", or "upgrade nx".
argument-hint: "[<version>] [--skip-e2e]"
allowed-tools: Bash, Read, Edit, Write, TodoWrite
---

# Nx Migrate

## Steps

### 1. Pre-flight

Stop unless `nx.json` exists and `git status --short --untracked-files=normal` is empty — ask the user to commit or stash.

Then check the install configuration, before any branch or install exists:

```bash
<skill directory>/scripts/check-install-config.py "$PWD"
```

Call it by its full path, the base directory of this skill plus `scripts/check-install-config.py`; the argument is the working directory, the one holding `nx.json` and where every `npm install` of this skill runs. The script prints one line per finding and nothing when the repository is clean; the docstring of the script is the list of what it checks. **Stop before step 2 (no branch, no install) if it prints any line or exits non-zero**, and report the lines. Do not work around a finding with command-line flags or by editing the setting inside this run's branch. The run edits no configuration outside the repository either (an ignored file such as an ignored `.npmrc`, the user or global npm configuration, the environment): a session running unsupervised only reports these lines. Such a file can hold other settings, a registry token for example, and an edit there changes the machine for every later run without showing up in the PR, so only the person whose configuration it is can make it.

The reason: with `legacy-peer-deps` or `force` in effect, `npm install` in step 4 does not report peer conflicts, and an existing override pins a version without a message, so the ban in step 4 never gets the chance to fire. The install goes green on a combination no package declared compatible, and the migration's lockfile records it. `packageExtensions` and an npm extension file (`.npm-extension.mjs`, `.npm-extension.cjs`) rewrite peer requirements in the same way. The script also reads Yarn and pnpm sources; this skill stays npm-only.

What follows from a line:

- `NPMRC`, `OVERRIDE`, `EXTENSION`, `YAML`, or `CONFIG … project`: the setting is the repository's own state when git tracks the file it comes from (`git ls-files --error-unmatch <file>`; for `NPMRC` and `CONFIG … project` the `.npmrc` in the working directory, for `OVERRIDE` its `package.json`, for `EXTENSION` and `YAML` the file the line names). It is then fixed in the repository's own PR first; the migration waits for it. A file that git does not track is the configuration of the person who started the run, like the next bullet: no change to the repository removes the setting. Report it; the person who started the run removes the setting from that file, outside this run (not the file, which may hold other configuration such as a registry token), then the run is repeated. Because the clean-tree check above already stops on any untracked file that is not ignored, this is an ignored file.
- `CONFIG … user`, `global`, `builtin`, `env` or `cli`: the setting is the configuration of the machine or session that runs the skill, not of the repository. Report it; the person who started the run removes it there, outside this run, then the run is repeated.
- `CONFIG … unknown`: the origin could not be determined. Report it and decide nothing.
- `SKIPPED`: the value could not be read, which is not a pass. Report the line.

Then record the installed version: `node -p "require('./node_modules/nx/package.json').version"`.

**Target version.** The skill takes an optional exact version as its argument (`nx-migrate 23.3.1`); `<target>` below is that version when it is given and `latest` otherwise. Without the argument nothing in this paragraph runs and the skill migrates to the latest release as before. With it, check it before step 2:

```bash
<skill directory>/scripts/check-target.js <installed version> <version>
```

Call it by its full path, the base directory of this skill plus `scripts/check-target.js`. The script accepts only an exact, published version (no range, dist-tag, `v` prefix or partial version) and prints `TARGET`, `INSTALLED`, `ORDER`, `PRERELEASE` and `LATEST` lines; the docstring of the script is the full description. If the installed version could not be read (no `node_modules` yet, a fresh clone), pass `-` instead: the script then checks the version and its publication only and prints `ORDER unknown`, and the order is decided by the check after `npm ci` in step 2. **Stop before step 2 (no branch, no install)** and report the output if it exits non-zero or prints `ORDER equal` or `ORDER older`: an equal version leaves nothing to migrate and a lower one would be a downgrade. The reason for the script: the version ends up in a branch name, a PR title and the provenance check (`verify-nx-provenance.js` runs it inside a shell command), so it is validated as strict semver before any of them sees it. The skill does not decide whether a prerelease target is acceptable: `PRERELEASE yes` is reported, and the caller (for `nx-migrator` a stop) decides.

### 2. Branch

Record the current branch (`git branch --show-current`; empty output means a detached HEAD, record `git rev-parse HEAD` instead), then create the branch from the fetched default branch:

```bash
git fetch origin
gh repo view --json defaultBranchRef -q .defaultBranchRef.name    # <default>
git switch --no-overwrite-ignore -c chore/nx-migrate-<target> refs/remotes/origin/<default>
```

(`chore/nx-migrate-latest` without a target version, `chore/nx-migrate-<version>` with one.)

`--no-overwrite-ignore` because the switch changes the tree and step 1's clean check does not see ignored files: git would otherwise replace an ignored file where `refs/remotes/origin/<default>` tracks something, and a later switch back would delete it. When git refuses, it names the paths and creates no branch: stop and report those paths, a person decides whether the local file may move.

Step 1 checked the tree that was checked out before the switch, so run the check again on the tree the migration installs on (no install has happened yet):

```bash
<skill directory>/scripts/check-install-config.py "$PWD"
```

If it prints any line or exits non-zero, switch back (`git switch <recorded branch>`, or `git switch --detach <recorded commit>`), delete the empty branch with `git branch -D chore/nx-migrate-<target>` (it holds no commits of this run), and handle the lines as step 1 says.

Then install what the new tree declares, with a plain `npm ci` (no flags; a failing install is the repository's own state: stop and report the output). Step 3's `npx nx@<target> migrate <target>` chooses its migrations by comparing against the Nx and plugin versions in `node_modules`, which still hold the tree checked out before the switch. Record the installed version again with the command from step 1: this is the "from" version, and it replaces the one step 1 recorded. With a target version, run `check-target.js` again with this "from" version: the one from step 1 was read on the tree checked out before the switch. On a non-zero exit or any `ORDER` other than `newer`, switch back and delete the empty branch as for the check above (`npm ci` changed only `node_modules`), then report the output and state that `node_modules` now holds the install of the default branch, so the recorded branch needs its own `npm ci` before it is used again.

### 3. Migrate

```bash
npx nx@<target> migrate <target> 2>&1
```

That is `npx nx@latest migrate latest 2>&1` without a target version and `npx nx@<version> migrate <version> 2>&1` with one.

If it fails a **provenance or supply-chain check**, read `supply-chain.md` and follow it before anything else. If it fails mid-run otherwise, `git diff package.json` shows what was partially updated.

### 4. Install

`npm install`. On peer conflicts, fix the incompatible ranges in `package.json` (pin to a version compatible with both sides) and reinstall.

A `legacy-peer-deps` or `force` setting or an override that is already present is caught by step 1; this rule covers what the migration itself would add.

**Never `--force`, `--legacy-peer-deps`, `legacy-peer-deps=true`, or `overrides`/`resolutions`** — npm's own error message suggests the first two. None of them resolves the conflict: the flags and the `.npmrc` setting make npm install past the peer ranges instead of honouring them, and an override forces a version the dependent package never declared support for. Either way the install goes green on a combination no package declared compatible, and the lockfile records it. If no released version satisfies both sides, stop and report the conflicting packages and ranges; the migration waits for the upstream release.

### 5. Run migrations

If `migrations.json` exists: `npx nx migrate --run-migrations 2>&1`. Fix each failing migration's underlying cause (usually a config format change) and re-run. Note every file the migrations create (the migration output names them): step 11 stages them.

**AI-prompt migrations** (Nx 23.1+, "N prompt migrations deferred", written to `tools/ai-migrations/`): apply each in the listed order, honouring its stated preconditions. A no-op is often the correct outcome — e.g. `migrate-ban-types-rule` changes nothing when `@typescript-eslint/ban-types` appears only inside `migrations.json`. Do not invent work. If a prompt names a target the workspace lacks (e.g. `typecheck`), say so and rely on step 8. Note the files an applied prompt creates as well; the prompt files in `tools/ai-migrations/` are not workspace content and are not noted.

When cleaning up, `tools/ai-migrations/` may hold *tracked* files from earlier runs: check `git status --short` for unexpected `D` entries and `git checkout -- <path>` anything you did not create.

### 6. Sync sub-package dependencies

For every `libs/*/package.json` and `apps/*/package.json` (excluding `node_modules`), align versions bumped by the migration with the root:

| Sub-package | Root |
|---|---|
| `peerDependencies` | `dependencies` |
| `devDependencies` | `devDependencies` |
| `dependencies` | `dependencies` |

Then `npm install` again.

### 7. Remove migrations.json

```bash
git ls-files --error-unmatch migrations.json 2>/dev/null && echo TRACKED || echo untracked
rm -f migrations.json
```

If it was **tracked** (leftover from an unfinished run), call the deletion out explicitly in the commit message and PR body.

### 8. Validate: lint → test → build

Use the repo's CLAUDE.md commands, but first confirm they exist — `npx nx show projects` and the `scripts` in `package.json`. A command failing on a missing target is neither a failure nor a pass: run the existing equivalent and note the discrepancy in the PR body. Default:

```bash
npx nx run-many -t lint 2>&1
npx nx run-many -t test 2>&1
npx nx run-many -t build 2>&1
```

Fix root causes (renamed APIs, config options, removed features); no `@ts-ignore` or similar unless unavoidable. Note every file you create while fixing (step 11 stages them). Done when all three exit 0.

### 9. E2E (unless `--skip-e2e`)

`npm run e2e`, fixing failures the same way, and noting every file you create.

### 10. Bump the version

Whether to bump depends only on whether there is a version for CI to read — never on whether the repo "looks like" it publishes anything:

```bash
jq -r '.version // empty' package.json    # non-empty → bump it as described below (the order there matters)
cat VERSION 2>/dev/null                   # only if package.json has none: bump the patch number in the file
```

`npm version` runs the root package's `preversion`, `version` and `postversion` scripts, and it is run without `--ignore-scripts`: the repository's own scripts belong to its version bump. Before the root bump (and before `bump-subpackages.sh`, whose `npm install --ignore-scripts` runs no package scripts), read those three scripts and record the state of the tree: the status in a file in a scratch directory, and a snapshot of the tracked content that is printed and kept (no file). Create the scratch directory so that a failure stops you: run `mktemp -d` as its own command (`mktemp -d -p <scratch directory>` when one was given); it prints the absolute path of the new directory, and a non-zero exit or an empty output ends the step. No shell variable holds the path, so every later command uses the printed absolute path literally (`<printed path>` in the block below is that path, not the scratch directory that was given); a file written into the checkout would count as script output below and be committed in step 11. The status shows new untracked entries; the snapshot of the tracked content, which is kept in the session, shows edits to files that were already modified (the sub-package manifests of step 6, for example), which a status line does not:

```bash
jq -c '.scripts // {} | {preversion, version, postversion} | with_entries(select(.value != null))' package.json   # prints {} when none is set
git status --porcelain --untracked-files=normal > <printed path>/status-before-bump.txt
git stash create
```

`git stash create` prints the sha of a commit holding the tracked content, or nothing on a clean tree (then the snapshot is `HEAD`); it stores nothing in the stash list and leaves the tree alone. Keep what it printed, and run it as the bare command, without a trailing comment (an exact permission rule such as `Bash(git stash create)` does not match text after the command).

Run `npm version patch --no-git-tag-version`, then sync the lockfile with `npm install --package-lock-only --ignore-scripts` (no lifecycle script runs in it and no package is installed or removed; npm only rewrites `package-lock.json` and its hidden copy `node_modules/.package-lock.json`, and `bump-subpackages.sh` resyncs `node_modules` when sub-packages are bumped), and take the same two snapshots again (`git stash create` printed and kept, `HEAD` when it prints nothing) after both, since no script runs in the sync. Then `git diff --name-status <sha before> <sha after>` lists every tracked file the bump changed: `package.json` and `package-lock.json` are the bump itself, every other file listed is output of the version scripts that are set. The difference of the two status files shows new untracked entries. With `--untracked-files=normal` a new untracked directory is one `?? <dir>/` entry, not one per file; it is allowed as a whole when a named script ran, and it is named as `git status` printed it. Every new untracked entry, a file or a `?? <dir>/` entry (checked before that directory is listed) as well as each entry of the directory listing below, whose path (the part after the `?? ` prefix) contains `'` or starts with `"` (C-quoted) is reported as git printed it, and the run stops here, before step 11, without listing that directory. It is not unquoted or escaped by hand: inside single quotes `'` ends the quoting, and the C-quoted text is not the path, so a transformation that goes wrong acts on another path. Only for a `?? <dir>/` entry that passed this check, list the files inside with `git status --porcelain --untracked-files=all -- '<dir>/'` and check each of those entries the same way. The comparison cannot see two things: a script's edit of an untracked file that already existed before the bump, and a file a script adds inside an untracked directory that already existed before the bump (its `?? <dir>/` line does not change); only entries a named script can have produced are counted. Note the scripts that ran and the files and entries they changed, or "none set".

CI derives the image tag, the git tag and the release from that value and does not fail when it already exists: the image is published as `latest` only, the tag and the release are skipped with a warning, and every job stays green. A missing bump is therefore silent — nothing points at it until someone looks for the version that was never published. If neither source holds a version there is nothing to bump — say so in the PR body instead of adding a version field.

Sub-packages are bumped the same way. Every package under `libs/*` and `apps/*` that already exists on the base commit, has a `version` and anything changed in its directory (so changes from steps 5, 6 and 8 all count) gets a patch bump. A package the run created is left alone, since it has no earlier version to move on from:

```bash
<skill directory>/scripts/bump-subpackages.sh    # run in the workspace root, before the commit in step 11
```

It prints one line per bumped package (directory, old version, new version) on stdout, runs one `npm install --ignore-scripts` after all bumps, and reports everything else on stderr or by its exit status:

- **Bumped:** a package that is in `HEAD` with a non-empty string `version` and has changes in its directory: a tracked difference from `HEAD` (staged or not) or an untracked file that is not git-ignored. Build or test output that is not ignored therefore also counts, which at worst gives a harmless bump. Versions follow `npm version patch` (`2.0.0-rc.1` becomes `2.0.0`, `1.0.0+build` becomes `1.0.1`), as for the root bump. No package scripts run (`--ignore-scripts`), so a library's `preversion`/`postversion` cannot act during the migration.
- **Already bumped** (the version differs from `HEAD`): not bumped again, but printed again and the lockfile is synced again, so running it twice, or rerunning after a failed `npm install`, is safe. It counts as a bump only if the version ranks above `HEAD`'s by semantic-version precedence; a lower or equal one, or one that is not valid semver, stops the script (a hand-edited downgrade is not reported as a bump). The script checks every package before it bumps the first one, so an invalid manifest stops it with nothing bumped; if `npm version` itself fails in the middle (not for an invalid version, which pass 1 stops on), the message names the packages bumped so far and says the lockfile is not synced. Fix the cause and rerun the script: it picks up where it stopped.
- **Skipped with a `# note:` line:** a symlinked package directory; a directory name with a tab or newline; a package that is not in `HEAD` (new or renamed in this run; a git-ignored package directory has its own note); a package without a `version` in `HEAD` whose directory has changes (none is added); a directory with changes but no regular `package.json` (deleted, a directory, a symlink); workspace globs other than exactly `libs/*` and `apps/*` (nested packages are not bumped, their pins are still checked; an entry with a leading `!` is listed as an exclusion that is not applied); no `libs/` and no `apps/` at all.
- **Skipped silently**, because nothing is hidden: an unchanged package, with or without a `version`; an unchanged directory without a `package.json`; a plain file or dot-directory under `libs/` or `apps/`; a package directory that was deleted.
- **Exit 1 with a message** (stop and report): a missing `git`, `jq`, `npm` or `node`; `HEAD` unresolvable; no `nx.json` (the script must run in the Nx workspace root, not inside a package), or an empty, whitespace-only or non-file one; a root `package.json` that is not one valid JSON object (its own `version` is not checked: the script never bumps the root), or whose `workspaces` field, if present, is not an array of strings or an object with a `packages` array of strings (`null`, a string, a boolean and an object without `packages` are rejected; a missing field or `[]` is accepted on purpose, because publishable libraries need not be npm workspaces); `libs` or `apps` as a symlink or a file; an empty, multi-document, non-object or invalid manifest in `HEAD` or in the working tree; a `version` that is not a non-empty string or contains a control character, or that is not valid semver in a package about to be bumped (`npm`'s loose spellings `v1.0.0`, `=1.0.0` and surrounding whitespace are accepted; a committed version that is not semver stops the script with nothing bumped, and this run cannot fix HEAD: report it for the owner); a version removed in this run; `npm version` failing or not changing the version; a failing `npm install`; a dependency spec in the root manifest or in a workspace manifest (workspaces as npm maps them: explicit entries, `libs/**`, other globs, `!` exclusions) on a package about to be bumped that its current version satisfies and its bumped version does not (`"1.0.0"`, `"=1.0.0"`, `^0.0.1`, `<=0.0.1`, `"1.0.0"` against `1.0.0+build`, an alias of the package onto itself such as `npm:<name>@1.0.0`; decided by npm's own `semver`, so `~0.0.1`, `>=0.0.1` and `*` pass), which no longer resolves to the workspace package once it has moved (npm looks the spec up in the registry: a 404 if it was never published); the npm modules for that check (`semver`, `npm-package-arg`, `@npmcli/map-workspaces`, found from the `npm` on `PATH`, also through a version manager's shim) not loadable, or the workspaces not mappable (a duplicate workspace name, for example). A package outside the mapped workspaces or without a `name` gets a note, and its pins are not checked. The script lists each pin and this run bumps nothing: report it, do not edit the pin yourself, and let the owner decide between a range and the new version.

`npm version` rewrites the whole `package.json`, for the root bump as for the sub-packages. Observed with npm 12.2.0 on an indented manifest: arrays expanded to one element per line, `\uXXXX` escapes written as the literal characters, a final newline added. The diff of a bumped manifest can therefore show more than the version line; say so in the PR body, so a reviewer does not take it for an unrelated change.

"Changed" is measured against `HEAD` because step 1 requires a clean tree and nothing is committed before step 11. The reason is the one above, applied to each library: a publish keyed on the library's own version skips an unchanged version without failing, so the rule is "has a version and changed", never "looks published". A sub-package without a `version` gets none added. Put the printed lines and the notes in the PR body. After a bump, re-run validation.

### 11. Commit

```bash
git add -u
git -c core.quotePath=false status --porcelain --untracked-files=all   # every ?? entry, file by file
git add -- ':(literal)<entry>' …   # the ?? entries noted in steps 5, 8, 9 and 10 (migration output, your own fixes, version-script output) and the ?? entries of the root `package.json`, `package-lock.json`, `nx.json`, everything under `.nx/` and root-level `tsconfig*.json`, one argument per entry; `git add -u` adds tracked files only
```

Step 1 required a clean tree, but its check does not see ignored files, so a file that only the branch you started from ignores can show up as `??` after the switch in step 2. A `??` entry is therefore not necessarily from this run, and not every one is workspace content: lint, test, build, e2e or install output that the repository does not ignore shows up too. Stage only the entries noted in steps 5, 8, 9 and 10 and the `??` entries of the paths `package.json`, `package-lock.json`, `nx.json` (all at the root), everything under `.nx/` and the root-level `tsconfig*.json` files (no `/` in the path). Those paths are not passed to `git add` by name: a `git add` with a fixed list aborts as a whole, staging nothing, when one path does not exist (`.nx/` exists only when Nx wrote into it), and an unmatched `tsconfig*.json` glob reaches git literally and fails the same way. Tracked changes to them, deletions included, are staged by `git add -u`, and their untracked files come from the status listing, which can only name paths that exist. Do not use `git add -A`, and do not edit `.gitignore`. List every other `??` entry that is output of a command you ran in this run in the PR body under "Untracked files not committed" (step 12), as git printed it, instead of dropping it silently; an entry you cannot attribute to a command of this run (a local file such as `.env.local`) stays out of the PR body and goes into your report. Each staged entry is a `:(literal)` argument because git reads the arguments as pathspecs: without it a `*`, `?` or `[` in a name would also stage other untracked files. A staged entry (one noted in steps 5, 8, 9 or 10, or one of the paths above) whose status line contains `'` or starts with `"` (C-quoted, which git also does for a name with a space) is reported as git printed it and stops the run before the commit: the path cannot be put inside `':(literal)…'` safely and is not unquoted or escaped by hand. Entries of step 10 have passed that check already.

Done when `git status --porcelain --untracked-files=all` shows no `??` entry except those listed in the PR body or named in your report.

```
chore(deps): migrate nx to vX.Y.Z

- Run nx migrate <target>
- Apply generated migrations
- Resolve peer dependency conflicts (if any)
- Sync sub-package dependency versions (if any)
- Bump patch versions of changed sub-packages (if any)
- Fix lint/test/build issues (list specific fixes if any)
```

### 12. Push and open PR

```bash
git push -u origin chore/nx-migrate-<target>
gh pr create --base <default> --title "chore(deps): migrate nx to <target>" --body "$(cat <<'EOF'
## Summary

- Migrated Nx workspace to vX.Y.Z (the target version, when one was given; then also the newest stable release from the `LATEST` line of `check-target.js`, when it is newer)
- Applied all generated migrations
- Bumped patch versions of changed sub-packages: <list, or none>
- Root version scripts run by the bump: <names and the files and entries they changed, or none set>
- Untracked files not committed: <paths, or none>
- All lint, test, and build checks pass

## Test plan

- [ ] `npm run lint` passes
- [ ] `npm run test` passes
- [ ] `npm run build` passes
- [ ] `npm run e2e` passes
EOF
)"
```

The PR is always opened against the default branch, never on the branch of another PR (no stacked PRs). Reason: the merge deletes the head branch, and an open PR based on it would be closed or retargeted without anybody deciding it.

Done when the PR is open and lint, test, build (and e2e unless skipped) pass locally.
