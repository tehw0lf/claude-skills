# Provenance or supply-chain check failed

Never disable the check as a reflex — not with an env var, not with a flag. Verify first:

```bash
node scripts/verify-nx-provenance.js [<version>]   # exits 0 only if all four checks hold
```

`npm audit signatures` is a useful cross-check.

- **Not verified** → stop and report. Do not migrate.
- **Verified** → use the narrowest override, scoped to one command, never exported, never written to a file. Write it as `env VAR=value npx ...`, not `VAR=value npx ...`: a leading assignment breaks permission-rule matching (an allow-rule for `npx nx:*` stops matching).
- **Override denied** by a permission rule → stop and report. Do not patch `node_modules`, export the variable, or write it to a file.

`npx nx@<VERSION>` resolves to the workspace's local `node_modules`, so the *installed* version runs, not the pinned one — relevant whenever the installed version is what misbehaves.
