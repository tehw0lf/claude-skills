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

Those are all the checks there are, so a changed script has to be exercised by hand against a real target (the calls are in `docs/commands.md`).

To try an unmerged change in a live session, load the working copy for that session only: `claude --plugin-dir plugins/<name>`.

Installed copies come from the marketplace (`/plugin marketplace add tehw0lf/claude-skills`, then `claude plugin install <name>@tehw0lf-claude-skills --scope user`). Add it from GitHub, never as a local directory: a directory marketplace installs whatever the working tree holds (any branch, uncommitted edits), so unmerged changes would reach the installed copies. The GitHub marketplace follows `main`, and an installed plugin is replaced only when its `version` changed, so a pushed branch or an unbumped change never reaches them. After a merge: `claude plugin marketplace update tehw0lf-claude-skills`, then `claude plugin update <name>@tehw0lf-claude-skills` and a restart.

## Layout

```
README.md                                  # front page: what it is, install, fully automated use; no plugin list
docs/                                      # coupling.md and commands.md, read at the point named in CLAUDE.md; not shipped by the marketplace
.claude-plugin/marketplace.json            # lists every plugin, source: ./plugins/<name>
scripts/                                   # repository checks and tools, not shipped by the marketplace
plugins/<name>/.claude-plugin/plugin.json  # name, version, description
plugins/<name>/skills/<name>/SKILL.md      # the skill; scripts/ and extra .md files sit beside it (work-issue: resume.md, stopping.md and the worker-*.md files, see below)
plugins/<name>/agents/<agent>.md           # optional: runs the skill unsupervised, one repository per agent (pr-review: the agent, its layout file and `scripts/dependency-diff.py` are the whole plugin)
                                           # a plugin may hold several skills and agents (work-issue: plan-issue + issue-planner, work-issue + issue-worker, orchestrate-issue)
plugins/pr-review/verdict-layout.md        # the verdict layout, read by pr-reviewer before it writes the verdict (beside agents/ because agents/**/*.md is checked as agents)
plugins/<name>/hooks/hooks.json            # optional: hooks the plugin adds while it is enabled (inbox: the SessionStart summary)
```

Three places describe each plugin and have to agree: the entry in `marketplace.json`, `plugin.json`, and the `description` in the `SKILL.md` front matter. The first two are the catalogue text; the third is what decides whether a session invokes the skill, so it carries the trigger phrases (including the German ones). `pr-review` has no skill: its third place is the `description` in the front matter of `agents/pr-reviewer.md`, which is what a session reads when it picks an agent type.

Adding a plugin means a new `plugins/<name>/` tree **and** an entry in `marketplace.json`.

## Versioning

Every change to a plugin bumps `version` in that plugin's `plugin.json` in the same PR (see the history of `setup-workflows`: 2.0.0 → 2.0.1 → 2.0.2). This stands in for the patch-bump rule that applies to repos with a `package.json`. Only the touched plugin is bumped.

## How the pieces are meant to divide

The split was established in the "reduce skills and agents, move mechanics into scripts" refactor and later changes follow it:

- **Scripts hold the mechanics.** Anything deterministic — parsing SARIF, classifying branches, fetching the orchestrator's inputs — lives in `scripts/` and prints a stable tab- or line-oriented format. A script says what it could not do instead of guessing: the fetch, validation and provenance scripts exit non-zero, while `classify.sh` prints a `SKIPPED` line for that repository and carries on. Each script opens with a usage comment or docstring. A script that is called by its path anywhere (a skill's `.md` files, an agent, a hook or the commands in this file and in `docs/commands.md`; e.g. `scripts/<file>`, `<skill directory>/…/scripts/<file>`) is tracked as mode 100755 (`git add --chmod=+x`, or `git update-index --chmod=+x`) and opens with a shebang; a call through an interpreter (`python3`, `node`, `uv run`, `bash`) works with either mode. Nothing checks the mode, and a path-called script committed as 100644 fails only in a live run with `Permission denied` (exit 126), as `classify.sh` did until #33. `SKILL.md` relies on the output format, so change both together.
- **`SKILL.md` holds judgment and order.** Numbered steps — where the end of a step is not obvious it closes with a checkable "Done when …" — plus the decisions a script cannot make (what to ask the user, when to stop). Skills refer to their scripts as `scripts/<file>` relative to the skill directory.
- **Agents add only what running unsupervised needs.** `issue-worker` says to follow the preloaded skill step by step, `nx-migrator` and `branch-scanner` say to invoke their skill and follow it, and each defines what the skill leaves to a human: scope limits, the shape of the final report, and the step a person would otherwise decide. For `branch-scanner` that is the confirmation prompt, replaced by a report mode and a prune mode; for `nx-migrator` and `issue-worker` it is everything after the open PR — the independent review and the merge — and for `issue-worker` also the skill's questions to the user, replaced by stopping with a report. An agent does not introduce or redefine skill rules. Where it repeats one, that is deliberate: it pins the rule down at the point where running unsupervised could tempt a session past it (in `branch-scanner`, that NEEDS REVIEW, KEPT and INELIGIBLE branches are never deleted even in prune mode). Keep those repetitions.
- **`issue-planner` and `issue-worker` preload their skill and list their tools.** `skills: plan-issue` / `skills: work-issue` injects the skill text at start (with its base directory, so `<skill directory>/scripts/…` still resolves), and `tools:` leaves out `Skill`. Reason: with `Skill` available every request carries the listing of every installed skill and the names of every deferred tool, and invoking the skill adds its text on top; with the skill preloaded both are dropped from each request. Cost: a preloaded skill does not apply the `allowed-tools` grant of its `SKILL.md` (`Bash`, `Read`, `Write`, `TodoWrite` for `plan-issue`; `Bash`, `Read`, `Edit`, `Write`, `TodoWrite` for `work-issue`), and a plugin agent cannot set `permissionMode`. An interactive run therefore prompts for these tools, and a run under `--permission-prompts none` denies what no rule allows, so the list under "Permissions" in `scheduling.md` decides what a scheduled run may do; it was last verified for the invoking agents, and `scheduling.md` records what was denied since. The lists are the tools the agents called in their transcripts plus `Grep` and `Glob`, which were not called (`ToolSearch` stays because `Monitor`, `SendMessage` and `TaskStop` are deferred tools); a tool the skill starts to need goes into `tools:` in the same PR, otherwise an unsupervised run stops without it.
- **Conditional sections sit in files beside `SKILL.md`, behind a hard pointer.** What applies only under a condition is read at that point instead of with every request: in `work-issue` the resumed PR (`resume.md`), the clean-up of a stop (`stopping.md`) and, for `issue-worker`, the resume rules, the stop marker and the merge (`worker-resume.md`, `worker-stop-marker.md`, `worker-merging.md`; they live in the skill directory because `agents/**/*.md` is checked as agents). The place where the section used to stand keeps its heading and says when to read the file, so rules that refer to the section by name still resolve; the text was moved without rewording, except references that crossed the file boundary. Rules that only exist in the moved text (the merge conditions, `merge-facts.py`) are read only after the review is clean, so a change to them is made in the file, not in the pointer. The same holds for `pr-reviewer`, which has no skill directory: its layout file sits in the plugin root and is reached by `${CLAUDE_PLUGIN_ROOT}`, which Claude Code substitutes in agent text but does not export to Bash. A file name that is called anywhere has to exist: renaming one means changing the pointers in `SKILL.md`, `issue-worker.md` and `pr-reviewer.md` too.
- **Text that applies only when particular files change sits in `docs/`, behind a hard pointer.** The cross-repository coupling and the calls to run a script by hand are not read by every session that touches the repository: `CLAUDE.md` keeps an index of the coupled files and says which file to read before changing them. Add a coupling to `docs/coupling.md` and, when it names a new file, to the index in the same PR.
- **`issue-planner` is the only agent that writes to an issue.** It runs `plan-issue` (Opus) and posts one comment, either the plan, the owner's questions with the label `needs-decision`, or, on an owner's answer that picks an option closing the issue, a close comment followed by `gh issue close`; it changes no code. `issue-worker` implements only a current plan and never spawns the planner (it needs its spawn depth for the reviewer): whoever starts the run spawns the planner, then the worker. `orchestrate-issue` is the skill that does this from the main session (never from a subagent, which would cost the worker its spawn depth): it selects explicit issues or those with the opt-in label `auto-work` (labeler must have write access), continues an own open PR (author `ACCOUNT`, body links the current plan comment; `RESUME` line) through validation, review and merge instead of stopping at it, reports one with a current stop marker as waiting for a person (except the reason `pending-check`, which it resumes once every required check passes), allows 1 issue per run unless `--max` is given, one worker per repository at a time, one replan, and tells the worker not to merge where the default branch requires no status check.
- **`pr-reviewer` is the exception: an agent without a skill.** A review has to come from a context that did not write the change, so there is nothing for a session to run inline. Installed from the plugin, the agent type is `pr-review:pr-reviewer`. The agent says how one review is done (read-only, first-hand, the three classes, the verdict file and how the verdict follows from the classes); the verdict layout itself sits in `verdict-layout.md`, which the agent reads by `${CLAUDE_PLUGIN_ROOT}/verdict-layout.md` before it writes the verdict, and the agent text keeps the lines the scripts read plus a flat fallback if the file cannot be read. The layout keeps the decision (heading, origin line, `Reviewed head`, `Verdict`, count line) always visible, opens the two deciding classes with each finding folded behind a one-line summary, and folds the rest into nested `<details>` sections, because a review with findings is too long to read in full on GitHub; the class names stay in the summaries, so rules that name the sections still match. `issue-worker` and `nx-migrator` both spawn it for every review round. When a review is required, which model a round runs on and the merge itself (the checks the base branch requires green, head still the reviewed one) stay with whoever spawns it.

Conventions to keep when editing or adding a skill. Only the first is found in every skill; the others are named with the skill that sets the example:

- **Stop-and-report beats guessing.** A skipped repository, an unverifiable provenance check or an ambiguous value ends in a report, not a best effort.
- **Live data over remembered lists.** `setup-workflows` reads inputs and permission scopes from `tehw0lf/workflows` at run time because a list frozen into the skill went stale and broke a real run. When a fetch fails, the script stops; it never falls back.
- **Rules carry their reason.** A hard rule states the mechanism that makes the shortcut fail, and `setup-workflows` also names the incident behind it (the `[a-z_]+` regex that dropped `e2e`, the six-scope list that produced `startup_failure` in `yaft-java`). Keep that when adding a rule: an unexplained prohibition gets argued away by the session reading it.
- **Destructive steps are gated by classification, not by the confirmation flag.** In `prune-branches`, `--yes` skips the prompt and nothing else; the agent's prune mode needs the word in its prompt.

## Cross-repository coupling

`docs/coupling.md` lists which files rely on each other's names, line formats, comment formats or contents, and what each side depends on. **Before you change any file named below, read `docs/coupling.md`.** Few of these links are checked (the `cmp` lines, `verify-nx-provenance.js`); a broken one otherwise shows up in a later run.

- `setup-workflows` and `tehw0lf/workflows`: the orchestrator inputs, `fetch-permissions.py`, `validate-caller.py`.
- `prune-branches` (`classify.sh --all`): `$CODING_ROOT` and the keep rules.
- `work-issue` and `pr-review`: `issue-worker.md` and the `worker-*.md` files beside the `work-issue` `SKILL.md` (`worker-merging.md` calls `merge-facts.py`, `worker-stop-marker.md` writes the stop marker), `pr-reviewer.md`; the line kinds of `issue-context.py` and the plan, questions, closed and stopped comment formats shared by `plan-issue`, `work-issue`, `issue-worker` and `issue-context.py`.
- `nx-migrate` and `pr-review`: `nx-migrator.md`.
- `orchestrate-issue`: `has-work.py`, `dispatch.py`, `run-context.py` and the units in `scheduling.md`, which rely on `issue-context.py` lines and on GitHub endpoints.
- `inbox`: `collect.py` and its SessionStart hook.
- Byte-identical copies in `nx-migrate` and `work-issue`: `check-install-config.py`, `merge-facts.py` (the `cmp` lines are in `docs/commands.md`).
- `repo-commands.py`: how `tehw0lf/workflows` `test-and-build.yml` composes commands, and steps 4 to 7 of the `work-issue` `SKILL.md`.
- `dependency-diff.py` and `pr-reviewer.md` (its dependency-file list and the `DEP` and `RESULT` lines), with the allow rule in `scheduling.md`.
- The verdict layout of `pr-reviewer.md` and `verdict-layout.md`: `merge-facts.py`, `scripts/review-rounds.py`.
- `verify-nx-provenance.js`: the pinned publisher.

## Working here

Classic branch protection is off on `main`, but an active repository ruleset applies to the default branch: no deletion, no non-fast-forward push, and the required status check "GitGuardian Security Checks" with the branch required to be up to date before merging. The check comes from an installed GitHub app, not from a workflow in this repository. It matters because `orchestrate-issue` and `issue-worker` merge only where the default branch requires at least one status check; without it an orchestrated worker would stop before every merge here. The up-to-date rule leaves every open PR behind `main` after each merge; `issue-worker` then merges `main` into the branch (`update-branch`, never a rebase) and reviews the new head before it merges. Read the current rules with `gh api repos/tehw0lf/claude-skills/rules/branches/main` instead of trusting this text.

The history is feature branches merged by PR with conventional-commit subjects scoped to the plugin (`fix(setup-workflows): …`); keep to that.
