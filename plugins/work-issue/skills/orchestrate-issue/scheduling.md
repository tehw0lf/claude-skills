# Running orchestrate-issue on a schedule

`orchestrate-issue` schedules nothing itself: one call takes the issues that are ready, and an issue that waits for an owner answer ends that run at its questions. To pick up `auto-work` issues without a person starting each run, something outside the skill has to call it. This file describes two ways. Neither is part of the plugin; they are examples.

## What every trigger has to respect

- Run it as a main session. A headless `claude -p` run is one; a subagent is not (the worker needs its spawn depth for `pr-review:pr-reviewer`).
- Name the repository: `/work-issue:orchestrate-issue <owner>/<repo>`. Without an argument the skill takes the repository of the current directory, which a timer or a routine does not control.
- The label `auto-work` is the consent to an unattended merge. It is not created by the skill; a missing label finds nothing. Only a labeler with `write`, `maintain` or `admin` counts, and an issue edited by anyone else after the label is skipped.
- Three issues per run unless `--max` is given. A trigger that runs often with a high `--max` raises the number of unattended merges per day; choose both together.
- Where the default branch has no required status check, or the checks could not be read, the worker stops at an open, reviewed PR and does not merge. That is by design.
- One trigger per repository. Two runners (a timer and a routine, or two machines) on the same account are not guarded against each other: the assignee check only stops on an assignee other than the account itself.

## Local: systemd user timer

Verified to start the skill headlessly: not yet. Check it once with a labelled test issue and `--max 1` before leaving it on.

`~/.config/systemd/user/work-issue.service`:

```ini
[Unit]
Description=Work auto-work issues of <owner>/<repo>

[Service]
Type=oneshot
# gh, git, uv and claude must be on this PATH
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin
WorkingDirectory=%h
ExecStart=/usr/bin/claude -p "/work-issue:orchestrate-issue --max 1 <owner>/<repo>" --permission-prompts none
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

A oneshot service does not start a second run while the first is still going, so a long run does not overlap itself.

Permissions are the open point. With `--permission-prompts none` anything that would prompt is denied, so the run needs `permissions.allow` rules in a settings file it loads that cover the git, `gh` and validation commands of the target repository and the `Agent` tool, or a permission mode that does not prompt (`--permission-mode`, see `claude --help`). Which rules are enough was not determined. A mode that bypasses all checks combined with an unattended merge is a decision about that repository's risk, not a default.

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
