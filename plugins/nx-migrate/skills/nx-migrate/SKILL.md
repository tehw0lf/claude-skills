---
name: nx-migrate
description: Migrate an Nx monorepo to the latest Nx version — applies migrations, resolves dependency conflicts, fixes lint/test/build errors, opens a PR. Use when the user says "nx migrate", "update nx", or "upgrade nx".
argument-hint: [--skip-e2e]
allowed-tools: Bash, Read, Edit, Write, TodoWrite
---

# Nx Migrate to Latest

## Steps

### 1. Pre-flight

Stop unless `nx.json` exists and `git status --short` is empty — ask the user to commit or stash. Record the installed version: `node -p "require('./node_modules/nx/package.json').version"`.

### 2. Branch

`git checkout -b chore/nx-migrate-latest`

### 3. Migrate

```bash
npx nx@latest migrate latest 2>&1
```

If it fails a **provenance or supply-chain check**, read `supply-chain.md` and follow it before anything else. If it fails mid-run otherwise, `git diff package.json` shows what was partially updated.

### 4. Install

`npm install`. On peer conflicts, fix the incompatible ranges in `package.json` (pin to a version compatible with both sides) and reinstall.

**Never `--legacy-peer-deps`, `legacy-peer-deps=true`, or `overrides`/`resolutions`.** None of them resolves the conflict: the flag and the `.npmrc` setting stop npm from checking peer ranges at all, and an override forces a version the dependent package never declared support for. Either way the install goes green on a combination no package declared compatible, and the lockfile records it. If no released version satisfies both sides, stop and report the conflicting packages and ranges; the migration waits for the upstream release.

### 5. Run migrations

If `migrations.json` exists: `npx nx migrate --run-migrations 2>&1`. Fix each failing migration's underlying cause (usually a config format change) and re-run.

**AI-prompt migrations** (Nx 23.1+, "N prompt migrations deferred", written to `tools/ai-migrations/`): apply each in the listed order, honouring its stated preconditions. A no-op is often the correct outcome — e.g. `migrate-ban-types-rule` changes nothing when `@typescript-eslint/ban-types` appears only inside `migrations.json`. Do not invent work. If a prompt names a target the workspace lacks (e.g. `typecheck`), say so and rely on step 8.

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

Fix root causes (renamed APIs, config options, removed features); no `@ts-ignore` or similar unless unavoidable. Done when all three exit 0.

### 9. E2E (unless `--skip-e2e`)

`npm run e2e`, fixing failures the same way.

### 10. Bump the version

Whether to bump depends only on whether there is a version for CI to read — never on whether the repo "looks like" it publishes anything:

```bash
jq -r '.version // empty' package.json    # non-empty → npm version patch --no-git-tag-version && npm install
cat VERSION 2>/dev/null                   # only if package.json has none: bump the patch number in the file
```

CI derives the image tag, the git tag and the release from that value and does not fail when it already exists: the image is published as `latest` only, the tag and the release are skipped with a warning, and every job stays green. A missing bump is therefore silent — nothing points at it until someone looks for the version that was never published. If neither source holds a version there is nothing to bump — say so in the PR body instead of adding a version field. After a bump, re-run validation.

### 11. Commit

```bash
git add package.json package-lock.json nx.json .nx/ tsconfig*.json
git add -u
```

```
chore(deps): migrate nx to vX.Y.Z

- Run nx migrate latest
- Apply generated migrations
- Resolve peer dependency conflicts (if any)
- Sync sub-package dependency versions (if any)
- Fix lint/test/build issues (list specific fixes if any)
```

### 12. Push and open PR

```bash
git push -u origin chore/nx-migrate-latest
gh pr create --title "chore(deps): migrate nx to latest" --body "$(cat <<'EOF'
## Summary

- Migrated Nx workspace to vX.Y.Z
- Applied all generated migrations
- All lint, test, and build checks pass

## Test plan

- [ ] `npm run lint` passes
- [ ] `npm run test` passes
- [ ] `npm run build` passes
- [ ] `npm run e2e` passes
EOF
)"
```

Done when the PR is open and lint, test, build (and e2e unless skipped) pass locally.
