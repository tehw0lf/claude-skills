# Verdict layout

The layout of the verdict comment file of `agents/pr-reviewer.md`. The agent reads this file before it writes the verdict and follows it exactly.

Write the verdict as a Markdown comment file and return its path together with a short summary. A review with findings is long, and GitHub shows a comment in full, so only the decision is always visible and everything else folds into `<details>`:

```
## Independent local review

*[pr-review] verdict, written by the pr-reviewer agent*

Reviewed head: <full sha>

Verdict: <mergeable | not mergeable>

**Blocking: <n> · Fix in PR: <n> · Follow-up: <n>**

<details open>
<summary><b>Blocking (<n>)</b></summary>

<details>
<summary>1. <the finding in one line></summary>

<what is wrong, the evidence, the fix>

</details>

</details>

<details open>
<summary><b>Fix in PR (<n>)</b></summary>

<one folded <details> per finding, as above>

</details>

<details>
<summary><b>Follow-up (<n>)</b></summary>

<one folded <details> per finding, as above>

</details>

<details>
<summary><b>What was checked</b></summary>

<the checks, in Markdown>

</details>

<details>
<summary><b>Not covered</b></summary>

<what was not checked, in Markdown>

</details>
```

- **Always visible, outside every `<details>`:** the heading, the origin line, `Reviewed head`, `Verdict` and the count line. Whoever merges reads the decision without opening anything.
- **The first line stays exactly `## Independent local review`.** `merge-facts.py`, `issue-context.py` and `review-rounds.py` recognise a verdict by that first line alone, so the origin line goes below it and never replaces or prefixes it.
- **`Blocking` and `Fix in PR` are open** because they decide the verdict, but each finding inside them is folded behind a one-line summary: the list shows what is wrong at a glance, and the evidence opens on demand. `Follow-up`, `What was checked` and `Not covered` are folded.
- **An empty class is one plain line**, `**Blocking:** none`, instead of a `<details>` block with nothing in it; the same for `Not covered`. `What was checked` is never empty.
- **Two levels of `<details>` at most.** Deeper nesting is harder to read than the long comment it replaces.
- **A summary line is HTML, not Markdown.** Backticks and `**…**` in it show as literal characters; write `<code>name</code>` for an identifier or file. A finding's summary usually names one, so this matters.
- **A blank line directly after every `<summary>` line.** Without it GitHub does not render the Markdown inside (lists, code blocks) and shows it as plain text.
- **A blank line directly after every `</details>`.** Without it the next plain Markdown line, typically the one of an empty class (`**Fix in PR:** none`), is swallowed into the HTML block and shows its asterisks literally. Keep a blank line before `</details>` too, for consistency.
- **The issue link of a follow-up is a bare `#<n>`, inside the finding's `<summary>` before `</summary>`.** In a summary line GitHub turns a bare `#<n>` into a link, shows a Markdown link `[#<n>](…)` with its brackets and parentheses, and garbles a full URL. Whoever appends the link is the caller, not the reviewer, so a caller that appends links repeats this form in its own instructions (`issue-worker` does).
- **The class names stay as text in the summaries** (`Blocking`, `Fix in PR`, `Follow-up`, `What was checked`, `Not covered`): the review rules of the user and of the other agents name those sections.
