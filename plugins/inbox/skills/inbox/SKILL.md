---
name: inbox
description: List everything on GitHub that a task can be derived from across all of the user's repositories — security alerts, failing default branches, open PRs, dependency PRs from dependabot or renovate, and issues (including bot-opened ones such as spec updates) — in priority order, then ask what to work on. Use when the user says "inbox", "what's open", "what should I work on", "open issues", "was steht an", "woran soll ich arbeiten", "offene issues", "offene PRs" or similar.
argument-hint: [ignore <owner/repo#n> | <owner>]
allowed-tools: Bash, AskUserQuestion
---

# Inbox

One list of what is waiting on GitHub, so nothing has to be remembered. `scripts/collect.py` does all the collecting; this skill only orders, shortens and asks. Never rebuild the list from single `gh` calls — the script reads every source for every repository in one pass, and a hand-made list silently misses the repositories nobody thought of.

The inbox reads GitHub and nothing else. A source that is not on GitHub (a Lighthouse run, a specification that moved on) belongs there as an issue opened by a workflow; then it shows up here without a special case.

## Arguments

- none — the account `gh` is logged in as (or `$INBOX_OWNER`)
- `<owner>` — another user or organisation: `scripts/collect.py --owner <owner>`. Expect `# SKIPPED` lines there: alerts are readable only with admin permission on a repository.
- `ignore <owner/repo#n>` — hide one item for good, see step 4

## Steps

1. **Collect.** Run `scripts/collect.py`. It always reads live and takes some seconds; the summary printed at session start comes from a cache and can be hours old, so never answer from it. A non-zero exit means the list is not available — report the message and stop; do not fall back to the cache or to memory.

   Output is tab-separated: `prio  kind  repo  ref  state  updated  title  url`, already sorted by priority and then by last update. Lines starting with `#` are status lines.

2. **Report what is missing.** Every `# SKIPPED <source> <repo>: <reason>` line is a source that could not be read, so its items are absent, not zero. Name them before the list. A `SKIPPED` line whose repository is `<owner>/*` concerns the whole source: `dependabot` when repositories without admin permission report no alerts, which cannot be told apart from unreadable, and `search` when there are more open issues and pull requests than the search returns, so the `PR`, `DEPS` and `ISSUE` groups are incomplete. Mention the `ignored=<n>` count from the first line when it is not 0.

3. **Show the list**, grouped by kind in the order the script gives:

   | kind | what it is | `state` |
   |---|---|---|
   | `SECURITY` | open Dependabot or code-scanning alerts of a repository | count, and the worst severity for Dependabot |
   | `CI` | a default branch whose last commit failed its checks | `failure` or `error` |
   | `PR` | an open pull request | `<checks>/<review decision>`, `/draft` appended |
   | `DEPS` | an open pull request from dependabot or renovate | as `PR` |
   | `ISSUE` | an open issue | its labels; `bot` first when a bot opened it |

   One line per item: repository, ref, title, state, age. The age comes from the `updated` column, and what that is depends on the kind: the last activity for `PR`, `DEPS` and `ISSUE`, the creation of the newest open alert for `SECURITY`, the date of the failing commit for `CI`. Label it accordingly ("newest alert 4 days ago", "red since the commit of …") instead of calling all of them "updated". Keep every item — shortening the list is the user's call (step 4), not yours. Where a group is long, group it by repository instead of dropping lines.

   The script's order is a default, not a judgment. Say so where the list itself shows a better one: an issue that explains a red default branch belongs next to it, several repositories with the same Dependabot count usually share one upstream cause and are one task, and a PR with green checks that only waits for its review is cheaper to finish than anything new. Do not open the items to find out more at this point — each one read costs context for an item the user may not pick.

4. **Ask what to work on** with a short recommendation of one to three items and the reason for each. Then:

   - the user picks an `ISSUE` item and the session's skill list names `work-issue:work-issue` → hand it to that skill as `'owner/repo#n'` (quoted), run from the checkout of that repository; do not read the issue beforehand, the skill reads it itself. The skill stops on a directory that is not a checkout of the issue's repository, so when the current directory is not one, ask the user for the checkout's path (the inbox reads GitHub only and cannot find it)
   - the user picks any other item (`SECURITY`, `CI`, `PR`, `DEPS`), or an `ISSUE` while the session's skill list does not name `work-issue:work-issue` → read it (`gh issue view`, `gh pr view`, `gh api repos/<repo>/dependabot/alerts`) and work on it in that repository under its own rules
   - the user never wants to see an item again → append its `owner/repo#n` to the ignore file as its own line and say so. `ignore <owner/repo#n>` as the argument does only this, without collecting first.

   **Done when** the user has picked something or said that nothing is to be done now.

## The ignore file

`$XDG_CONFIG_HOME/inbox-ignore` (default `~/.config/inbox-ignore`), one glob per line. Only a line that starts with `#` is a comment — `#` inside a line is part of the ref. A glob is matched against `owner/repo`, which hides everything in that repository, and against `owner/repo` followed by the ref:

```
# one issue
tehw0lf/airbash#7
# a whole repository
tehw0lf/some-archive
# one source everywhere
tehw0lf/*code-scanning
```

Add only what the user named. An ignored security alert or red branch stays invisible for good, so never ignore one to make the list shorter. An ignored source is not queried at all, so it cannot show up as `SKIPPED` either.

## Not covered

- repositories that are archived or forks, and anything outside GitHub
- secret-scanning alerts
- workflow runs that failed without being part of the head commit's check rollup, such as a failed Dependabot update run: `CI` is the rollup of the default branch's head commit and nothing else
- open issues and pull requests beyond the first 1000 of an owner; the script prints a `# SKIPPED search` line when that happens
- the worst Dependabot severity and the newest alert date are taken from a repository's first 100 open alerts
- pull requests and issues in other owners' repositories that involve the user
- more than 100 code-scanning alerts per repository are shown as `100+`
