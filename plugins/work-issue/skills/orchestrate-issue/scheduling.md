# Running orchestrate-issue on a schedule

`orchestrate-issue` schedules nothing itself: one call takes the issues that are ready, and an issue that waits for an owner answer ends that run at its questions. To pick up `auto-work` issues without a person starting each run, something outside the skill has to call it. This file describes two ways. Neither is part of the plugin; they are examples.

## What every trigger has to respect

- Run it as a main session. A headless `claude -p` run is one; a subagent is not (the worker needs its spawn depth for `pr-review:pr-reviewer`).
- Name the repository: `/work-issue:orchestrate-issue <owner>/<repo>`. Without an argument the skill takes the repository of the current directory. The example below starts in the home directory, which is not a checkout, and a checkout used as working directory can be left on a feature branch by the worker, so the repository is named explicitly.
- The label `auto-work` is the consent to an unattended merge. It is not created by the skill; a missing label finds nothing. Only a labeler with `write`, `maintain` or `admin` counts, and an issue edited by anyone else after the label is skipped.
- Three issues per run unless `--max` is given. A trigger that runs often with a high `--max` raises the number of unattended merges per day; choose both together.
- Where the default branch has no required status check, or the checks could not be read, the worker stops at an open, reviewed PR and does not merge. That is by design.
- One trigger per repository. Two runners (a timer and a routine, or two machines) on the same account are not guarded against each other: the assignee check only stops on an assignee other than the account itself.

## Local: systemd user timer

Verified once, on one machine: a timer started the service, the skill took one `auto-work` issue, the planner posted its plan, the worker opened a PR, the independent review ran on Opus and the PR was squash-merged with both required checks green. The run took about seven minutes. Run it once the same way (a labelled test issue, `--max 1`, the 30-second drop-in below) before leaving the hourly timer on.

`~/.config/systemd/user/work-issue.service`:

```ini
[Unit]
Description=Work auto-work issues of <owner>/<repo>

[Service]
Type=oneshot
# gh, git and python3 must be on this PATH (the skills' scripts start with `#!/usr/bin/env python3`), uv and node/npm only for a target that uses them; a per-user Node install is on none of these directories, add it. Adjust the path of claude (`command -v claude`)
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin
WorkingDirectory=%h
ExecStart=%h/.local/bin/claude -p "/work-issue:orchestrate-issue --max 1 <owner>/<repo>" --permission-prompts none --permission-mode acceptEdits --settings %h/.config/work-issue/settings.json
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

A systemd user manager runs only while the account has a login session, unless lingering is on: check with `loginctl show-user "$USER" -p Linger`, enable with `loginctl enable-linger "$USER"` if runs must continue while logged out. Without it the hourly run does not happen while logged out, and `Persistent=true` catches up only once at the next login. A run without a login session was not tested, and neither was whether `gh` and Claude Code find their credentials there.

A oneshot service does not start a second run while the first is still going, so a long run does not overlap itself.

To test the timer without waiting for the full hour, add `~/.config/systemd/user/work-issue.timer.d/test.conf` with `[Timer]` and `OnActiveSec=30s`, run `systemctl --user daemon-reload`, then `systemctl --user restart work-issue.timer`. Remove the drop-in afterwards. A oneshot service stays `activating` while it runs, so wait on `ActiveState` leaving `activating` (`systemctl --user is-active` reports it as not active and ends a wait loop at once).

### Permissions

With `--permission-prompts none` anything that would prompt is denied, and nobody can answer. The first runs showed which steps need a rule, in this order: the planner agent was denied the `plan-issue` skill; then the orchestrator's re-check, `issue-context.py` piped through `grep`, was denied as a compound command. `--settings` loads a file in addition to the user's settings, so the rules apply to this run only. The file `~/.config/work-issue/settings.json` that completed the run above; the transcripts of that run show no denied tool call, so the list is sufficient for it. It was not reduced to a minimum, and which rules were exercised was not counted:

```json
{
  "permissions": {
    "allow": [
      "Skill(work-issue:plan-issue)", "Skill(work-issue:work-issue)", "Skill(work-issue:orchestrate-issue)",
      "Agent(work-issue:issue-planner)", "Agent(work-issue:issue-worker)", "Agent(pr-review:pr-reviewer)",
      "Bash(*issue-context.py*)", "Bash(*run-context.py*)", "Bash(*bot-activity.py*)",
      "Bash(*repo-commands.py*)", "Bash(*check-install-config.py*)",
      "Bash(python3 *)", "Bash(uv *)", "Bash(npm *)", "Bash(npx *)", "Bash(node *)",
      "Bash(gh issue *)", "Bash(gh pr *)", "Bash(gh api *)", "Bash(gh run *)",
      "Bash(gh repo clone *)", "Bash(gh repo view *)",
      "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git show*)", "Bash(git fetch*)",
      "Bash(git switch*)", "Bash(git checkout*)", "Bash(git branch*)", "Bash(git add*)", "Bash(git commit*)",
      "Bash(git push*)", "Bash(git rebase*)", "Bash(git merge*)", "Bash(git rev-parse*)", "Bash(git remote*)",
      "Bash(git ls-files*)", "Bash(git worktree*)", "Bash(git cat-file*)",
      "Bash(ls*)", "Bash(cat *)", "Bash(grep *)", "Bash(jq *)", "Bash(head *)", "Bash(tail *)",
      "Bash(wc *)", "Bash(sort *)", "Bash(cut *)"
    ]
  }
}
```

This list is broad, and these are the rules that carry the risk:

- `Bash(python3 *)`, `Bash(uv *)`, `Bash(node *)` and `Bash(npx *)` each allow arbitrary code (`python3 -c …`, `uv run …`, `node -e …`), which is close to bypassing the permission check for Bash. `Bash(npm *)` runs the repository's scripts.
- The script rules (`Bash(*issue-context.py*)` and the other four) have a wildcard on both sides, so they match any single command that contains the file name anywhere. Anchor them on the script path to match only the script.
- `Bash(gh api *)` allows any GitHub API call the token can make, including merging a PR through the REST endpoint, which skips the worker's `--match-head-commit` guard, and deleting branches. Together with `Bash(gh issue *)` it can add the `auto-work` label; `run-context.py` accepts a label added by an account with write access, and the running account has it, so a label the session adds itself counts as consent for the next run. The skills tell the agents not to act on issue text, so this is not a defect in them, but the permission list does not stop it.
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
