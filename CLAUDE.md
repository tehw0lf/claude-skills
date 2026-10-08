# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A Claude Code plugin marketplace (`tehw0lf-claude-skills`), not an application. The "code" is mostly prompt text: skills and agents that other Claude sessions execute, plus small helper scripts those skills call. There is no build, no test suite, no CI workflow in the repository and no package manifest.

## Commands

```bash
claude plugin validate .                    # the marketplace manifest
claude plugin validate plugins/<name>       # one plugin manifest
uv run scripts/check-frontmatter.py         # front matter of every agent and SKILL.md; claude plugin validate reads plugin.json only

bash -n plugins/prune-branches/skills/prune-branches/scripts/classify.sh
python3 -m py_compile <script.py>           # syntax only; __pycache__ is gitignored
git ls-files -s -- 'scripts/*' '*/scripts/*'   # modes: every script called by its path must be 100755
```

Those are all the checks there are, so a changed script has to be exercised by hand against a real target:

```bash
S=plugins/setup-workflows/skills/setup-workflows/scripts
$S/fetch-inputs.sh && $S/fetch-permissions.py    # write /tmp/valid_inputs.txt (and /tmp/btp.yml), /tmp/required_permissions.txt; the second needs gh auth
uv run $S/validate-caller.py <repo-with-build.yml>
python3 $S/check-scripts.py <repo-with-package.json>

bash plugins/prune-branches/skills/prune-branches/scripts/classify.sh <repo>     # deletes nothing, but runs git fetch --prune and writes probe commit objects
python3 plugins/trivy-fix/skills/trivy-fix/scripts/parse-sarif.py <file.sarif>
python3 plugins/inbox/skills/inbox/scripts/collect.py [--owner <login>]            # read-only on GitHub (needs gh auth), rewrites that owner's inbox cache; --summary prints the hook JSON from the default owner's cache
python3 plugins/work-issue/skills/work-issue/scripts/issue-context.py <n | 'owner/repo#n'>  # read-only (needs gh auth); run it inside a checkout, the MANIFEST and WORKTREE lines describe the current directory
python3 plugins/work-issue/skills/orchestrate-issue/scripts/run-context.py [owner/repo | 'owner/repo#n']...  # read-only (needs gh auth); prints repositories, required checks, local checkouts and the selected issues
node plugins/nx-migrate/skills/nx-migrate/scripts/verify-nx-provenance.js [<version>]
```

`check-frontmatter.py`, `fetch-permissions.py` and `validate-caller.py` carry PEP 723 inline metadata (`pyyaml`) and run through `uv run --script`; the other Python scripts are stdlib-only.

To try an unmerged change in a live session, load the working copy for that session only: `claude --plugin-dir plugins/<name>`.

Installed copies come from the marketplace (`/plugin marketplace add tehw0lf/claude-skills`, then `claude plugin install <name>@tehw0lf-claude-skills --scope user`). Add it from GitHub, never as a local directory: a directory marketplace installs whatever the working tree holds (any branch, uncommitted edits), so unmerged changes would reach the installed copies. The GitHub marketplace follows `main`, and an installed plugin is replaced only when its `version` changed, so a pushed branch or an unbumped change never reaches them. After a merge: `claude plugin marketplace update tehw0lf-claude-skills`, then `claude plugin update <name>@tehw0lf-claude-skills` and a restart.

## Layout

```
.claude-plugin/marketplace.json            # lists every plugin, source: ./plugins/<name>
scripts/                                   # repository checks, not shipped by the marketplace
plugins/<name>/.claude-plugin/plugin.json  # name, version, description
plugins/<name>/skills/<name>/SKILL.md      # the skill; scripts/ and extra .md files sit beside it
plugins/<name>/agents/<agent>.md           # optional: runs the skill unsupervised, one repository per agent (pr-review: the agent is the whole plugin)
                                           # a plugin may hold several skills and agents (work-issue: plan-issue + issue-planner, work-issue + issue-worker, orchestrate-issue)
plugins/<name>/hooks/hooks.json            # optional: hooks the plugin adds while it is enabled (inbox: the SessionStart summary)
```

Three places describe each plugin and have to agree: the entry in `marketplace.json`, `plugin.json`, and the `description` in the `SKILL.md` front matter. The first two are the catalogue text; the third is what decides whether a session invokes the skill, so it carries the trigger phrases (including the German ones). `pr-review` has no skill: its third place is the `description` in the front matter of `agents/pr-reviewer.md`, which is what a session reads when it picks an agent type.

Adding a plugin means a new `plugins/<name>/` tree **and** an entry in `marketplace.json`.

## Versioning

Every change to a plugin bumps `version` in that plugin's `plugin.json` in the same PR (see the history of `setup-workflows`: 2.0.0 → 2.0.1 → 2.0.2). This stands in for the patch-bump rule that applies to repos with a `package.json`. Only the touched plugin is bumped.

## How the pieces are meant to divide

The split was established in the "reduce skills and agents, move mechanics into scripts" refactor and later changes follow it:

- **Scripts hold the mechanics.** Anything deterministic — parsing SARIF, classifying branches, fetching the orchestrator's inputs — lives in `scripts/` and prints a stable tab- or line-oriented format. A script says what it could not do instead of guessing: the fetch, validation and provenance scripts exit non-zero, while `classify.sh` prints a `SKIPPED` line for that repository and carries on. Each script opens with a usage comment or docstring. A script that is called by its path anywhere (a skill's `.md` files, an agent, a hook or the commands in this file; e.g. `scripts/<file>`, `<skill directory>/…/scripts/<file>`) is tracked as mode 100755 (`git add --chmod=+x`, or `git update-index --chmod=+x`) and opens with a shebang; a call through an interpreter (`python3`, `node`, `uv run`, `bash`) works with either mode. Nothing checks the mode, and a path-called script committed as 100644 fails only in a live run with `Permission denied` (exit 126), as `classify.sh` did until #33. `SKILL.md` relies on the output format, so change both together.
- **`SKILL.md` holds judgment and order.** Numbered steps — where the end of a step is not obvious it closes with a checkable "Done when …" — plus the decisions a script cannot make (what to ask the user, when to stop). Skills refer to their scripts as `scripts/<file>` relative to the skill directory.
- **Agents add only what running unsupervised needs.** `nx-migrator`, `branch-scanner` and `issue-worker` each say "invoke the skill and follow it" and then define what the skill leaves to a human: scope limits, the shape of the final report, and the step a person would otherwise decide. For `branch-scanner` that is the confirmation prompt, replaced by a report mode and a prune mode; for `nx-migrator` and `issue-worker` it is everything after the open PR — the independent review and the merge — and for `issue-worker` also the skill's questions to the user, replaced by stopping with a report. An agent does not introduce or redefine skill rules. Where it repeats one, that is deliberate: it pins the rule down at the point where running unsupervised could tempt a session past it (in `branch-scanner`, that NEEDS REVIEW, KEPT and INELIGIBLE branches are never deleted even in prune mode). Keep those repetitions.
- **`issue-planner` is the only agent that writes to an issue.** It runs `plan-issue` (Opus) and posts one comment, either the plan or the owner's questions with the label `needs-decision`; it changes no code. `issue-worker` implements only a current plan and never spawns the planner (it needs its spawn depth for the reviewer): whoever starts the run spawns the planner, then the worker. `orchestrate-issue` is the skill that does this from the main session (never from a subagent, which would cost the worker its spawn depth): it selects explicit issues or those with the opt-in label `auto-work` (labeler must have write access), allows 3 issues per run unless `--max` is given, one worker per repository at a time, one replan, and tells the worker not to merge where the default branch requires no status check.
- **`pr-reviewer` is the exception: an agent without a skill.** A review has to come from a context that did not write the change, so there is nothing for a session to run inline. Installed from the plugin, the agent type is `pr-review:pr-reviewer`. The agent says how one review is done (read-only, first-hand, the three classes, the verdict file, its layout and how the verdict follows from the classes). The layout keeps the decision (heading, `Reviewed head`, `Verdict`, count line) always visible, opens the two deciding classes with each finding folded behind a one-line summary, and folds the rest into nested `<details>` sections, because a review with findings is too long to read in full on GitHub; the class names stay in the summaries, so rules that name the sections still match. `nx-migrator` spawns a generic reviewer with its own briefing and keeps the flat layout until it uses this agent (#19). When a review is required, which model a round runs on and the merge itself (checks green, head still the reviewed one) stay with whoever spawns it.

Conventions to keep when editing or adding a skill. Only the first is found in every skill; the others are named with the skill that sets the example:

- **Stop-and-report beats guessing.** A skipped repository, an unverifiable provenance check or an ambiguous value ends in a report, not a best effort.
- **Live data over remembered lists.** `setup-workflows` reads inputs and permission scopes from `tehw0lf/workflows` at run time because a list frozen into the skill went stale and broke a real run. When a fetch fails, the script stops; it never falls back.
- **Rules carry their reason.** A hard rule states the mechanism that makes the shortcut fail, and `setup-workflows` also names the incident behind it (the `[a-z_]+` regex that dropped `e2e`, the six-scope list that produced `startup_failure` in `yaft-java`). Keep that when adding a rule: an unexplained prohibition gets argued away by the session reading it.
- **Destructive steps are gated by classification, not by the confirmation flag.** In `prune-branches`, `--yes` skips the prompt and nothing else; the agent's prune mode needs the word in its prompt.

## Cross-repository coupling

- `setup-workflows` is bound to `tehw0lf/workflows` (checked out at `../../workflows`): the orchestrator path `.github/workflows/build-test-publish.yml`, its `workflow_call.inputs` block, and the fact that it only calls other workflows by `./` reference. `fetch-permissions.py` exits on any other kind of reference on purpose — extend the script when that changes, do not skip the reference. `validate-caller.py` expects the caller at `.github/workflows/build.yml` and finds the job by its `uses:`, not by name.
- `prune-branches all` (`classify.sh --all`) scans `$CODING_ROOT` (default `~/Nextcloud/Coding`) for repositories up to two directory levels below it and reads keep rules from `$XDG_CONFIG_HOME/prune-branches-keep` and `.git/prune-branches-keep`.
- `work-issue` depends on `pr-review` at run time: `issue-worker` spawns `pr-review:pr-reviewer` for every review round and does not merge when that agent type is missing. The skill alone (up to the open PR) needs no other plugin. `issue-context.py` reads one issue through `gh api graphql` and the checkout it is run in; `SKILL.md` relies on its line kinds (`ISSUE`, `CHECKOUT`, `ACCOUNT`, `PR`, `PLAN`, `QUESTIONS`, `BRANCH`, `WORKTREE`, …) and on the names of its `# SKIPPED` sources. Planner, worker and script share one comment format: the first line `<!-- work-issue:plan base=<sha> -->` or `<!-- work-issue:questions -->`, written by the `ACCOUNT` login; the script counts only such comments of that login as plan or questions, and only owner, member or collaborator comments as answers (anyone can comment on an issue, so a forged plan must not steer the worker). Change the format in `plan-issue`, `work-issue` and `issue-context.py` together.
- `orchestrate-issue` (`run-context.py`) depends on `$CODING_ROOT` (same scan as `classify.sh --all`), `gh repo clone`, the line kinds of `issue-context.py` and `run-context.py`, the GraphQL query `issue.userContentEdits` (the edit check: any edit after the label by a non-writer makes the issue untrusted) and the endpoints `repos/<o>/<r>/rules/branches/<default>`, `repos/<o>/<r>/branches/<default>` (classic protection), `repos/<o>/<r>/issues/<n>/events` and `repos/<o>/<r>/collaborators/<login>/permission`.
- `inbox` (`collect.py`) reads every non-archived, non-fork repository of the owner through `gh`, keeps one cache per owner plus `default-owner`, `refresh.lock` and `refresh.error` in `$XDG_CACHE_HOME/claude-inbox/` and reads ignore rules from `$XDG_CONFIG_HOME/inbox-ignore`. Its SessionStart hook only ever reads the default owner's cache and starts a detached `--background` refresh when it is stale, so a session start never waits for GitHub; the lock keeps refreshes from overlapping, and a failed background refresh is shown by the next summary; the hook prints JSON, and its line format is not parsed anywhere. Step 4 hands a picked `ISSUE` item to `work-issue:work-issue` when the session lists that skill and falls back to working it by hand otherwise, so the coupling is optional.
- `verify-nx-provenance.js` pins the expected publisher (`nrwl/nx`, `.github/workflows/publish.yml`, tag ref) — an upstream release-process change shows up there as a failed check.

## Working here

`main` is not branch-protected, but the history is feature branches merged by PR with conventional-commit subjects scoped to the plugin (`fix(setup-workflows): …`); keep to that.
