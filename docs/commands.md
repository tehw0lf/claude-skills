# Calling the scripts by hand

The checks in `CLAUDE.md` are all there are, so a changed script has to be exercised by hand against a real target:

```bash
S=plugins/setup-workflows/skills/setup-workflows/scripts
$S/fetch-inputs.sh && $S/fetch-permissions.py    # write /tmp/valid_inputs.txt (and /tmp/btp.yml), /tmp/required_permissions.txt; the second needs gh auth
uv run $S/validate-caller.py <repo-with-build.yml>
python3 $S/check-scripts.py <repo-with-package.json>

bash plugins/prune-branches/skills/prune-branches/scripts/classify.sh [<repo>...]     # no argument: current repository, or all under $CODING_ROOT outside any repository; deletes nothing, but runs git fetch --prune and writes probe commit objects
python3 plugins/trivy-fix/skills/trivy-fix/scripts/parse-sarif.py <file.sarif>
python3 plugins/inbox/skills/inbox/scripts/collect.py [--owner <login> | --repo <owner/name> | --here]   # read-only on GitHub (needs gh auth); only the default run rewrites the inbox cache (the default owner, plus its organisations when that is the login), --owner, --repo and --here write nothing (--here exits 2 outside a GitHub checkout); --summary prints the hook JSON from the default owner's cache
python3 plugins/work-issue/skills/work-issue/scripts/issue-context.py [--comments] <n | 'owner/repo#n'>  # read-only (needs gh auth); run it inside a checkout, the MANIFEST and WORKTREE lines describe the current directory; --comments prints only the comments of trusted accounts
python3 plugins/work-issue/skills/orchestrate-issue/scripts/run-context.py [--owner <login>]... [owner/repo | 'owner/repo#n']...  # read-only (needs gh auth); prints repositories, required checks, local checkouts and the selected issues; `--owner` (account or organisation) reads every repository with an open labelled issue, found by one issue search
plugins/work-issue/skills/orchestrate-issue/scripts/has-work.py [--all] [--label <name>] [--owner <login>]... [<owner/repo>]...   # read-only (needs gh auth); the `ExecCondition=` check of the systemd unit: exit 0 a hit, 1 none, 255 the check failed; prints WORK or NONE; --all prints one WORK or ERROR line per repository (exit 2: only ERROR lines)
plugins/work-issue/skills/orchestrate-issue/scripts/dispatch.py --dry-run [--parallel <n>] [--owner <login>]... [<owner/repo>]...   # runs has-work.py --all and lists the running work-issue@ instances; without --dry-run it starts instances with systemctl --user start
node plugins/nx-migrate/skills/nx-migrate/scripts/verify-nx-provenance.js [<version>]
node plugins/nx-migrate/skills/nx-migrate/scripts/check-target.js <installed version | -> <target version>   # writes nothing itself (the npm it starts writes its log and cache); reads the registry through npm view (needs network) and validates the target as strict semver before; prints TARGET/INSTALLED/ORDER/PRERELEASE/LATEST lines, exit 1 with a message on a bad or unpublished target
bash plugins/nx-migrate/skills/nx-migrate/scripts/project-packages.sh   # run in an Nx workspace root (Nx installed); writes nothing itself (the Nx it starts stores its workspace data under .nx/ and may start its daemon); `--all` lists projects without a package.json too; prints one project root per line (sorted, unique) for every Nx project other than the workspace root with a regular package.json; exit 1 with a message when Nx cannot compute the graph
bash plugins/nx-migrate/skills/nx-migrate/scripts/bump-subpackages.sh [--base <rev>]   # run in an Nx workspace root with a clean tree plus changes (changed = different from <rev>, default HEAD; exit 1 when <rev> is no commit): bumps changed project packages (the Nx project graph, via project-packages.sh) and runs npm install (the pin check is plugins/nx-migrate/skills/nx-migrate/scripts/check-pins.js, node with npm's bundled semver and @npmcli/map-workspaces)
python3 plugins/nx-migrate/skills/nx-migrate/scripts/check-install-config.py <repo>   # writes nothing itself (the npm it starts may create its cache directory); reads package.json, .npmrc, .yarnrc.yml and pnpm-workspace.yaml in <repo> and checks whether .npm-extension.mjs/.cjs exist; runs npm config get (npm config ls only for a value that is not false; no log, no update check); line kinds NPMRC/CONFIG/OVERRIDE/EXTENSION/YAML/SKIPPED, the docstring has the full list
python3 plugins/work-issue/skills/work-issue/scripts/repo-commands.py <checkout root>   # reads files only, runs no tool; prints the repository's commands with their source (CALLER/CI/SCRIPT/MAKE/PROJECT/DOC/FALLBACK/…)
python3 plugins/work-issue/skills/work-issue/scripts/check-lock-overrides.py <directory>   # reads files only, runs no tool; uv, Cargo, Go and Composer settings that hide a conflict (work-issue only, not shared with nx-migrate)
cmp plugins/nx-migrate/skills/nx-migrate/scripts/check-install-config.py plugins/work-issue/skills/work-issue/scripts/check-install-config.py   # the two copies must stay identical
python3 plugins/work-issue/skills/work-issue/scripts/merge-facts.py <owner/repo> <pr>   # read-only (needs gh auth); lists the latest verdict, the head and every change of the PR's base branch at or after the verdict, plus the newest base change of the whole timeline (`LASTBASE`)
python3 plugins/pr-review/scripts/dependency-diff.py <base ref> <head ref>   # reads git only; prints `DEP` lines and a `RESULT` of none, version-only or changed for the dependency files of the diff <base>...<head>
cmp plugins/nx-migrate/skills/nx-migrate/scripts/merge-facts.py plugins/work-issue/skills/work-issue/scripts/merge-facts.py   # the two copies must stay identical
python3 scripts/review-rounds.py --since YYYY-MM-DD [--until YYYY-MM-DD] [--owner <login>]   # read-only (needs gh auth); counts review rounds and findings per round of the merged PRs in the range from their verdict comments
```

`check-frontmatter.py`, `fetch-permissions.py` and `validate-caller.py` carry PEP 723 inline metadata (`pyyaml`) and run through `uv run --script`; the other Python scripts are stdlib-only.
