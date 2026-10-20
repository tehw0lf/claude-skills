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

The worker's own merge conditions are fixed: the independent review of the current head has no `blocking` and no `fix-in-PR` finding, every check the base branch requires is green, and the head is still the reviewed one. Everything beyond that is not in the scope of the plugins, and they have no setting for it. That includes:

- **Waiting for an external reviewer** (a review bot such as CodeRabbit, a human reviewer, a security scanner that is not a required check): whether a run waits for it, how long, and what it does with its findings. The worker does not wait for such an account, and no merge condition depends on what it posts after the verdict; a check that the base branch does not require is not a merge condition either.
- **Further merge rules**: for example no unattended merge for certain paths or labels, a minimum age of the PR, a required approval, or merging only by a person.

Define these in the `CLAUDE.md` files that your runs load, and you have to: without a rule there, the plugins merge as soon as their own conditions hold, and a bot that has not posted yet does not hold them. A rule that has to hold is better enforced by GitHub (a required check or a required approving review in a ruleset, see the gates above), because the worker follows an instruction file but nothing enforces it. Which files a scheduled run loads is described in [scheduling.md](plugins/work-issue/skills/orchestrate-issue/scheduling.md), under "What the run reads and leaves behind".
