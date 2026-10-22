## Merging

Merge only when all hold:

- the review of the **current** head has zero `blocking` and zero `fix-in-PR`
- the base branch requires at least one check, and every required check on the head has concluded **green** — not pending, not "failed but probably unrelated". Checks that are not required are neither waited for nor a merge condition, whatever their state. Reason: no merge condition depends on an account posting, or not posting, something; the required checks are named by the repository's own ruleset and enforced by the base branch. The wait for them is short and bounded (the commands below): when the prompt names the required checks and one of them is missing from `gh pr checks --required` or has not concluded when the wait ends, do not merge and do not wait longer, stop with the marker (reason `pending-check`, one sentence naming the checks that had not concluded). Reason: the runs are asynchronous; a check that takes longer than the wait (an installed app once reported ten minutes after the push) is picked up by the next run, which resumes the PR once every required check passes, instead of holding a session open or waiting for a person. When the prompt names no required checks and `gh pr checks --required` reports none (exit 1, "no required checks reported" or "no checks reported"), nothing is known to gate the merge: either the base branch requires none or a required check has not reported yet, and the command cannot tell which. Do not merge, stop with the marker (reason `awaiting-merge`, one sentence saying that no required check has reported on the head)
- the PR has no merge conflicts and the head is still the reviewed SHA
- every required check on the head started after the PR's latest base change (a retarget keeps the head SHA, and a `pull_request` workflow without the `edited` activity type does not run again, so green checks can come from the merge with the old base; checked as described below)
- the PR's base is the default branch (otherwise the change lands in another PR's branch, and the review looked at the diff against that branch), and no open PR in the repository has this PR's head branch as its base (the merge deletes the head branch, and a PR based on it would be closed or retargeted without anybody deciding it)
- every validation command of the skill's step 7 exited 0 locally on that head — a command that could not run is not a pass: report the open PR and what could not run
- the head is not behind the base where the base requires it to be up to date (below)

First the base, each as its own command:

```bash
gh pr view <PR> -R <owner>/<repo> --json baseRefName,headRefName    # baseRefName must equal the default branch
gh pr list -R <owner>/<repo> --base <headRefName> --state open --json number    # must be empty
```

Either condition failing means no merge and a stop with the marker (reason `other`, one sentence naming the base or the dependent PR numbers):

- **Base is not the default branch:** this PR is itself the stacked one. Report that a person decides: merge by hand, or retarget it to the default branch and get a new review, because the existing verdict covers the diff against the other branch while the retargeted diff also holds that branch's commits (the head SHA stays the same, so the verdict would still match it; the `BASE` line of the script below catches a retarget made after the verdict).
- **Dependent PRs exist:** report that a person retargets them to the default branch, or merges by hand, because `--delete-branch` would otherwise close or retarget them.

The check also covers a resumed PR, whose base this run never set; a stacked PR can only come from a person.

**A branch behind its base is updated, not left for a person.** Once the review of the current head is clean and the base checks above have passed (a stacked PR stops there, before anything is written to it), and before the required checks are read, run `gh pr view <PR> -R <owner>/<repo> --json mergeStateStatus -q .mergeStateStatus`. `UNKNOWN` (GitHub is still computing it, as after a change of the base) is read again, a few times over at most a minute; a state that stays `UNKNOWN` is a stop without a marker, like an `unknown` mergeable state, and the next run reads it again. Only `BEHIND` (the base requires the branch to be up to date and it is not) leads to an update; any other state goes on. Update with the base merged in, never rebased, as its own command:

```bash
gh api -X PUT repos/<owner>/<repo>/pulls/<PR>/update-branch -f expected_head_sha=<reviewed-sha>
```

`expected_head_sha` makes GitHub refuse the update when the head moved after the review. The update itself runs asynchronously: read `gh pr view <PR> -R <owner>/<repo> --json headRefOid -q .headRefOid` again, a few times over at most a minute, until it differs from the reviewed SHA. Then:

- a refusal because of a merge conflict → stop with the marker (reason `conflict`); any other refusal → stop with the marker (reason `merge-refused`, one sentence with the reason GitHub gave), both written for the reviewed head
- the head did not move within the minute → stop without a marker and report it: the update may still land, and the next run reads whatever head the PR has then
- the head moved → `git fetch origin`, then as its own command `git merge --ff-only --no-overwrite-ignore refs/remotes/origin/<headRefName>` on the PR branch. A refusal because an ignored local file would be overwritten is the same stop as the refused fast-forward in the skill's "Resume an own PR" (`resume.md`) (the marker reason `other` under "Stop marker", the paths in the report, the file neither moved nor deleted); any other refusal is reported and stops the run without a marker, nothing reset. Check that `git rev-parse HEAD` equals the new head. Record the untracked entries again into `status-after-switch.txt`, with the command of the skill's "Resume an own PR" (`resume.md`) (the base may have changed an ignore rule, and step 7 must not take a file that was there before for validation output), run the install of the skill's step 4 again (the base may have changed manifests or lockfiles, and step 4 installs only once), then the full validation of step 7. A failing command is not fixed here: stop with the marker (reason `other`, one sentence naming the command that fails on the head with the base merged in); a command that cannot run stops with the marker (reason `not-runnable`). Then get a review round of the new head as in "Independent review": a fresh reviewer without a model override, told about the earlier rounds and that the new head merges the base into the reviewed head. This round is not bound by the limit of 4, like the `BASE` round below; its `blocking` and `fix-in-PR` findings are handled as under "Independent review". Then every merge condition is checked again for the new head, from the base checks on, including a second `mergeStateStatus` read.

At most one update per run: a head that is `BEHIND` again at that second read stops without a marker, and the report says that the base moved again during the run. Nothing is wrong with the PR, and a marker would park it for a person; without one the next run resumes it, finds the review of the new head and updates once more. Reason for the update: with a base that requires an up-to-date branch, every merge into the default branch leaves every other open PR behind it, and a PR that waits for a person each time does not get merged unattended. A merge commit instead of `--rebase` keeps the push a fast-forward, so no reviewed commit is rewritten and the squash merge lands the same change; the new head is reviewed and checked like any other, so nothing unreviewed is merged. The checks of the new head often report later than the bounded wait, which ends in `pending-check` and the next run.

**If a required check is red, stop and report — do not merge** (marker reason `red-check`). Judging a failure "unrelated" is not your call: a scan can go red from a vulnerability-database refresh, and only a person decides to merge past that. Report the failing check and your reading of the cause.

```bash
gh pr checks <PR> -R <owner>/<repo> --required    # a named check missing here has not reported: stop with pending-check now, a watch cannot see it
timeout 300 gh pr checks <PR> -R <owner>/<repo> --required --watch    # only while a listed check is pending; at most five minutes (exit 124: the time ran out)
gh pr checks <PR> -R <owner>/<repo> --required    # confirm: every check pass goes on; fail or cancel stops with red-check, skipping with awaiting-merge (a person decides whether a skipped required check is enough), missing or pending with pending-check when the prompt names the required checks, else with awaiting-merge
gh pr checks <PR> -R <owner>/<repo>    # once, for the report only: name each check that is not required and red or still pending
gh pr view <PR> -R <owner>/<repo> --json headRefOid -q .headRefOid    # must equal the reviewed SHA
```

Run the `timeout 300` watch with the Bash tool's own `timeout` parameter set to `330000` (milliseconds): the tool ends a call after 120 seconds by default, which would cut the wait short. A watch that ends without its own exit code (the tool stopped it) counts like exit 124: the confirming listing after it decides.

Then read the verdict and base facts. This is the last check before the merge, after the required checks are green. `<skill directory>` is the `work-issue` skill directory named in the first line of the preloaded skill.

```bash
<skill directory>/scripts/merge-facts.py <owner>/<repo> <PR>
```

It prints the latest verdict of this account (`VERDICT`, with the head it reviewed), the current head (`HEAD`), one `BASE` line (created time, previous base, current base) for each change of the PR's base branch at or after the verdict, and, whatever the verdict, one `LASTBASE` line (created time) for the newest base change of the whole timeline (none when the base never changed). Act on the result:

- exit code other than 0 or a `SKIPPED` line → report, no merge: a source that could not be read leaves the condition unverified
- the `VERDICT` head differs from the reviewed SHA → report, no merge
- `BASE` lines → the latest verdict was made against the diff of another base, and it binds only the head, so the retargeted PR passes a head comparison with a review that never saw its current diff. Get a further review round as in "Independent review": a fresh reviewer without a model override, told about the earlier rounds and that the base changed after the previous verdict (from, to), so that it reviews the current head against the current base as a whole. It is not bound by the limit of 4 rounds; each further round needs another retarget. The worker opens every PR against the default branch, so only a person causes a base change, just as only somebody else's push or the worker's own branch update causes the resume round that is exempt in the same way. Its `blocking` and `fix-in-PR` findings are handled as under "Independent review" (stop, or fix and push). The same holds on a resumed PR (see "Resuming an own PR" for the limit). Afterwards check all merge conditions again for the new verdict, including this script
- a `LASTBASE` line → the required checks must postdate it. Read their names with `gh pr checks <PR> -R <owner>/<repo> --required --json name` and their start times with `gh pr view <PR> -R <owner>/<repo> --json statusCheckRollup` (`startedAt` of each entry, matched by `name` for a check run or `context` for a commit status; its `startedAt` is the time it was created, whereas `gh pr checks --json startedAt` reports the zero date for it) and stop with the marker (reason `other`, one sentence naming the checks that predate the base change) when any required check's `startedAt` is before or equal to the `LASTBASE` time, or is empty, unreadable, missing from the rollup or the zero date `0001-01-01T00:00:00Z` (unverified): that check ran, or may have run, against the merge result of the old base. The line stays after the `BASE` round and in every later run, because it ignores the verdict. Every required check counts, also one that does not depend on the base. Only a person decides how to get checks on the current base (a new event, or a manual merge). The condition applies again after each round above, with the script run again.

Windows stay open. A retarget between the reviewer's last read of the base and the posting of the verdict is caught by nothing. A required check that has not reported yet is not listed by `--required`, so the wait cannot see it (`orchestrate-issue` closes this by naming the required checks in the worker's prompt, and a named check that is missing ends the run with `pending-check`). Three cases of the check-start rule stay open, because `startedAt` is when a job started or a status was last posted, not when its event happened: a run triggered before the retarget whose jobs only start after it (waiting for a runner or in a concurrency group), and a re-run after the retarget, which keeps its original event, and an external check that started before the retarget and posts its result as a commit status afterwards (the status's `startedAt` is the time of its last state). All three pass the rule although they may have tested the old base. And a person can retarget the PR in the seconds between this script and the merge; `--match-head-commit` covers the head only, not the base (the base check above runs earlier).

Right before it, read `gh pr view <PR> -R <owner>/<repo> --json mergeStateStatus -q .mergeStateStatus` once more: the base can move during the check wait and the script. `BEHIND` there is a stop without a marker, and the report says that the base moved after the checks; the next run resumes the PR and updates it (a branch update made in this run still counts as the one per run). Then, as one plain command — not in a loop or a compound command:

```bash
gh pr merge <PR> -R <owner>/<repo> --squash --delete-branch --match-head-commit <reviewed-sha>
```

`--match-head-commit` makes the merge fail if the head moved after the review, so nothing unreviewed can ride along. A merge that is refused (a missing token scope for workflow files, branch protection) is reported with its message, not worked around. A refusal because the head is not up to date with the base (the base moved in the seconds after the last read) is a stop without a marker, like `BEHIND` above; any other refusal stops with the marker (reason `merge-refused`).

After the merge, check that the issue is closed and, where the default branch runs a pipeline on push, report that run's state without waiting longer than it normally takes. Then the checkout goes back as under "Back to the starting branch".
