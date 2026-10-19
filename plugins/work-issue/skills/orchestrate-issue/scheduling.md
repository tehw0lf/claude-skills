# Running orchestrate-issue on a schedule

`orchestrate-issue` schedules nothing itself: one call takes the issues that are ready, up to its limit (one unless `--max` is given), and an issue that waits for an owner answer ends that run at its questions. To pick up `auto-work` issues without a person starting each run, something outside the skill has to call it. This file describes two ways. Neither is part of the plugin; they are examples.

## What every trigger has to respect

- Run it as a main session. A headless `claude -p` run is one; a subagent is not (the worker needs its spawn depth for `pr-review:pr-reviewer`).
- Name the scope: a repository, `/work-issue:orchestrate-issue <owner>/<repo>`, or an account or organisation, `/work-issue:orchestrate-issue <owner>`, which covers every repository of it with an open `auto-work` issue (found by an issue search, archived repositories left out). The skill finds local checkouts only under `$CODING_ROOT` (default `~/Nextcloud/Coding`), not from the working directory, and a systemd service does not inherit the shell's environment: set `Environment=CODING_ROOT=<directory>` in the unit, or pass `--checkout <owner>/<repo>=<path>`. Otherwise every run clones, which is the path the verified run did not take (see below); with an account as scope that holds for every repository that has no checkout there. Without an argument the skill takes the repository of the current directory, never an account. The example below starts in the home directory, which is not a checkout, and a checkout used as working directory can be left on a feature branch by the worker, so the scope is named explicitly.
- The label `auto-work` is the consent to an unattended merge. It is not created by the skill; a missing label finds nothing. Only a labeler with `write`, `maintain` or `admin` counts, and an issue that an account without write access ever edited, before or after the label, is skipped.
- An issue that is already started is continued by the next run: its own open PR goes back to the worker for validation, review and merge. When the worker stops there, it leaves a comment starting with `<!-- work-issue:stopped … -->` on the PR, and later runs report the PR as waiting for a person and spawn nothing until somebody pushes to it or comments on it with admin, maintain or write permission. The answer goes on the PR: a comment on the issue makes the plan stale (`owner-comment`), and the PR is then reported as an own PR on a plan that is no longer current instead of being continued.
- One issue per run unless `--max` is given. A trigger that runs often with a high `--max` raises the number of unattended merges per day; choose both together. Each reviewed PR can also leave follow-up issues without `auto-work`, which wait for a person, so the same two settings set how fast that backlog grows.
- Where the default branch has no required status check, or the checks could not be read, the worker stops at an open, reviewed PR and does not merge. That is by design.
- One trigger per repository, and none next to an account-wide one: a trigger whose scope is an account or organisation covers every repository in it. Two runners (a timer and a routine, or two machines) on the same account are not guarded against each other, also not when their scopes overlap: the assignee check only stops on an assignee other than the account itself.
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
Description=Work auto-work issues of <owner>/<repo> (or of <owner>)

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

Without a check every tick starts a full session, also when no issue carries `auto-work`; such a run costs tokens and, once the usage limit is reached, ends as a failed unit. `scripts/has-work.py <owner | owner/repo>...` answers the same question without a model: is there an open issue with the label whose newest questions of the account are not still open? Give it the same scope as the session's call (`<owner>/<repo>` or `<owner>`). For an `<owner>` it reads one issue search instead of a repository listing: the search index can lag behind a label set just before the call (found by the next tick), and a search that reports itself incomplete or holds more than 1000 results is a failed check, exit 255. An issue without `needs-decision` is a hit; one with it is a hit unless `issue-context.py` shows `QUESTIONS … open`. The planner removes the label only after it has read the answer, so a test on the label alone would skip an answered issue for good, and the session itself decides by the `QUESTIONS` and `PLAN` lines, not by the label. It lists issues and, for a waiting one, runs `issue-context.py`; it writes nothing.

By `systemd.service(5)` an `ExecCondition=` command that exits 1 to 254 skips the remaining commands and the unit is not marked failed, 255 or an abnormal end marks it failed, and 0 goes on. The script exits 0 on a hit, 1 on none, and 255 when the check itself failed (`gh` missing or not authenticated, GitHub unreachable, a time-out), so a check that breaks while it runs shows as a failed unit and starts no session. That includes `issue-context.py` reporting `# SKIPPED account`, `plan`, `comments` or `permission`, and any unexpected error in the script. A script that systemd cannot start is different: with a path that does not exist, or `python3` not on the unit's `PATH` (the shebang is `#!/usr/bin/env python3`; `env` then exits 127), systemd ends the `ExecCondition=` as `exec-condition`, which skips the unit without a failure (tried in transient user units; systemd 262). Every tick would then be skipped and nothing would report it. The path in the example exists only once the marketplace is registered under that name and `claude plugin marketplace update` has pulled this version. So run the exact `ExecCondition=` command line by hand once (it prints `WORK` or `NONE`), and after the first ticks look in the journal for "Skipped due to 'exec-condition'" next to a `NONE` you expect, not next to an error.

If the unit's session uses another label, pass the same one to the script with `--label`.

The unit above points at the marketplace clone, which follows `main` after `claude plugin marketplace update`; the plugin cache is keyed by version and the path would go stale at each update. `gh` and `python3` must be on the unit's `PATH`, and the `ExecStartPre=` lines run after the check.

It is the simple test of the selection, not the whole of it. These cases pass the check and the session then reports them and spawns nothing, as before: a labeler without write access, an edit by a non-writer, an assignee other than the account, a pull request that already closes the issue (also one waiting for a person under a stop marker), a PR with a conflict. The reverse case does not occur: an issue the session would work is not skipped here, because the check does not read the label alone (an issue with `needs-decision` and no questions of the account, for example a label set by hand, is a hit). A label set so recently that the label-filtered listing lacks it is found by the next tick. Not checked: a full scheduled run of the unit, an `<owner>` argument against an organisation with its own repositories, private repositories in the search; the script was run by hand against GitHub (no hit, a failing repository, bad arguments) and with a stubbed `gh` (hit kinds, a failing `gh api user`, a `null` listing); the open-questions case only with a stubbed `issue-context.py` output.

A systemd user manager runs only while the account has a login session, unless lingering is on: check with `loginctl show-user "$USER" -p Linger`, enable with `loginctl enable-linger "$USER"` if runs must continue while logged out. Without it the hourly run does not happen while logged out, and `Persistent=true` catches up only once at the next login. A run without a login session was not tested, and neither was whether `gh` and Claude Code find their credentials there.

A oneshot service does not start a second run while the first is still going, so a long run does not overlap itself.

To test the timer without waiting for the full hour, add `~/.config/systemd/user/work-issue.timer.d/test.conf` with `[Timer]` and `OnActiveSec=30s`, run `systemctl --user daemon-reload`, then `systemctl --user restart work-issue.timer`. Remove the drop-in afterwards. A oneshot service stays `activating` while it runs, so wait on `ActiveState` leaving `activating` (`systemctl --user is-active` reports it as not active and ends a wait loop at once).

### Permissions

With `--permission-prompts none` anything that would prompt is denied, and nobody can answer. The first runs showed which steps need a rule, in this order: the planner agent was denied the `plan-issue` skill; then the orchestrator's re-check, `issue-context.py` piped through `grep`, was denied as a compound command. `--settings` loads a file in addition to the user's settings, so the rules apply to this run only. The list below is the file `~/.config/work-issue/settings.json` that completed the run above, plus fourteen rules; the transcripts of that run show no denied tool call, so the file was sufficient for it. It was not reduced to a minimum, and which rules were exercised was not counted. Fourteen added rules do not come from a run: `Bash(gh label create needs-decision *)` comes from the last step of `plan-issue` (creating the `needs-decision` label when a plan ends in open questions and the repository has none), and `Bash(*check-lock-overrides.py*)` from step 5 of `work-issue` (the check for uv, Cargo, Go and Composer, which a run that touches no dependencies never calls), `Bash(git -c core.quotePath=false ls-files*)` from step 2 and the stop clean-up of `work-issue`, which list ignore files with that form (the prefix rule `git ls-files*` does not match it), and five rules for the commands of the stop clean-up and of step 5 of `work-issue` that no rule covered: `Bash(git reset*)` and `Bash(git clean*)` (the stop clean-up and its resume-mode form; `git clean` also removes the untracked entries of step 7 of `work-issue` as `git clean -f -- ':(literal)<path>' …` and the lockfile of step 6 as `git clean -f -- package-lock.json`, which is read from the files, not seen in a run, and no `rm` rule is needed), `Bash(LC_ALL=C comm *)` (the stop clean-up), `Bash(git check-ignore*)` (`git check-ignore -q` of step 5) and `Bash(git -c core.quotePath=false check-ignore*)` (the stop clean-up; as for `ls-files`, the prefix rule does not match the `-c` form). These five are read from the files, not seen in a run, and so are the next two: `Bash(git rev-list*)` (`git rev-list --count origin/<branch>..<branch>` in resume mode, step 1 of `work-issue`; the documentation does not say whether Claude Code counts `git rev-list` among its built-in read-only git forms, so it has its own rule) and `Bash(git restore*)` (`git restore -- ':(literal)<path>' …` in step 7). The last four are read from the files as well: `Bash(mktemp -d*)` (the temporary directory of `work-issue`, `plan-issue`, both agents and `pr-review:pr-reviewer`, run as a plain command), `Bash(git stash create)` (the snapshot of the tracked content before and after the version bump in step 6 of `work-issue`; the exact rule does not open `git stash drop`, `clear` or `pop`, which `git stash*` would, and the skill runs it as the bare command because an exact rule does not match text after it), `Bash(git -c core.quotePath=false status*)` (steps 1, 2 and 7 of `work-issue` list untracked entries with that form; as for `ls-files`, the prefix rule `git status*` does not match it) and `Bash(LC_ALL=C grep *)` (the comparison of the status snapshots in step 7; the list has `LC_ALL=C comm *`, and which variables the matcher strips before matching is not documented). The questions path and the close path (the planner's `gh issue close` on an owner's answer) of a scheduled run were not exercised:

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
      "Bash(git check-ignore*)", "Bash(git -c core.quotePath=false check-ignore*)", "Bash(git reset*)", "Bash(git clean*)", "Bash(git restore*)", "Bash(git worktree*)", "Bash(git cat-file*)", "Bash(git stash create)", "Bash(mktemp -d*)",
      "Bash(ls*)", "Bash(cat *)", "Bash(grep *)", "Bash(jq *)", "Bash(head *)", "Bash(tail *)",
      "Bash(wc *)", "Bash(sort *)", "Bash(cut *)", "Bash(LC_ALL=C comm *)", "Bash(LC_ALL=C grep *)"
    ]
  }
}
```

The stop clean-up of `work-issue` chains commands: `git reset --hard && git clean -fd` and three forms of `git add -N … && git diff HEAD >> <temporary directory>/abandoned.diff` (for `.`, for the listed paths and, with `-f`, for the selected paths). By the Claude Code permission documentation (section "Compound commands"; read there, not run with the matcher) a command is split at `&&`, `||`, `;`, `|`, `|&`, `&` and newlines, and each part has to match an allow rule on its own, so these lines are covered by `Bash(git reset*)`, `Bash(git clean*)`, `Bash(git add*)` and `Bash(git diff*)` and are not split in the skill. The same documentation (section "Redirections") says the target of `>` or `>>` is checked against `Edit` rules and the working directories independently of the Bash rule. The unit sets `TMPDIR` to a dedicated directory and passes it with `--add-dir`, so `mktemp -d` creates the skills' temporary directories there. By the same documentation (read there, not run with the matcher) that makes the directory a working directory, which covers: output and input redirects into it, the files the agents write with the file tools (`--body-file` files, `comment.md`), `cp` of test files into the base worktree under it, and `cd` into it. Not covered: `cd` combined with `git` still prompts by the documentation, so the `<command>` inside a `( cd <path> && pwd && <command> )` subshell must not be `git`; the skills create and remove the base worktree with `git worktree` from the checkout, which stays the working directory. The same limit reaches a `pr-review:pr-reviewer` spawned by an unattended `issue-worker`: with this list the plain commands for the throwaway repository with commits that its instructions describe (`git init` has no rule, and `git` inside the `cd` subshell prompts) and for a nested agent session (no rule allows `claude`) are denied under `--permission-prompts none`, and those checks belong under `Not covered` in its verdict. The interpreter rules above (`python3`, `uv`, `node`, `npx`) would let wrapped forms of the same commands through, but the reviewer is told not to retry a denied check in another form, and that is no reason to rely on them. The list gets no rule for them: one for git in an arbitrary directory (for example a `git -C <path>` form) would be one of the broadest in the list, and is not part of it. This too is read from the documentation, not run with the matcher. For `mktemp -d -p <scratch directory>`, used when a session was given a scratch directory: Claude Code's own temporary directory defaults to `os.tmpdir()` on Linux, which follows `TMPDIR` when it is set unless `CLAUDE_CODE_TMPDIR` says otherwise, so such a scratch directory may lie inside the `--add-dir` directory; whether a headless session is given one, where, and whether it is writable without a rule is not verified. The place to confirm all of this is the transcripts of the verified run, or a probe run with this list.

This list is broad, and these are the rules that carry the risk. The token the run uses decides what a hijacked run can reach: give it only the repositories it works on and no `workflow` or administration scope, and keep unattended runs away from keys and checkouts they do not need (a container or a separate user account). Everything in the list runs as the user that starts the session.

- `Bash(python3 *)`, `Bash(uv *)`, `Bash(node *)` and `Bash(npx *)` each allow arbitrary code (`python3 -c …`, `uv run …`, `node -e …`), which is close to bypassing the permission check for Bash. `Bash(npm *)` runs the repository's scripts.
- The script rules (`Bash(*issue-context.py*)` and the other five) have a wildcard on both sides, so they match any single command that contains the file name anywhere. Anchor them on the script path to match only the script.
- `Bash(gh api *)` allows any GitHub API call the token can make, including merging a PR through the REST endpoint, which skips the worker's `--match-head-commit` guard, and deleting branches. A narrowed list must still allow the `gh api` calls the skills and agents run as their own command, written here as their files write them (read from the files, not seen in a run): `gh api repos/<owner/repo>/issues/comments/<id> -q .body` (the plan comment, in `plan-issue` and `work-issue`), `gh api repos/<owner/repo>/labels/needs-decision` (the label check of `plan-issue`) and, in `pr-review:pr-reviewer`, `gh api --paginate repos/<owner>/<repo>/issues/<n>/comments`, `gh api --paginate repos/<owner>/<repo>/pulls/<n>/reviews` and `gh api --paginate repos/<owner>/<repo>/pulls/<n>/comments`; the flag comes before the path, so a rule written on the path alone does not match the reviewer's calls. The helper scripts make their own `gh api` calls inside the script; whether the script rules cover them was not tested. A narrowed rule that contains a `*` (the plan comment read and the reviewer's three calls have a variable `<id>` or `<n>`, so each needs one; the label check can be one exact rule per repository) does not pin the method or the endpoint: by the Claude Code permission documentation (read there, not tried in a run) a `*` in a Bash rule stands for any text, including `/`, spaces and further flags, so such a rule still allows another method (`-X`/`--method`, or a `-f`/`-F` parameter, which switches `gh api` to `POST` unless `--method` sets another method) and other paths under its literal prefix. `--paginate` in the rule does not close the gap, although `gh api` rejects it together with an explicit method other than `GET`: checked with gh 2.102.0 against a non-resolvable host with `--verbose` (no request reached GitHub), `--paginate -X PUT <path>` is rejected, but `--paginate -f body=x <path>` and `--paginate -F per_page=1 <path>` send `POST`, and a later `--paginate=false` lets `-X PUT` through (`--paginate <path> --paginate=false -X PUT -f a=/comments` sends `PUT <path>`, so it still ends in the tail of the reviewer's call). Whether the permission matcher accepts such commands for a given rule was not run. Only a rule without a `*`, one exact command, fixes the method; a list that keeps wildcard rules accepts that gap. This list does not replace the run-once-and-read-the-denial test.
- `Bash(gh issue *)` alone allows `gh issue edit --add-label`, and `Bash(gh api *)` alone allows the REST labels endpoint. `run-context.py` accepts a label added by an account with write access, and the running account has it, so a label the session adds itself counts as consent for the next run. Narrowing `gh api *` does not close this while `gh issue *` stays, and the worker needs `gh issue` for its follow-up issues. The planner also runs `gh issue close` under the close rule of `plan-issue`, and the permission allows closing any issue the token can reach. The skills tell the agents not to act on issue text, so this is not a defect in them, but the permission list does not stop it.
- `Bash(git reset*)` and `Bash(git clean*)` allow more than the clean-up needs, wherever the session runs git: `git clean*` also matches `git clean -fdx` and `-X`, which delete ignored files (installed dependencies, build output, an ignored `.npmrc` or `.env` holding a token), and `git reset*` also matches `git reset --hard <ref>`, which drops local commits from the branch, not only uncommitted changes. The skill only runs `git reset --hard` without an argument, so the exact rule `Bash(git reset --hard)` would cover its uses; the skill gates the other forms, the list does not. The skill's own uses of `git clean` are `git clean -f -- ':(literal)<path>' …` (the stop clean-up and step 7; step 6 names a fixed file) and `git clean -fd` and `git clean -fx -- ':(literal)<path>' …` (the stop clean-up); the rule still allows the broader forms.
- `Bash(git restore*)` also matches `git restore --source=<ref>` and whole-tree restores (`git restore .`), which discard uncommitted work; the skill restores only single files, each as a `:(literal)` argument, that the change did not touch on purpose (step 7), and the list does not gate the other forms.
- `--add-dir` on the temporary directory grants read and write access to it for the whole session, and `TMPDIR` also moves the temporary files of other tools (npm, pytest and the like) and, by default, Claude Code's own temporary files there; the unit removes entries older than seven days before each run (`find … -delete`, which can leave a stale registration of a deleted base worktree in the checkout: `git worktree prune` removes it), nothing else cleans the directory, and the seven days are not tuned to the growth, which was not measured.
- `Bash(git push*)` and `Bash(gh pr *)` are what lets the worker push and merge unattended.

Narrow them to what the target repository needs; no narrowed list was run, so a narrower file needs the same run-once-and-read-the-denial test. `--permission-mode acceptEdits` lets the worker edit files. A mode that bypasses all checks combined with an unattended merge is a decision about that repository's risk, not a default. The list was built for one repository (an npm project); another ecosystem needs its own tool rules, found the same way: run once, read the denial, add one rule.

### What the run reads and leaves behind

- Instruction files, read from the transcripts of the run: the main session (started in `%h`) loads only the user-level `~/.claude/CLAUDE.md`. The `issue-worker` and, one level below it, the `pr-review:pr-reviewer` both received the `CLAUDE.md` files of the checkout's parent directories as `nested_memory` attachments once they worked in the checkout. So the rules of the checkout's location apply to the worker and the reviewer. The planner's transcript was not inspected.
- The checkout's own `CLAUDE.md`: the repository of that run has none, so its transcripts cannot show it, and the worker's commands there came from `repo-commands.py`, not from an instruction file. A separate probe, a headless `claude -p` started in `%h` that used the `Read` tool on a file inside a repository that has a root `CLAUDE.md`, received that file as a `nested_memory` attachment next to the parent directories' files. So a repository's `CLAUDE.md` is loaded once a session reads a file there. The probe used `Read`; whether a session that only runs shell commands in the checkout gets it was not tested.
- Where the transcripts are: `~/.claude/projects/<working directory with / as ->/<session id>.jsonl`, with the agents under `<session id>/subagents/` (`*.meta.json` names the agent type and spawn depth, and the model when the spawn set one). A headless run writes them like an interactive one; this is where a denied tool call or a loaded instruction file shows up when the journal only has the final report.
- Checkout: the orchestrator uses a clean checkout on the default branch, else it clones. After a merged PR the found checkout was left on the worker's feature branch, so the next run does not use it and clones instead; switch it back to the default branch if you want it reused. The clone path (`gh repo clone` into a scratch directory, the worker editing and installing there under `acceptEdits`) was not exercised by the verified run, so make the first test a second run, or leave the hourly timer off until one has gone through it.

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
