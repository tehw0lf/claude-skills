**An own PR or pushed branch on a plan that no longer stands.** Read when step 1 of `plan-issue` sends you here: a `RESUME` or `PUSHED` line has a plan field that is not `-`, and the `PLAN` line is `stale`, missing, or current with another id that it does not adopt. The PR or branch is the account's own earlier work (a `PUSHED` plan id is set only when every commit carries the `Work-issue-plan` trailer of `work-issue` step 8; a `RESUME` plan id is the plan link in the body of a PR written by `ACCOUNT`). A plan written next to it that does not account for it would orphan it, because `work-issue` stops on such a branch or PR. The result of this run is therefore one of two comments, never a plain plan:

- a plan that **adopts** the PR or branch (the marker carries `adopts=<plan comment id>`), or
- a **questions** comment (`needs-decision`) that asks the owner.

You never close the PR, delete the branch or push to either.

### Judging

Take the newest comment of the plan the line names (read it with `gh api repos/<owner/repo>/issues/comments/<id> -q .body`) as the old plan. Judge why the `PLAN` line is stale or missing:

- **`issue-edited`:** plan from the current issue text as for any plan (steps 2 to 5 of `plan-issue`) and compare that plan with the old one. You do not need the earlier text and do not read the edit history.
- **`owner-comment`, `newer-questions`:** read the comments after the old plan through `--comments`; they are what changed.
- **`unknown-commenter`, or `OMITTED` above 0 where a comment may be relevant:** you cannot tell what changed. Do not adopt: post the questions comment.
- **A plan that is current by its comments but stale by code move** (step 1, the files under Changes moved), and **a current plan with a line on another id:** plan again and compare the same way. The replacement adopts the line's id.
- **No `PLAN` line:** plan as for any plan and compare with the old one.

Adopt only when both hold:

1. **The plans match in substance:** goal, cause, files and what changes in each, tests. Wording, base commit and line numbers may differ.
2. **The PR or branch implements the plan:** read `git diff refs/remotes/origin/<default>...refs/remotes/origin/<branch>` in the checkout (`gh pr diff <n>` for a PR; fetch first) and compare it with the **Changes** of the new plan. The head you judge is the head you name in the comment.

Otherwise ask.

**Several lines.** `adopts=` holds one id and `work-issue` step 1 stops on a second own branch. When there is more than one `RESUME` or `PUSHED` line with a plan id that is neither the current plan's nor the one it adopts, do not adopt: post the questions comment, name every line, and offer only option B.

### The adopting plan

A normal plan (step 6 of `plan-issue`) whose first line is `<!-- work-issue:plan base=<sha> adopts=<id> -->`, where `<id>` is the plan field of the line (the id the PR body or the branch trailers carry), never the id of the previous plan if the line is on another one. One level only: if the old plan itself adopts an earlier one, the new `adopts=` still names the id the line carries, and it is not followed further. The plan describes what the PR or branch implements, with the current base: goal, cause, files and tests as of the old plan where they still hold. **Decisions** names the PR or branch with its head and, after an owner's answer, links that answer.

`issue-context.py` prints the id as the last field of the `PLAN` line only when it is the id of an earlier plan comment of `ACCOUNT`; anything else prints `-` and the next run asks again. After the comment, `issue-context.py` must show `PLAN … current` with that id in the last field.

### The questions comment

The usual format (step 6: first line `<!-- work-issue:questions -->`, `## Decision needed`, origin line), and `needs-decision` as for any questions. It names:

- the branch, or the PR with its number, **and its full head sha**
- the plan it is on, as the full link `https://github.com/<owner>/<repo>/issues/<n>#issuecomment-<id>`
- what made the plan stale (the `PLAN` reason, or "the code under Changes moved", or "no current plan") and what you found that you could not settle

with these options, in this order:

- **A: adopt: it fits.** The owner says the PR or branch still does what the issue needs; the next run writes the adopting plan.
- **B: a person handles the branch or PR first** (finishes it by hand, closes the PR or deletes the branch), then answers B. The next run plans the issue as usual once the line is gone.

With several lines only B is offered.

### The answer

Under `QUESTIONS … answered`, read the questions comment of this file and the newest trusted comment after it (from `--comments`). Then:

- **A, and only A:** check that the line named in the questions still exists with the same branch or PR, the same head (compared by prefix) and the same plan id. If it does, write the adopting plan; **Decisions** links the answer. If it does not, post a new questions comment that says what changed.
- **B, and only B:** if the line is gone, plan the issue as usual. If it is still there, post a new questions comment that says so.
- **Anything else** (a changed head or plan id, a condition, both options, an answer you cannot read as A or B): post a new questions comment. The close-option rule of step 3 does not apply to these questions: they offer none.

At most one planner run per owner comment follows from this: every outcome above is one comment of this run, and it leaves the issue with either a current adopting plan or open questions.
