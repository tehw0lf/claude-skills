# claude-skills

A Claude Code plugin marketplace (`tehw0lf-claude-skills`) of skills and agents.

## Install

```
claude plugin marketplace add tehw0lf/claude-skills
claude plugin install <name>@tehw0lf-claude-skills --scope user
```

Add the marketplace from GitHub, not as a local directory: a directory marketplace installs whatever the working tree holds, including unmerged and uncommitted changes.

## Fully automated use

`work-issue` (orchestrator, planner and worker) and `nx-migrate` (`nx-migrator`) can run without a person up to and including the merge of a pull request: the worker merges once an independent review is clean and the required checks are green. The plugins are MIT licensed and used at your own risk. Whether a run may merge unattended, and what stops it from doing so, is yours to decide and to set up. Three gates exist, alone or combined:

- **An instruction in a `CLAUDE.md`** (for example "never merge"). The model follows it; Claude Code does not enforce it. Which `CLAUDE.md` files a scheduled run loads is described in `scheduling.md`, under "What the run reads and leaves behind".
- **A Claude Code permission rule** (`deny` or `ask` on `Bash(gh pr merge *)`). Claude Code evaluates it, but it matches the text of the command, not what the command does: `gh pr --repo <owner>/<repo> merge <n>` does not start with `gh pr merge`, a REST merge through `gh api` is not covered while `Bash(gh api *)` is allowed, and rules that run arbitrary code (`python3`, `node`, `npx`, `uv`) can call a merge as well. A mod installed in Claude Code can approve a call that an `ask` rule holds. It can also approve a call that a `deny` rule refuses, except that by default it cannot on a machine with managed settings or when signed in with a Team or Enterprise plan; the organization can change that default.
- **A required approving review in a ruleset** of the repository. GitHub enforces it, but an account on the ruleset's bypass list is not held by it.

None of these is promised to be impossible to bypass. For scheduled runs and the permission list, see [scheduling.md](plugins/work-issue/skills/orchestrate-issue/scheduling.md).

### Merge rules and external reviewers are yours to define

The merge conditions of the worker are listed in `issue-worker.md` and `nx-migrator.md` under "Merging". Among them: the independent review of the current head has no `blocking` and no `fix-in-PR` finding, the base branch requires at least one check and every required check is green, and the head is still the reviewed one. They are fixed; the plugins have no setting for further ones, and `issue-worker` (not `nx-migrator`) can be told not to merge by whoever starts it, and then stops with the marker reason `awaiting-merge`. Two things are deliberately not part of them:

- **Waiting for an external reviewer** (a review bot such as CodeRabbit, a human reviewer, a scanner that is not a required check). The worker does not wait for such an account, and no merge condition depends on what it posts: a check that the base branch does not require is neither waited for nor a merge condition. The independent reviewer does read the comments and reviews that exist on the PR when it runs, so findings posted before then can enter its verdict; findings posted afterwards do not.
- **Further merge rules**, for example no unattended merge for certain paths or labels, a minimum age of the PR, or merging only by a person.

If you want either, you define it yourself; the plugins do not do it for you. Two ways, which can be combined:

- **In GitHub, which enforces it:** make the external reviewer's check a required status check, or require an approving review, in a ruleset of the repository. GitHub then refuses the merge until the check is green or the approval exists (unless the worker's account is on the ruleset's bypass list, see above). The worker's own wait for required checks cannot see a check that has not reported yet, so under `orchestrate-issue`, which names the required checks in the worker's prompt, a missing one means no merge, and a worker started directly is held only by GitHub.
- **In a `CLAUDE.md` that your runs load,** which Claude Code does not enforce. The runs load these files, and such a rule has been seen to take effect in practice, even where it goes beyond the agent text ("checks that are not required are neither waited for"); it is an instruction to the model, though, so use a ruleset for a rule that must hold. Which files a scheduled run loads is described in [scheduling.md](plugins/work-issue/skills/orchestrate-issue/scheduling.md), under "What the run reads and leaves behind".
