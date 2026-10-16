# claude-skills

A Claude Code plugin marketplace (`tehw0lf-claude-skills`) of skills and agents.

## Install

```
/plugin marketplace add tehw0lf/claude-skills
claude plugin install <name>@tehw0lf-claude-skills --scope user
```

Add the marketplace from GitHub, not as a local directory: a directory marketplace installs whatever the working tree holds, including unmerged and uncommitted changes.

## Fully automated use

`work-issue` (orchestrator, planner and worker) and `nx-migrate` (`nx-migrator`) can run without a person up to and including the merge of a pull request: the worker merges once an independent review is clean and the required checks are green. The plugins are MIT licensed and used at your own risk. Whether a run may merge unattended, and what stops it from doing so, is yours to decide and to set up. Three gates exist, alone or combined:

- **An instruction in a `CLAUDE.md`** (for example "never merge"). The model follows it; Claude Code does not enforce it. Which `CLAUDE.md` files a scheduled run loads is described in `scheduling.md`, under "What the run reads and leaves behind".
- **A Claude Code permission rule** (`deny` or `ask` on `Bash(gh pr merge *)`). Claude Code enforces it, and `deny` is evaluated before any `allow`. It does not cover a merge through the REST endpoint while `Bash(gh api *)` is allowed, and a mod installed in Claude Code can approve a call that a `deny` rule refuses unless managed settings or a Team or Enterprise plan hold the rule.
- **A required approving review in a ruleset** of the repository. GitHub enforces it, but an account on the ruleset's bypass list is not held by it.

None of these is promised to be impossible to bypass. For scheduled runs and the permission list, see [scheduling.md](plugins/work-issue/skills/orchestrate-issue/scheduling.md).
