---
name: orchestrate-issue
description: "Run planner and worker for several GitHub issues without a person driving each step — takes explicit issue references, or the open issues carrying the opt-in label auto-work, finds a checkout per repository, spawns work-issue:issue-planner and work-issue:issue-worker in order, replans once when the worker's plan did not hold, and reports per issue. Use when the user says \"orchestrate issues\", \"work the auto-work issues\", \"run planner and worker for 12 and 13\", \"issues automatisch abarbeiten\", \"arbeite die auto-work Issues ab\"."
argument-hint: "[--max <n>] [--label <name>] [--checkout owner/repo=<path>] [owner/repo | owner/repo#n]..."
allowed-tools: Bash, Read, Agent, TodoWrite
---

# Orchestrate issues

Drives the sequence a person otherwise runs by hand: for each issue the context check, `work-issue:issue-planner`, then `work-issue:issue-worker`, which implements, gets the independent review and merges as its own file says. This skill decides nothing about an issue: the planner plans, the worker works, their reports are relayed, not re-judged.

**Run it in the main session, never inside a subagent.** The worker needs its spawn depth for `pr-review:pr-reviewer`; an orchestrator that is itself a subagent takes a level of it, and at the limit the `Agent` tool is withheld.

Nothing is scheduled: an issue that waits for an owner answer ends this run at its questions, and calling the skill again picks it up (`QUESTIONS … answered`).

## Steps

### 1. Select

```bash
<skill directory>/scripts/run-context.py [--label <name>] [--checkout owner/repo=<path>]... [owner/repo | 'owner/repo#n']...
```

Call it by its full path (`--help` describes the output). Arguments: `owner/repo#n` is an explicit issue (naming it is the consent); `owner/repo` takes every open issue of that repository that carries the label (`auto-work`); no argument means the repository of the current directory in label mode. Report every `# SKIPPED` line. A non-zero exit: report its reason and stop.

A labelled issue is taken only when its `ISSUE` line shows a labeler with permission `admin`, `maintain` or `write`. Reason: triage users can add labels without having merge rights, and an issue form can apply labels automatically (the labeler is then whoever opened the issue); without the check anyone who can open an issue could start an unattended merge. A labeler or permission shown as `-` (unreadable) is not taken. The same holds for the `edit` field: an issue whose body or title was edited after the label event by an account without `admin`, `maintain` or `write` (`untrusted`), or whose edits could not be read (`-`), is not taken. Reason: the author of an issue can edit it at any time, and an edit after a plan makes the plan stale, so the planner would replan on text nobody with write access agreed to under the old label. Explicit references are exempt (naming the issue is the consent). An issue that is both named and labelled appears once, as `ref`. Name every issue dropped this way in the report.

The label is not created by this skill; a missing label finds nothing, and the report says so.

### 2. Checkouts

Per repository, once per run, the first of:

1. a `CHECKOUT … given` line;
2. the first `CHECKOUT … found` line that is `clean` and on the default branch of the `REPO` line;
3. `gh repo clone <owner/repo> <scratch directory>/<owner>-<repo>` (a fresh clone is on the default branch).

A found checkout that is dirty or on another branch is not used. Reason: the worker stops on a dirty working tree, and a checkout on another branch is probably in use by another session that the worker's `git switch` would disrupt; a clone is never the owner's working copy. A failed clone ends that repository's issues with a report.

### 3. Context per issue

Run `<skill directory>/../work-issue/scripts/issue-context.py 'owner/repo#n'` inside the issue's checkout and apply the stop conditions of step 1 of `work-issue` (closed, a `PR` that is `open` and `closes`, an assignee other than `ACCOUNT`, `CHECKOUT` not `match`, a skipped source a condition depends on, `# SKIPPED plan`). An issue that stops there is reported with the reason. `QUESTIONS … open` is reported with the comment link: the issue waits for the owner.

Take the remaining issues in order, explicit references in the given order first, then labelled ones oldest first, until the limit of **3 issues per run** (`--max <n>` overrides it). Only an issue for which a planner or worker is spawned counts; the ones that ended above cost nothing. Issues beyond the limit are listed as not taken. Reason for 3: each issue can cost two Opus planner runs, two Sonnet worker runs and an Opus first review round, and the model split exists because of the usage limit; 3 also bounds the unattended merges before a person looks.

### 4. Plan

For each taken issue without `PLAN … current`, spawn `work-issue:issue-planner` with the issue reference and the checkout path. Planners of different issues may run in parallel. Then run `issue-context.py` again: `QUESTIONS … open` is reported with the link; `PLAN … current` goes to step 5; anything else is reported.

### 5. Work

Read the required status checks from the `CHECKS` line of the issue's repository (names are tab-separated) and spawn `work-issue:issue-worker` with the issue reference, the checkout path and, in its prompt:

- **at least one required check:** the names, and that in addition to its own merge rules each named check must appear as `pass` in `gh pr checks <PR> --required` on the reviewed head; a named check that does not appear is not green. Reason: the worker's own rule covers only checks that report, and a merge by an account that may bypass a ruleset would not be held by GitHub.
- **no required check, or any `# SKIPPED rules` / `# SKIPPED protection` line for the repository** (the script then prints `- -`, since a list from one source may be incomplete): that it must not merge. It runs its review rounds, opens follow-up issues as its file says and stops where it would merge. Reason: a check that reports on a PR but is not required (an installed app) can be green without verifying the change, and a green status alone is not a required check. The report then names the open PR, its reviewed head and the verdict for a person to merge, and the skipped source when there was one.

One worker per repository at a time; repositories may run in parallel. Reason: workers of one repository touch the same manifests and collide on rebase and version numbers.

### 6. Rounds

A round is one plan (existing or new) and one worker run; an issue gets at most **2 worker runs**, so one replan. Only the worker's three plan stops go back to the planner, with the stop named in its prompt: the files under **Changes** moved, the code did not show the plan's cause, the issue waits for an open `REF`. Then steps 4 and 5 once more. Any other stop (validation, `blocking`, non-converging `fix-in-PR`, a red or missing check, a refused merge, no reviewer, no current plan) ends the issue with the worker's report.

### 7. Report

Per issue: the outcome (merged PR; open PR awaiting a person, with the reason; questions with the comment link; stopped, with the reason; not taken because of the limit or the labeler) and what was not verified. Relay the agents' reports; do not re-judge them. Done when every selected issue has one outcome line.
