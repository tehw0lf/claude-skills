# Running orchestrate-issue on a schedule

`orchestrate-issue` schedules nothing itself: one call takes the issues that are ready, up to its limit (one unless `--max` is given), and an issue that waits for an owner answer ends that run at its questions. To pick up `auto-work` issues without a person starting each run, something outside the skill has to call it. This file describes two ways, a local systemd timer (one session at a time, or one per repository with several in parallel) and a cloud routine. Neither is part of the plugin; they are examples.

## What every trigger has to respect

- Run it as a main session. A headless `claude -p` run is one; a subagent is not (the worker needs its spawn depth for `pr-review:pr-reviewer`).
- Name the scope: a repository, `/work-issue:orchestrate-issue <owner>/<repo>`, or an account or organisation, `/work-issue:orchestrate-issue --owner <login>`, which covers every repository of it with an open `auto-work` issue (found by an issue search, archived repositories left out). The skill finds local checkouts only under `$CODING_ROOT` (default `~/Nextcloud/Coding`), not from the working directory, and a systemd service does not inherit the shell's environment: set `Environment=CODING_ROOT=<directory>` in the unit, or pass `--checkout <owner>/<repo>=<path>`. Otherwise every run clones, which is the path the verified run did not take (see below); with an account as scope that holds for every repository that has no checkout there. Without an argument the skill takes the repository of the current directory, never an account. The example below starts in the home directory, which is not a checkout, and a checkout used as working directory can still be left on a feature branch by the worker (a dirty tree, a refused switch back, a run that was cut off), so the scope is named explicitly.
- The label `auto-work` is the consent to an unattended merge. It is not created by the skill; a missing label finds nothing. Only a labeler with `write`, `maintain` or `admin` counts, and an issue that an account without write access ever edited, before or after the label, is skipped.
- An issue that is already started is continued by the next run: its own open PR goes back to the worker for validation, review and merge. When the worker stops there, it leaves a comment starting with `<!-- work-issue:stopped … -->` on the PR, and later runs report the PR as waiting for a person and spawn nothing until somebody pushes to it or comments on it with admin, maintain or write permission. The marker reason `pending-check` needs nobody: the worker waits at most five minutes for the required checks, and when one has not reported or concluded by then, the next run that finds all of them passed resumes the PR and merges it. The answer goes on the PR: a comment on the issue makes the plan stale (`owner-comment`), and the PR is then reported as an own PR on a plan that is no longer current instead of being continued.
- One issue per run unless `--max` is given. A trigger that runs often with a high `--max` raises the number of unattended merges per day; choose both together. Each reviewed PR can also leave follow-up issues without `auto-work`, which wait for a person, so the same two settings set how fast that backlog grows.
- Where the default branch has no required status check, or the checks could not be read, the worker stops at an open, reviewed PR and does not merge. That is by design.
- One trigger per repository, and none next to an account-wide one: a trigger whose scope is an account or organisation covers every repository in it. The dispatcher under "Several repositories in parallel" is one trigger in this sense: it replaces the single unit, it does not run next to it. Two runners (a timer and a routine, or two machines) on the same account are not guarded against each other, also not when their scopes overlap: the assignee check only stops on an assignee other than the account itself.
- An account as scope widens what one label can start: every repository the token reaches becomes a target of unattended merges once a labeler with write access sets `auto-work` there. The gate is the same per repository (a required status check; without one the worker does not merge), the list under "Permissions" was built for one npm repository, and a repository of another ecosystem needs its own tool rules, otherwise the denial stops the worker there.

## Gates against an unattended merge are yours

A run can merge without a person once an issue carries `auto-work`, the default branch requires a status check, and the conditions under "Merging" in the worker's instructions (`agents/issue-worker.md`) hold. It is used at your own risk (the plugin is MIT licensed). The plugin's own stop at a missing required check is not a gate you set. Any gate against the merge is yours to build, alone or combined:

- **An instruction in a `CLAUDE.md`.** The model follows it; Claude Code does not enforce it. Which files a run loads is under "What the run reads and leaves behind".
- **A Claude Code permission rule** (`deny` or `ask` on `Bash(gh pr merge *)`). Claude Code evaluates rules `deny`, then `ask`, then `allow`, so a `deny` beats the `Bash(gh pr *)` allow of the example list, and with `--permission-prompts none` an `ask` acts as a denial. A Bash rule matches the text of the command, not what the command does: `gh pr --repo <owner>/<repo> merge <n>` does not start with `gh pr merge` and is allowed by `Bash(gh pr *)`, a REST merge is not covered while `Bash(gh api *)` is allowed (see "Permissions"), and the rules that run arbitrary code (`python3`, `node`, `npx`, `uv`) can call a merge as well. A mod installed in Claude Code can approve a call that an `ask` rule holds. It can also approve a call that a `deny` rule refuses, except that by default it cannot on a machine with managed settings or when signed in with a Team or Enterprise plan; the organization can change that default. These gaps follow from the permission documentation and gh's argument parsing; the matcher was not run against them.
- **A required approving review in a ruleset.** GitHub enforces it, but an account on the ruleset's bypass list is not held by it. A merge that GitHub refuses is reported by the worker, not worked around. How GitHub treats a self-approval or an administrator's merge was not checked.

No gate is promised to be impossible to bypass.

## Local: systemd user timer

Verified once, on one machine: a timer started the service, the skill took one `auto-work` issue, the planner posted its plan, the worker opened a PR, the independent review ran on Opus and the PR was squash-merged with both required checks green. The run took about seven minutes. Run it once the same way (a labelled test issue, `--max 1`, the 30-second drop-in below) before leaving the hourly timer on.

`~/.config/systemd/user/work-issue.service`:

```ini
[Unit]
Description=Work auto-work issues of <owner>/<repo> (or of an account with --owner)

[Service]
Type=oneshot
# gh, git and python3 must be on this PATH (the skills' scripts start with `#!/usr/bin/env python3`), uv and node/npm only for a target that uses them; a per-user Node install is on none of these directories, add it. Adjust the path of claude (`command -v claude`)
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin
# where the skill looks for local checkouts of the repository (default ~/Nextcloud/Coding)
Environment=CODING_ROOT=%h/<directory with your checkouts>
# the skills' temporary directories (`mktemp -d`) are created under TMPDIR; --add-dir makes that directory a working directory, which the redirects, the file tools, `cp` and `cd` into it need (see Permissions)
Environment=TMPDIR=%h/.cache/work-issue/tmp
ExecStartPre=/usr/bin/mkdir -p %h/.cache/work-issue/tmp
# the skills never remove their temporary directories; drop entries older than seven days (the leading `-` keeps a failure from stopping the run; a oneshot unit does not overlap itself, so no running session loses files)
ExecStartPre=-/usr/bin/find %h/.cache/work-issue/tmp -mindepth 1 -mtime +7 -delete
WorkingDirectory=%h
# skip the unit, without a failure, when no auto-work issue can be taken (no tokens, see "Skipping empty runs" below)
ExecCondition=%h/.claude/plugins/marketplaces/tehw0lf-claude-skills/plugins/work-issue/skills/orchestrate-issue/scripts/has-work.py <owner>/<repo>
ExecStart=%h/.local/bin/claude -p "/work-issue:orchestrate-issue --max 1 <owner>/<repo>" --permission-prompts none --permission-mode acceptEdits --add-dir %h/.cache/work-issue/tmp --settings %h/.config/work-issue/settings.json
```

`~/.config/systemd/user/work-issue.timer`:

```ini
[Unit]
Description=Run work-issue.service hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now work-issue.timer
journalctl --user -u work-issue.service      # the skill's report is the output of the run
```

### Skipping empty runs

Without a check every tick starts a full session, also when no issue carries `auto-work`; such a run costs tokens and, once the usage limit is reached, ends as a failed unit. `scripts/has-work.py [--owner <login>]... [<owner>/<repo>]...` answers the same question without a model: is there an open issue with the label whose newest questions of the account are not still open? Give it the same scope as the session's call (`<owner>/<repo>` or `--owner <login>`). For `--owner` it reads the first page of one issue search instead of a repository listing: the search index can lag behind a label set just before the call (found by the next tick), a search that reports itself incomplete or holds more than 1000 results is a failed check, exit 255, and so is one with more results than the first page of 100 when no issue on that page is a hit (the rest is not read). Every listed issue is read with `issue-context.py`. One with `needs-decision` is no hit while it shows `QUESTIONS … open`; an issue whose own PR carries an unanswered stop marker on its current head is no hit either, except for the reason `pending-check` once every required check of the default branch is listed as passed by `gh pr checks --required` on that PR (it reads the rulesets, every page, and the classic protection for the names). Any other issue is a hit, so a PR that waits for a person or for checks no longer starts a session at every tick. The planner removes the label only after it has read the answer, so a test on the label alone would skip an answered issue for good, and the session itself decides by the `QUESTIONS` and `PLAN` lines, not by the label. It lists issues and runs `issue-context.py` for each listed one until the first hit; it writes nothing.

By `systemd.service(5)` an `ExecCondition=` command that exits 1 to 254 skips the remaining commands and the unit is not marked failed, 255 or an abnormal end marks it failed, and 0 goes on. The script exits 0 on a hit, 1 on none, and 255 when the check itself failed (`gh` missing or not authenticated, GitHub unreachable, a time-out), so a check that breaks while it runs shows as a failed unit and starts no session. That includes `issue-context.py` reporting `# SKIPPED account` or `plan`, or `# SKIPPED comments` without a `QUESTIONS` line or for any reason but a thread of more than 100 comments (under that line the `QUESTIONS` line is used as it stands: the window cut loses nothing, but a comment that GitHub returned as null inside the newest 100 is not seen, so the check can report a hit or a wait that the session would decide the other way; `permission` alone never fails the check), and any unexpected error in the script. A script that systemd cannot start is different: with a path that does not exist, or `python3` not on the unit's `PATH` (the shebang is `#!/usr/bin/env python3`; `env` then exits 127), systemd ends the `ExecCondition=` as `exec-condition`, which skips the unit without a failure (tried in transient user units; systemd 262). Every tick would then be skipped and nothing would report it. The path in the example exists only once the marketplace is registered under that name and `claude plugin marketplace update` has pulled this version. So run the exact `ExecCondition=` command line by hand once (it prints `WORK` or `NONE`), and after the first ticks look in the journal for "Skipped due to 'exec-condition'" next to a `NONE` you expect, not next to an error.

If the unit's session uses another label, pass the same one to the script with `--label`.

The unit above points at the marketplace clone, which follows `main` after `claude plugin marketplace update`; the plugin cache is keyed by version and the path would go stale at each update. `gh` and `python3` must be on the unit's `PATH`, and the `ExecStartPre=` lines run after the check.

It is the simple test of the selection, not the whole of it. These cases pass the check and the session then reports them and spawns nothing, as before: a labeler without write access, an edit by a non-writer, an assignee other than the account, a pull request of somebody else that already closes the issue, an own PR that does not link the current plan and carries no unanswered stop marker on its head, a PR with a conflict, an own PR whose `RESUME` source could not be read (`# SKIPPED resume`). The reverse case does not occur: an issue the session would work is not skipped here, because the check does not read the label alone (an issue with `needs-decision` and no questions of the account, for example a label set by hand, is a hit). A label set so recently that the label-filtered listing lacks it is found by the next tick. Not checked: a full scheduled run of the unit, an `--owner` argument against an organisation with its own repositories, private repositories in the search; the script was run by hand against GitHub (no hit, a failing repository, bad arguments) and with a stubbed `gh` (hit kinds, a failing `gh api user`, a `null` listing); the open-questions case only with a stubbed `issue-context.py` output.

A systemd user manager runs only while the account has a login session, unless lingering is on: check with `loginctl show-user "$USER" -p Linger`, enable with `loginctl enable-linger "$USER"` if runs must continue while logged out. Without it the hourly run does not happen while logged out, and `Persistent=true` catches up only once at the next login. A run without a login session was not tested, and neither was whether `gh` and Claude Code find their credentials there.

A oneshot service does not start a second run while the first is still going, so a long run does not overlap itself.

To test the timer without waiting for the full hour, add `~/.config/systemd/user/work-issue.timer.d/test.conf` with `[Timer]` and `OnActiveSec=30s`, run `systemctl --user daemon-reload`, then `systemctl --user restart work-issue.timer`. Remove the drop-in afterwards. A oneshot service stays `activating` while it runs, so wait on `ActiveState` leaving `activating` (`systemctl --user is-active` reports it as not active and ends a wait loop at once).

### Permissions

With `--permission-prompts none` anything that would prompt is denied, and nobody can answer. The first runs showed which steps need a rule, in this order: the planner agent was denied the `plan-issue` skill; then the orchestrator's re-check, `issue-context.py` piped through `grep`, was denied as a compound command. `--settings` loads a file in addition to the user's settings, so the rules apply to this run only. The list below is the file `~/.config/work-issue/settings.json` that completed the run above, plus fifteen rules; the transcripts of that run show no denied tool call, so the file was sufficient for it. It was not reduced to a minimum, and which rules were exercised was not counted. Fifteen added rules do not come from a run: `Bash(gh label create needs-decision *)` comes from the last step of `plan-issue` (creating the `needs-decision` label when a plan ends in open questions and the repository has none), and `Bash(*check-lock-overrides.py*)` from step 5 of `work-issue` (the check for uv, Cargo, Go and Composer, which a run that touches no dependencies never calls), `Bash(git -c core.quotePath=false ls-files*)` from step 2 and the stop clean-up of `work-issue`, which list ignore files with that form (the prefix rule `git ls-files*` does not match it), and six rules for the commands of the stop clean-up and of step 5 of `work-issue` that no rule covered: the narrowed `Bash(git reset --hard)`, `Bash(git clean -fd)` and `Bash(git clean -f -- *)` (the stop clean-up and its resume-mode form: `git reset --hard`, `git clean -fd`; `git clean -f -- ':(literal)<path>' …` removes the untracked entries of step 7 of `work-issue` and the listed ignore files of the stop clean-up, and `git clean -f -- package-lock.json` the lockfile of step 6, so no `rm` rule is needed; the forms and what they allow and deny were run against the matcher, see below), `Bash(LC_ALL=C comm *)` (the stop clean-up), `Bash(git check-ignore*)` (`git check-ignore -q` of step 5) and `Bash(git -c core.quotePath=false check-ignore*)` (the stop clean-up; as for `ls-files`, the prefix rule does not match the `-c` form). The narrowed git rules were exercised in a probe (below); the other three of the six are read from the files, not seen in a run, and so are the next two: `Bash(git rev-list*)` (`git rev-list --count refs/remotes/origin/<branch>..refs/heads/<branch> --` in resume mode, step 1 of `work-issue`; the documentation does not say whether Claude Code counts `git rev-list` among its built-in read-only git forms, so it has its own rule) and `Bash(git restore*)` (`git restore -- ':(literal)<path>' …` in step 7). The other git forms that resume mode in step 1 of `work-issue` runs are covered by rules that are in the list already; this coverage is read from the files, not run against the matcher: `git fetch origin` by `Bash(git fetch*)`, `git rev-parse --verify refs/remotes/origin/<branch>` by `Bash(git rev-parse*)`, `git switch --no-overwrite-ignore --track refs/remotes/origin/<branch>` and `git switch --no-overwrite-ignore <branch>` by `Bash(git switch*)` (so is the switch back to the starting branch in the stop clean-up, `git switch <branch>` or `git switch --detach <commit>`), and `git merge --ff-only --no-overwrite-ignore refs/remotes/origin/<branch>` by `Bash(git merge*)`; `gh pr view <PR> -R <owner>/<repo> --json headRefOid -q .headRefOid` (the head for a stop marker and for the merge check) is covered by `Bash(gh pr *)`, read from the files in the same way. The last four added rules are read from the files as well: `Bash(mktemp -d*)` (the temporary directory of `work-issue`, `plan-issue`, both agents and `pr-review:pr-reviewer`, run as a plain command), `Bash(git stash create)` (the snapshot of the tracked content before and after the version bump in step 6 of `work-issue`; the exact rule does not open `git stash drop`, `clear` or `pop`, which `git stash*` would, and the skill runs it as the bare command because an exact rule does not match text after it), `Bash(git -c core.quotePath=false status*)` (steps 1, 2 and 7 of `work-issue` list untracked entries with that form; as for `ls-files`, the prefix rule `git status*` does not match it) and `Bash(LC_ALL=C grep *)` (the comparison of the status snapshots in step 7; the list has `LC_ALL=C comm *`, and which variables the matcher strips before matching is not documented). The bounded wait for the required checks under "Merging" in `issue-worker.md` (`timeout 300 gh pr checks …`) needs no rule of its own: by the Claude Code permission documentation `timeout` is a wrapper that the matcher strips, so `Bash(gh pr *)` covers it. The questions path and the close path (the planner's `gh issue close` on an owner's answer) of a scheduled run were not exercised:

```json
{
  "permissions": {
    "allow": [
      "Skill(work-issue:plan-issue)", "Skill(work-issue:work-issue)", "Skill(work-issue:orchestrate-issue)",
      "Agent(work-issue:issue-planner)", "Agent(work-issue:issue-worker)", "Agent(pr-review:pr-reviewer)",
      "Bash(*issue-context.py*)", "Bash(*run-context.py*)", "Bash(*merge-facts.py*)",
      "Bash(*repo-commands.py*)", "Bash(*check-install-config.py*)", "Bash(*check-lock-overrides.py*)",
      "Bash(python3 *)", "Bash(uv *)", "Bash(npm *)", "Bash(npx *)", "Bash(node *)",
      "Bash(gh issue *)", "Bash(gh pr *)", "Bash(gh api *)", "Bash(gh run *)",
      "Bash(gh repo clone *)", "Bash(gh repo view *)", "Bash(gh label create needs-decision *)",
      "Bash(git status*)", "Bash(git -c core.quotePath=false status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git show*)", "Bash(git fetch*)",
      "Bash(git switch*)", "Bash(git checkout*)", "Bash(git branch*)", "Bash(git add*)", "Bash(git commit*)",
      "Bash(git push*)", "Bash(git rebase*)", "Bash(git merge*)", "Bash(git rev-parse*)", "Bash(git rev-list*)", "Bash(git remote*)",
      "Bash(git ls-files*)", "Bash(git -c core.quotePath=false ls-files*)",
      "Bash(git check-ignore*)", "Bash(git -c core.quotePath=false check-ignore*)", "Bash(git reset --hard)", "Bash(git clean -fd)", "Bash(git clean -f -- *)", "Bash(git restore*)", "Bash(git worktree*)", "Bash(git cat-file*)", "Bash(git stash create)", "Bash(mktemp -d*)",
      "Bash(ls*)", "Bash(cat *)", "Bash(grep *)", "Bash(jq *)", "Bash(head *)", "Bash(tail *)",
      "Bash(wc *)", "Bash(sort *)", "Bash(cut *)", "Bash(LC_ALL=C comm *)", "Bash(LC_ALL=C grep *)"
    ]
  }
}
```

The stop clean-up of `work-issue` chains commands: `git reset --hard && git clean -fd` and three forms of `git add -N … && git diff HEAD >> <temporary directory>/abandoned.diff` (for `.`, for the listed paths and, with `-f`, for the selected paths). A probe run of a headless `claude -p` session (Claude Code 2.1.304, model `sonnet`, `--permission-prompts none`, `--setting-sources project` in a throwaway repository so that no other settings source could allow a form, `--settings` with only the git rules of this list and `Bash(git add*)` and `Bash(git diff*)`) exercised the narrowed rules; allowed or denied was read from the tool results, not from the session's own report. With `Bash(git reset --hard)`, `Bash(git clean -fd)` and `Bash(git clean -f -- *)` these were allowed: `git reset --hard`, `git clean -fd`, `git reset --hard && git clean -fd`, `git clean -f -- ':(literal)a.txt'`, the same with two paths (one with a space), `git clean -f -- package-lock.json`, `git add -N -f -- ':(literal)sub/.gitignore'`, and (with `--permission-mode acceptEdits` and `--add-dir` on the target directory, as the unit runs) `git add -N . && git diff HEAD >> <temporary directory>/abandoned.diff` and its `-f` form. These were denied: `git reset --hard HEAD~1`, `git reset --hard origin/main`, `git reset HEAD~1`, `git clean -fdx`, `git clean -fX`, `git clean -fx`, `git clean -fd -x`, `git clean -f -x -- a.txt`, `git reset --hard && git clean -fdx`, `git clean -fd; git clean -fdx` and (the part after `&&` decides) `git add -N . && git clean -fdx`. With the broad rules `git reset*` and `git clean*` every one of these denied forms was allowed (`git reset HEAD~1` reached git, which failed on a revision of the probe repository), which is the defect. A chain is therefore matched part by part, as the documentation says, for these rules. Also observed: `git reset --hard  # comment` is allowed by the exact rule `Bash(git reset --hard)` (a trailing comment does not stop the match in this version), but the skill runs the clean-up commands without the comments anyway. Not probed: `git clean -f -- *` allows any pathspec after `--`, `.` included (read from the documentation: a `*` stands for any text); other versions of Claude Code; the redirect forms without `--permission-mode acceptEdits` (a first run without it denied `>>` into a directory outside the working directory, with the broad rules as well, so the denial there comes from the redirect, not from the git rules). The same documentation (section "Redirections") says the target of `>` or `>>` is checked against `Edit` rules and the working directories independently of the Bash rule. The unit sets `TMPDIR` to a dedicated directory and passes it with `--add-dir`, so `mktemp -d` creates the skills' temporary directories there. By the same documentation (read there, not run with the matcher) that makes the directory a working directory, which covers: output and input redirects into it, the files the agents write with the file tools (`--body-file` files, `comment.md`), `cp` of test files into the base worktree under it, and `cd` into it. Not covered: `cd` combined with `git` still prompts by the documentation, so the `<command>` inside a `( cd <path> && pwd && <command> )` subshell must not be `git`; the skills create and remove the base worktree with `git worktree` from the checkout, which stays the working directory. The same limit reaches a `pr-review:pr-reviewer` spawned by an unattended `issue-worker`: with this list the plain commands for the throwaway repository with commits that its instructions describe (`git init` has no rule, and `git` inside the `cd` subshell prompts) and for a nested agent session (no rule allows `claude`) are denied under `--permission-prompts none`, and those checks belong under `Not covered` in its verdict. The interpreter rules above (`python3`, `uv`, `node`, `npx`) would let wrapped forms of the same commands through, but the reviewer is told not to retry a denied check in another form, and that is no reason to rely on them. The list gets no rule for them: one for git in an arbitrary directory (for example a `git -C <path>` form) would be one of the broadest in the list, and is not part of it. This too is read from the documentation, not run with the matcher. For `mktemp -d -p <scratch directory>`, used when a session was given a scratch directory: Claude Code's own temporary directory defaults to `os.tmpdir()` on Linux, which follows `TMPDIR` when it is set unless `CLAUDE_CODE_TMPDIR` says otherwise, so such a scratch directory may lie inside the `--add-dir` directory; whether a headless session is given one, where, and whether it is writable without a rule is not verified. The place to confirm all of this is the transcripts of the verified run, or a probe run with this list.

This list is broad, and these are the rules that carry the risk. The token the run uses decides what a hijacked run can reach: give it only the repositories it works on and no `workflow` or administration scope, and keep unattended runs away from keys and checkouts they do not need (a container or a separate user account). Everything in the list runs as the user that starts the session.

- `Bash(python3 *)`, `Bash(uv *)`, `Bash(node *)` and `Bash(npx *)` each allow arbitrary code (`python3 -c …`, `uv run …`, `node -e …`), which is close to bypassing the permission check for Bash. `Bash(npm *)` runs the repository's scripts. The skills' own files run no `python3` or `node` command (their scripts start with a shebang and are covered by the script rules above), so these two rules serve the target repository's own commands (the validation commands that `repo-commands.py` prints, the reproduction of step 4 of `work-issue`, the checks of `pr-review:pr-reviewer`); leave out the ones that repository does not use.
- The script rules (`Bash(*issue-context.py*)` and the other five) have a wildcard on both sides, so they match any single command that contains the file name anywhere. Anchor them on the script path to match only the script.
- `Bash(gh api *)` allows any GitHub API call the token can make, including merging a PR through the REST endpoint, which skips the worker's `--match-head-commit` guard, and deleting branches. A narrowed list must still allow the `gh api` calls the skills and agents run as their own command, written here as their files write them (read from the files, not seen in a run): `gh api repos/<owner/repo>/issues/comments/<id> -q .body` (the plan comment, in `plan-issue` and `work-issue`), `gh api repos/<owner/repo>/labels/needs-decision` (the label check of `plan-issue`) and, in `pr-review:pr-reviewer`, `gh api --paginate repos/<owner>/<repo>/issues/<n>/comments`, `gh api --paginate repos/<owner>/<repo>/pulls/<n>/reviews` and `gh api --paginate repos/<owner>/<repo>/pulls/<n>/comments`, and in `issue-worker` the branch update `gh api -X PUT repos/<owner>/<repo>/pulls/<PR>/update-branch -f expected_head_sha=<reviewed-sha>` (a write; without it a branch behind a base that requires an up-to-date branch is never merged unattended); the flag comes before the path, so a rule written on the path alone does not match the reviewer's calls. The helper scripts make their own `gh api` calls inside the script; whether the script rules cover them was not tested. A narrowed rule that contains a `*` (the plan comment read, the reviewer's three calls and the branch update have a variable `<id>`, `<n>`, `<PR>` or `<reviewed-sha>`, so each needs one, and a rule for the update is a `PUT` rule that a `*` does not pin either; the label check can be one exact rule per repository) does not pin the method or the endpoint: by the Claude Code permission documentation (read there, not tried in a run) a `*` in a Bash rule stands for any text, including `/`, spaces and further flags, so such a rule still allows another method (`-X`/`--method`, or a `-f`/`-F` parameter, which switches `gh api` to `POST` unless `--method` sets another method) and other paths under its literal prefix. `--paginate` in the rule does not close the gap, although `gh api` rejects it together with an explicit method other than `GET`: checked with gh 2.102.0 against a non-resolvable host with `--verbose` (no request reached GitHub), `--paginate -X PUT <path>` is rejected, but `--paginate -f body=x <path>` and `--paginate -F per_page=1 <path>` send `POST`, and a later `--paginate=false` lets `-X PUT` through (`--paginate <path> --paginate=false -X PUT -f a=/comments` sends `PUT <path>`, so it still ends in the tail of the reviewer's call). Whether the permission matcher accepts such commands for a given rule was not run. Only a rule without a `*`, one exact command, fixes the method; a list that keeps wildcard rules accepts that gap. This list does not replace the run-once-and-read-the-denial test.
- `Bash(gh issue *)` alone allows `gh issue edit --add-label`, and `Bash(gh api *)` alone allows the REST labels endpoint. `run-context.py` accepts a label added by an account with write access, and the running account has it, so a label the session adds itself counts as consent for the next run. Narrowing `gh api *` does not close this while `gh issue *` stays, and the worker needs `gh issue` for its follow-up issues. The planner also runs `gh issue close` under the close rule of `plan-issue`, and the permission allows closing any issue the token can reach. The skills tell the agents not to act on issue text, so this is not a defect in them, but the permission list does not stop it.
- `Bash(git clean -f -- *)` still allows any pathspec after `--` (`git clean -f -- .` removes every untracked, not ignored, file; ignored files stay, because `-x` is not allowed). The narrowed git rules narrow nothing while another settings source (the user's own settings) still allows `git reset` or `git clean` broadly: `--settings` adds to the user's settings, and the probe above loaded no other source. The rules were run only against Claude Code 2.1.304. Replace the two broad rules in `~/.config/work-issue/settings.json` by hand with the three narrowed ones; the agents do not change that file. The skill's own uses are `git reset --hard`, `git clean -fd` and `git clean -f -- ':(literal)<path>' …` (the stop clean-up and step 7; step 6 names a fixed file); the stop clean-up no longer runs `git clean -fx`, it marks the selected ignore files with `git add -N -f` and removes them with `git reset --hard`.
- `Bash(git restore*)` also matches `git restore --source=<ref>` and whole-tree restores (`git restore .`), which discard uncommitted work; the skill restores only single files, each as a `:(literal)` argument, that the change did not touch on purpose (step 7), and the list does not gate the other forms.
- `--add-dir` on the temporary directory grants read and write access to it for the whole session, and `TMPDIR` also moves the temporary files of other tools (npm, pytest and the like) and, by default, Claude Code's own temporary files there; the unit removes entries older than seven days before each run (`find … -delete`, which can leave a stale registration of a deleted base worktree in the checkout: `git worktree prune` removes it), nothing else cleans the directory, and the seven days are not tuned to the growth, which was not measured.
- `Bash(git push*)` and `Bash(gh pr *)` are what lets the worker push and merge unattended.

Narrow them to what the target repository needs; no narrowed list was run, so a narrower file needs the same run-once-and-read-the-denial test. `--permission-mode acceptEdits` lets the worker edit files. A mode that bypasses all checks combined with an unattended merge is a decision about that repository's risk, not a default. The list was built for one repository (an npm project); another ecosystem needs its own tool rules, found the same way: run once, read the denial, add one rule.

### What the run reads and leaves behind

- Instruction files, read from the transcripts of the run: the main session (started in `%h`) loads only the user-level `~/.claude/CLAUDE.md`. The `issue-worker` and, one level below it, the `pr-review:pr-reviewer` both received the `CLAUDE.md` files of the checkout's parent directories as `nested_memory` attachments once they worked in the checkout. So the rules of the checkout's location apply to the worker and the reviewer. The planner's transcript was not inspected.
- The checkout's own `CLAUDE.md`: the repository of that run has none, so its transcripts cannot show it, and the worker's commands there came from `repo-commands.py`, not from an instruction file. A separate probe, a headless `claude -p` started in `%h` that used the `Read` tool on a file inside a repository that has a root `CLAUDE.md`, received that file as a `nested_memory` attachment next to the parent directories' files. So a repository's `CLAUDE.md` is loaded once a session reads a file there. The probe used `Read`; whether a session that only runs shell commands in the checkout gets it was not tested.
- Where the transcripts are: `~/.claude/projects/<working directory with / as ->/<session id>.jsonl`, with the agents under `<session id>/subagents/` (`*.meta.json` names the agent type and spawn depth, and the model when the spawn set one). A headless run writes them like an interactive one; this is where a denied tool call or a loaded instruction file shows up when the journal only has the final report.
- Checkout: the orchestrator uses a clean checkout on the default branch, else it clones. After a merge and after a stop the worker switches the checkout back to the branch it started on when the tree is clean and git allows the switch; otherwise (a dirty tree, a refused switch; a run that was cut off cannot switch at all) the checkout stays on the PR branch, which the next run does not use and clones instead, so switch it back by hand if you want it reused. The clone path (`gh repo clone` into a scratch directory, the worker editing and installing there under `acceptEdits`) was not exercised by the verified run, so make the first test a second run, or leave the hourly timer off until one has gone through it.

### Several repositories in parallel

The unit above runs one session at a time for its whole scope: while it works one repository, an `auto-work` issue in another waits for the next tick that finds the unit idle. To work several repositories at once, replace it by a template unit with one instance per repository and a dispatcher that a timer starts. Two limits, both 1 by default: `--max` of the session (issues per session, as above) and `--parallel` of the dispatcher (sessions at the same time).

- **At most one session per repository.** The session works in the repository's checkout (found under `$CODING_ROOT`, else a fresh clone, as in step 2 of the skill) and the worker switches its branches; a second session there would disrupt the first. The instance name is the repository, so systemd itself never runs two of the same.
- **`--parallel` defaults to 1.** Each session can cost Opus planner runs, Sonnet worker runs and an Opus review round, and parallel sessions draw on the same usage limit; with 1 the dispatcher behaves like the single unit, except that a repository whose session is still running no longer holds back the others' next tick. Raise it as a decision about usage and about unattended merges per hour, together with `--max` and the timer.

`scripts/dispatch.py [--parallel <n>] [--template work-issue@.service] [--dry-run] [--owner <login>]... [<owner>/<repo>]...` (call it by its full path; its docstring has the output) runs `has-work.py --all` with the same scope, which prints one `WORK` line per repository with work (the same rules as the single check, read to the first hit of each repository) and an `ERROR` line for a repository whose check failed. It counts the running instances of the template (`systemctl --user list-units 'work-issue@*.service'`, states `activating`, `active`, `deactivating`, `reloading`; a oneshot unit is `activating` from its `ExecCondition=` to the end of its session), skips a repository whose instance runs (it keeps its slot), and starts the others with `systemctl --user start --no-block` while slots are free, in the order of the oldest issue. The rest waits for the next tick. An `ERROR` line does not stop the others: they are started, and the dispatcher then exits 1, so its unit shows as failed and the journal names the repository and the reason. A failure that belongs to no repository (the search, a bad argument, `gh` missing) starts nothing and exits 255.

`~/.config/systemd/user/work-issue@.service` (the instance name is the repository escaped by `systemd-escape`; `%I` gives `<owner>/<repo>` back):

```ini
[Unit]
Description=Work auto-work issues of %I

[Service]
Type=oneshot
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin
Environment=CODING_ROOT=%h/<directory with your checkouts>
# one temporary directory per repository: the clean-up below must not remove files of another instance that is running
Environment=TMPDIR=%h/.cache/work-issue/tmp/%I
ExecStartPre=/usr/bin/mkdir -p %h/.cache/work-issue/tmp/%I
ExecStartPre=-/usr/bin/find %h/.cache/work-issue/tmp/%I -mindepth 1 -mtime +7 -delete
WorkingDirectory=%h
# checks again: the dispatcher's answer can be minutes old, and an instance started by hand gets the same check
ExecCondition=%h/.claude/plugins/marketplaces/tehw0lf-claude-skills/plugins/work-issue/skills/orchestrate-issue/scripts/has-work.py %I
ExecStart=%h/.local/bin/claude -p "/work-issue:orchestrate-issue --max 1 %I" --permission-prompts none --permission-mode acceptEdits --add-dir %h/.cache/work-issue/tmp/%I --settings %h/.config/work-issue/settings.json
```

`~/.config/systemd/user/work-issue-dispatch.service` (`systemctl` and `systemd-escape` are in `/usr/bin`; `gh` and `python3` must be on the `PATH` as for the check):

```ini
[Unit]
Description=Start a work-issue@ instance per repository with auto-work issues

[Service]
Type=oneshot
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin
ExecStart=%h/.claude/plugins/marketplaces/tehw0lf-claude-skills/plugins/work-issue/skills/orchestrate-issue/scripts/dispatch.py --parallel 1 --owner <login>
```

`~/.config/systemd/user/work-issue-dispatch.timer`: the timer of the single unit with `Description=Run work-issue-dispatch.service hourly`. Switch over in this order, so that the old unit and an instance never work the same repository: `systemctl --user disable --now work-issue.timer`, wait until `systemctl --user is-active work-issue.service` no longer says `activating`, then `systemctl --user daemon-reload` and `systemctl --user enable --now work-issue-dispatch.timer`. The dispatcher does not see the old unit.

What runs and what it did:

```bash
systemctl --user list-units 'work-issue@*'                 # the running and failed instances, one per repository
journalctl --user -u work-issue-dispatch.service           # START, SKIP and ERROR lines of each tick
journalctl --user -u 'work-issue@*'                        # the sessions' reports
```

The dispatcher is itself a oneshot unit, so two ticks do not overlap. An instance's `ExecCondition=` (`has-work.py <owner>/<repo>`, the single check, without `--all`) keeps its meaning: exit 1 skips the instance, 255 marks that instance failed. Run `dispatch.py --dry-run` with the unit's arguments once by hand: it prints what a tick would start and starts nothing. Tried with stubs for `gh`, `issue-context.py` and `systemctl` (two repositories with work, one failing, a running instance, a full limit, a failed start, a search over two pages) and with `--dry-run` against GitHub and the real `systemctl`; `systemctl --user` and `systemd-escape` were run inside a transient user service (systemd 262) and answered there. Not checked: a tick that starts two sessions at once and what they cost, and `has-work.py --all` against an account with more than one repository with work.

#### cron

Not run; a sketch of the same rules for a machine without systemd. Use one lock file per repository and `flock -n`, which gives up when the lock is held, so a repository never gets a second session; count the held locks for the limit. The repositories with work come from `has-work.py --all` (only `WORK` lines; an `ERROR` line is for a person to read):

```bash
#!/bin/bash
# crontab: */10 * * * * <this script> >> ~/.cache/work-issue/cron.log 2>&1
S=~/.claude/plugins/marketplaces/tehw0lf-claude-skills/plugins/work-issue/skills/orchestrate-issue/scripts
LOCKS=~/.cache/work-issue/locks; PARALLEL=1; mkdir -p "$LOCKS"
running() { local n=0 f; for f in "$LOCKS"/*.lock; do [ -e "$f" ] && ! flock -n "$f" true && n=$((n+1)); done; echo "$n"; }
"$S/has-work.py" --all --owner <login> | while IFS=$'\t' read -r kind ref _; do
  [ "$kind" = WORK ] || { echo "$kind $ref"; continue; }
  repo=${ref%%#*}; lock="$LOCKS/${repo//\//__}.lock"
  [ "$(running)" -lt "$PARALLEL" ] || { echo "SKIP $repo limit"; continue; }
  # same environment and arguments as ExecStart= of the template; nohup so that it outlives this script
  TMPDIR=~/.cache/work-issue/tmp/$repo CODING_ROOT=<directory> nohup flock -n "$lock" \
    sh -c "mkdir -p \"\$TMPDIR\" && \"$S/has-work.py\" '$repo' && claude -p '/work-issue:orchestrate-issue --max 1 $repo' --permission-prompts none --permission-mode acceptEdits --add-dir \"\$TMPDIR\" --settings ~/.config/work-issue/settings.json" >/dev/null 2>&1 &
  sleep 1  # let flock take the lock before the next count
done
```

## Cloud: routine

Verified: nothing. The body below follows the instructions of the `schedule` skill. It is unknown whether a routine's session has the `work-issue` and `pr-review` plugins (the session starts from a git checkout of the source repository and the connectors of the account), whether it has `gh` credentials for the issue and PR calls, and whether the nested subagents of the worker are allowed there. Without `pr-review:pr-reviewer` the worker does not merge. Try it with one test issue before relying on it.

A routine is not a file: it is a server-side object created in the web UI (claude.ai/code/routines) or by `/schedule`. Cron is UTC with a minimum interval of one hour; MCP servers configured only in Claude Code are not available, only claude.ai connectors. Shape of the create body:

```json
{
  "name": "work-issue <owner>/<repo>",
  "cron_expression": "0 * * * *",
  "enabled": true,
  "job_config": {
    "ccr": {
      "environment_id": "<environment id of the account>",
      "session_context": {
        "model": "claude-sonnet-5-5",
        "sources": [{"git_repository": {"url": "https://github.com/<owner>/<repo>"}}],
        "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Agent"]
      },
      "events": [{"data": {
        "uuid": "<lowercase v4 uuid>",
        "session_id": "",
        "type": "user",
        "parent_tool_use_id": null,
        "message": {"role": "user", "content": "/work-issue:orchestrate-issue --max 1 <owner>/<repo>"}
      }}]
    }
  }
}
```

`allowed_tools` here adds `Agent` to the skill's default list because the orchestrator spawns the planner and worker; whether the routine accepts it was not checked. The prompt has to be self-contained: the session starts with no context from any local conversation.
