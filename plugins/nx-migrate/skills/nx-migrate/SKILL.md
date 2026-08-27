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

#### If this fails with a provenance error

> **Durable rule:** never disable a supply-chain check as a reflex. Verify the
> artifact independently; if it verifies, use the narrowest override scoped to
> one command; if it does not, stop and report.
>
> **The specific bug below expires.** It is fixed in nx 23.1.2. Check the
> installed version first — if it is 23.1.2 or newer and you still see this
> error, the cause is something else, so investigate rather than applying the
> workaround.

On npm 12+, `nx migrate` can abort with:

```
An error occurred while checking the provenance of nx@latest.
Error: No attestation URL found
```

**Do not reflexively set `NX_SKIP_PROVENANCE_CHECK`.** The nx source comments
explicitly warn against it, and blindly disabling a supply-chain check is the
wrong default. This specific error is usually an nx bug, not a compromised
package: nx reads `dist.attestations.url` off `npm view --json` output, but
npm 12 always wraps that output in a JSON **array**, so the lookup is
`undefined` for every version. Nx 23.1.2+ contains the fix
(`Array.isArray(parsed) ? parsed[0] : parsed`).

Verify the package is genuine before proceeding. Resolve the concrete version
first (`npm view nx version`), then run the same checks nx would have:

```bash
node -e "
(async () => {
  const V = process.argv[1];
  const cp = require('child_process');
  const view = JSON.parse(cp.execSync('npm view \"nx@'+V+'\" --json --silent',{encoding:'utf8'}));
  const v = Array.isArray(view) ? view[0] : view;
  const att = await (await fetch(v.dist.attestations.url)).json();
  const prov = att.attestations.find(a => a.predicateType === 'https://slsa.dev/provenance/v1');
  const payload = JSON.parse(Buffer.from(prov.bundle.dsseEnvelope.payload,'base64').toString());
  const wf = payload.predicate.buildDefinition.externalParameters.workflow;
  const distSha = Buffer.from(v.dist.integrity.replace('sha512-',''),'base64').toString('hex');
  console.log('repository :', wf.repository);
  console.log('workflow   :', wf.path);
  console.log('ref        :', wf.ref, '(expect refs/tags/' + v.version + ')');
  console.log('digest ok  :', distSha === payload.subject[0].digest.sha512);
})();
" <VERSION>
```

All four must hold: repository `https://github.com/nrwl/nx`, workflow
`.github/workflows/publish.yml`, ref `refs/tags/<version>`, digest match.
`npm audit signatures` is a useful cross-check.

If verification passes, re-run pinned to the **exact version** (not the tag),
scoping the skip flag to that single command — never export it, never persist it:

```bash
NX_SKIP_PROVENANCE_CHECK=true npx nx@<VERSION> migrate <VERSION> 2>&1
```

If verification **fails**, stop and report. Do not migrate.

Be aware that `npx nx@<VERSION>` still resolves `nx` to the workspace's local
`node_modules`, so the installed (buggy) version runs the check no matter what
you pin on the command line — the error text keeps saying `nx@latest` even
when you pinned an exact version. There is consequently no flag-free path out
of this from an affected install, and the override above is the only way
through. If it is unavailable (e.g. denied by a permission rule), stop and
report. Do not patch `node_modules`, export the variable, or write it to a
file to get around it.

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

#### AI-prompt migrations

Nx 23.1+ can defer some migrations to prompts written into
`tools/ai-migrations/`, reported at the end of the run as
"N prompt migrations deferred". Read and apply each one **in the order listed**.

These prompts state their own preconditions. Honour them: several are no-ops in
a given workspace, and the correct outcome is then to change nothing. Example —
the `migrate-ban-types-rule` prompt says to first confirm some ESLint flat
config actually references `@typescript-eslint/ban-types`; if the only matches
are inside `migrations.json` itself (its own description text), the instruction
is to make no changes. Do not invent work to look productive.

If a prompt asks you to run a target that does not exist in the workspace (e.g.
`nx run-many -t typecheck` where no project defines `typecheck`), say so
plainly and rely on the normal validation step instead of fabricating a result.

**Careful when cleaning up:** `tools/ai-migrations/` may already contain
*tracked* files from earlier migrations. Removing the whole directory deletes
them too. Check first, and restore anything you did not create:

```bash
git status --short          # look for unexpected deletions (D)
git checkout -- <path>      # restore a tracked file removed by accident
```

### 6. Sync sub-package dependencies

After `npm install`, check if any dependency versions in sub-packages (`libs/*/package.json`, `apps/*/package.json`) are out of sync with the root `package.json`. In particular, `peerDependencies` and `devDependencies` in library packages must stay in sync with the versions in the root.

```bash
# Find all sub-package.json files
find libs apps -name "package.json" -not -path "*/node_modules/*" 2>/dev/null
```

For each sub-package found:
- Read the file
- Compare its dependency versions against the root `package.json` using these mappings:
  - Sub-package `peerDependencies` → root `dependencies` (libraries declare as peer what the app installs as prod dep)
  - Sub-package `devDependencies` → root `devDependencies`
  - Sub-package `dependencies` → root `dependencies`
- Update any version that was bumped by the migration (e.g. `typescript`, `tslib`, `@angular/*`, `@nx/*`, `@angular/cdk`, `@angular/material`)
- After edits, run `npm install` again to update `package-lock.json`

### 7. Clean up migrations.json

Check whether it is tracked before deleting — some repos have an old
`migrations.json` committed from an earlier, unfinished migration:

```bash
git ls-files --error-unmatch migrations.json 2>/dev/null && echo TRACKED || echo untracked
rm -f migrations.json
```

If it was **untracked**, this is just cleanup of the file nx generated.

If it was **tracked**, deleting it is a real change to the repo. That is
usually correct (a leftover from a previous run that was never cleaned up),
but it must be called out explicitly in the commit message and PR body rather
than sliding in silently among the dependency updates.

### 8. Validate: lint → test → build

Run the project's validation commands. Check the repo's CLAUDE.md first.

**Verify the commands exist before trusting them.** A repo's CLAUDE.md can name
scripts or projects that are no longer present, and a command that fails because
the target does not exist is not a validation failure — but it must not be
reported as a pass either. Confirm what is actually available:

```bash
npx nx show projects                          # real project names
node -e "console.log(Object.keys(require('./package.json').scripts||{}).join('\n'))"
```

If a documented command references a missing project or script, run the
equivalent that does exist, and note the discrepancy in the PR body.

Default:

```bash
npx nx run-many -t lint 2>&1
npx nx run-many -t test 2>&1
npx nx run-many -t build 2>&1
```

For each failure:
- Read the error output carefully
- Fix the root cause (updated API, renamed config option, removed feature)
- Re-run the failing command to confirm the fix
- Do not suppress errors with `// @ts-ignore` or similar unless absolutely unavoidable

Repeat until all three pass with exit code 0.

### 9. Run E2E tests (unless --skip-e2e was passed)

```bash
npm run e2e 2>&1
```

Fix any E2E failures the same way as above.

### 9b. Bump the package version

In repos whose CI keys artifact names on the package version (Docker tags via
`nx affected`, published images), a patch bump is required on **every** PR
branch, not just releases — otherwise the security scan or publish step cannot
find the expected versioned artifact.

```bash
# bump patch in package.json, then ALWAYS resync the lockfile
npm install 2>&1 | tail -3
```

Never commit a `package.json` change without the updated `package-lock.json`.
Re-run validation after this, since files changed since the last green run.

### 10. Commit

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
- Sync sub-package dependency versions (if any)
- Fix lint/test/build issues (list specific fixes if any)
```

### 11. Push and open PR

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
