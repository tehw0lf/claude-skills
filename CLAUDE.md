# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A Claude Code plugin marketplace (`tehw0lf-claude-skills`), not an application. The "code" is mostly prompt text: skills and agents that other Claude sessions execute, plus small helper scripts those skills call. There is no build, no test suite, no CI and no package manifest.

## Commands

```bash
claude plugin validate .                    # the marketplace manifest
claude plugin validate plugins/<name>       # one plugin manifest

bash -n plugins/prune-branches/skills/prune-branches/scripts/classify.sh
python3 -m py_compile <script.py>           # syntax only; __pycache__ is gitignored
```

There is nothing else to run before a commit, so a changed script has to be exercised by hand against a real target:

```bash
S=plugins/setup-workflows/skills/setup-workflows/scripts
$S/fetch-inputs.sh && $S/fetch-permissions.py    # need gh auth; write /tmp/valid_inputs.txt, /tmp/required_permissions.txt
uv run $S/validate-caller.py <repo-with-build.yml>
python3 $S/check-scripts.py <repo-with-package.json>

bash plugins/prune-branches/skills/prune-branches/scripts/classify.sh <repo>     # read-only, but runs git fetch --prune
python3 plugins/trivy-fix/skills/trivy-fix/scripts/parse-sarif.py <file.sarif>
node plugins/nx-migrate/skills/nx-migrate/scripts/verify-nx-provenance.js [<version>]
```

`fetch-permissions.py` and `validate-caller.py` carry PEP 723 inline metadata (`pyyaml`) and run through `uv run --script`; the other Python scripts are stdlib-only.

To try a change in a live session, install from the marketplace (`/plugin marketplace add tehw0lf/claude-skills`, then `claude plugin install <name>@tehw0lf-claude-skills --scope user`) and refresh with `claude plugin marketplace update tehw0lf-claude-skills` after pushing.

## Layout

```
.claude-plugin/marketplace.json            # lists every plugin, source: ./plugins/<name>
plugins/<name>/.claude-plugin/plugin.json  # name, version, description
plugins/<name>/skills/<name>/SKILL.md      # the skill; scripts/ and extra .md files sit beside it
plugins/<name>/agents/<agent>.md           # optional: unsupervised wrapper around the skill
```

Three places describe each plugin and have to agree: the entry in `marketplace.json`, `plugin.json`, and the `description` in the `SKILL.md` front matter. The first two are the catalogue text; the third is what decides whether a session invokes the skill, so it carries the trigger phrases (including the German ones).

Adding a plugin means a new `plugins/<name>/` tree **and** an entry in `marketplace.json`.

## Versioning

Every change to a plugin bumps `version` in that plugin's `plugin.json` in the same PR (see the history of `setup-workflows`: 2.0.0 → 2.0.1 → 2.0.2). This stands in for the patch-bump rule that applies to repos with a `package.json`. Only the touched plugin is bumped.

## How the pieces are meant to divide

The split was established in the "reduce skills and agents, move mechanics into scripts" refactor and later changes follow it:

- **Scripts hold the mechanics.** Anything deterministic — parsing SARIF, classifying branches, fetching the orchestrator's inputs — lives in `scripts/`, prints a stable tab- or line-oriented format, and exits non-zero rather than degrading. Each script opens with a usage comment or docstring that states its output format; `SKILL.md` relies on that format, so change both together.
- **`SKILL.md` holds judgment and order.** Numbered steps, each ending in a checkable "done when", plus the decisions a script cannot make (what to ask the user, when to stop). Skills refer to their scripts as `scripts/<file>` relative to the skill directory.
- **Agents add only what running unsupervised needs.** `nx-migrator` and `branch-scanner` each say "invoke the skill and follow it" and then define scope limits, what replaces the confirmation prompt, and the shape of the final report. Skill rules are not restated in the agent file; do not duplicate them there.

Conventions that recur across all four skills and should be kept when editing or adding one:

- **Live data over remembered lists.** `setup-workflows` reads inputs and permission scopes from `tehw0lf/workflows` at run time because a list frozen into the skill went stale and broke a real run. When a fetch fails, the script stops; it never falls back.
- **Stop-and-report beats guessing.** A skipped repository, an unverifiable provenance check or an ambiguous value ends in a report, not a best effort.
- **Rules carry their reason.** Most hard rules name the incident behind them (the `[a-z_]+` regex that dropped `e2e`, the six-scope list that produced `startup_failure` in `yaft-java`). Keep that when adding a rule: an unexplained prohibition gets argued away by the session reading it.
- **Destructive steps are gated by classification, not by the confirmation flag.** In `prune-branches`, `--yes` skips the prompt and nothing else; the agent's prune mode needs the word in its prompt.

## Cross-repository coupling

- `setup-workflows` is bound to `tehw0lf/workflows` (checked out at `../../workflows`): the orchestrator path `.github/workflows/build-test-publish.yml`, its `workflow_call.inputs` block, and the fact that it only calls other workflows by `./` reference. `fetch-permissions.py` exits on any other kind of reference on purpose — extend the script when that changes, do not skip the reference. `validate-caller.py` expects the caller at `.github/workflows/build.yml` and finds the job by its `uses:`, not by name.
- `prune-branches --all` scans `$CODING_ROOT` (default `~/Nextcloud/Coding`) to depth 3 and reads keep rules from `$XDG_CONFIG_HOME/prune-branches-keep` and `.git/prune-branches-keep`.
- `verify-nx-provenance.js` pins the expected publisher (`nrwl/nx`, `.github/workflows/publish.yml`, tag ref) — an upstream release-process change shows up there as a failed check.

## Working here

`main` is not branch-protected, but the history is feature branches merged by PR with conventional-commit subjects scoped to the plugin (`fix(setup-workflows): …`); keep to that.
