---
name: nx-migrate
description: Migrate an Nx monorepo to the latest Nx version. Runs nx migrate latest, applies migrations, resolves package conflicts, fixes lint/test/build errors, and opens a PR. Use when the user says "nx migrate", "update nx", or "upgrade nx".
argument-hint: [--skip-e2e]
allowed-tools: Bash, Read, Edit, Write, TodoWrite
---

# Nx Migrate to Latest

Migrate the active Nx monorepo to the latest Nx version, resolve conflicts, fix errors, and open a PR.

## Steps

### 1. Pre-flight check

Verify we're in an Nx workspace and on a clean branch:

```bash
# Confirm nx workspace
test -f nx.json || { echo "Not an Nx workspace"; exit 1; }

# Confirm clean git state
git status --short
```

If there are uncommitted changes, stop and ask the user to commit or stash them first.

Get current Nx version for the branch name:
```bash
node -e "console.log(require('./node_modules/nx/package.json').version)"
```

### 2. Create feature branch

```bash
git checkout -b chore/nx-migrate-latest
```

### 3. Run nx migrate latest

```bash
npx nx@latest migrate latest 2>&1
```

This updates `package.json` and generates `migrations.json` if migrations exist.

### 4. Install updated dependencies

```bash
npm install 2>&1
```

Resolve any peer dependency conflicts:
- If `npm install` fails with peer conflicts, try `npm install --legacy-peer-deps`
- Read the error output and manually resolve incompatible version ranges in `package.json`
- After manual edits, run `npm install` again

### 5. Run migrations

If `migrations.json` was generated:

```bash
npx nx migrate --run-migrations 2>&1
```

If individual migrations fail, read the error, fix the underlying issue (usually a config file format change), and re-run.

### 6. Clean up migrations.json

```bash
rm -f migrations.json
```

### 7. Validate: lint → test → build

Run the project's validation commands. For this workspace, check CLAUDE.md for the project-specific commands. Default:

```bash
npm run lint 2>&1
npm run test 2>&1
npm run build 2>&1
```

For each failure:
- Read the error output carefully
- Fix the root cause (updated API, renamed config option, removed feature)
- Re-run the failing command to confirm the fix
- Do not suppress errors with `// @ts-ignore` or similar unless absolutely unavoidable

Repeat until all three pass with exit code 0.

### 8. Run E2E tests (unless --skip-e2e was passed)

```bash
npm run e2e 2>&1
```

Fix any E2E failures the same way as above.

### 9. Commit

Stage all changes and commit:

```bash
git add package.json package-lock.json nx.json .nx/ tsconfig*.json
git add -u  # stage any other modified tracked files
```

Commit message format:
```
chore(deps): migrate nx to vX.Y.Z

- Run nx migrate latest
- Apply generated migrations
- Resolve peer dependency conflicts (if any)
- Fix lint/test/build issues (list specific fixes if any)
```

### 10. Push and open PR

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

## Error handling

- **`nx migrate` fails mid-run**: Check if `package.json` was partially updated. Run `git diff package.json` to see what changed and fix manually.
- **Peer dependency hell**: Pin the conflicting package to a version compatible with both nx and the other package, or find the nx-recommended version in the nx migration guide.
- **Test failures after migration**: Usually caused by renamed APIs or changed behavior. Check the nx changelog/migration guide for breaking changes in the updated version range.
- **Build failures**: Often TypeScript strict mode changes or removed Angular APIs. Check `@angular/core` and `@angular/compiler` changelogs.
