## Stopping after the branch exists

A stop in steps 3 to 7 leaves the checkout as step 1 found it. The working tree was clean then (a dirty one is a stop in step 1) and nothing has been committed, so every uncommitted change, and every file that is untracked under the ignore rules of the branch step 1 found, is this run's own. The sequence therefore goes back to that branch before it decides what is the run's own:

```bash
git diff --binary HEAD > <temporary directory>/abandoned.diff   # only when there is work worth showing: HEAD includes what is already staged (a `git mv`, a `git rm`) and the edits of tracked ignore files. Outside the checkout, or the clean-up below deletes it; name the file in the report
git reset --hard                            # on the run's branch: discards the tracked changes (a rewritten lockfile, edited tracked ignore files), so that the switch neither refuses nor carries them over
git switch <previous branch>           # the branch WORKTREE named; for "(detached)": git switch --detach <the commit kept in step 1>. Only when it exits 0 do the commands below run
git -c core.quotePath=false ls-files --others --exclude-standard -- ':(glob)**/.gitignore'   # ignore files the run created
git -c core.quotePath=false ls-files --others --ignored --exclude-standard -- ':(glob)**/.gitignore' > <temporary directory>/ignored-ignore-files-after.txt   # ignore files that ignore themselves, now
LC_ALL=C comm -13 <temporary directory>/ignored-ignore-files-before.txt <temporary directory>/ignored-ignore-files-after.txt   # those that appeared during the run
```

`core.quotePath=false` makes git print bytes above 0x80 as they are; a path with `"`, `\` or a control character is still printed in C-quoted form, starting with `"`, and every other character, `'` and (in `ls-files`) the space among them, is printed as is. When the first listing or the comparison prints a line that starts with `"` or contains `'` (see the rule on paths taken from printed output), stop the clean-up there, before the blocks that follow, and report the line as git printed it. The quoted text is not a path, and unquoting it by hand risks acting on another one; the file cannot be left in place either, because the blocks below run `git add -N .` and `git clean -fd` with its ignore rules still in effect, which can write an ignored file (a registry token, for example) into the saved diff and delete it.

A `comm` that exits non-zero (a missing snapshot; also a quoted line that put a listing out of byte order) is not an empty comparison: stop the clean-up there and report it. Only when the comparison printed a path, test those paths for a negation line: `grep -l -e '^!' -- '<path>' …` (one single-quoted argument per path the comparison printed). Then, when grep printed a path, check the directories of those paths: `git -c core.quotePath=false check-ignore --no-index -- '<directory>' …` (one single-quoted argument per directory of a path grep printed) (`--no-index` so that a directory holding a tracked file still counts as ignored, as git does not read an ignore file there either), each without a trailing slash and a root ignore file (directory `.`) left out, because git always reads it; when only root ignore files are left, the call is skipped (it exits 128 without a path) and they are all selected. A path grep printed is selected when its directory is not printed by `check-ignore`; a root ignore file is selected without the check. Exit 1 with no output means that no directory is ignored (all selected); an exit other than 0 and 1 stops the clean-up, and it is reported. A selected path is an ignore file of the run that ignores itself and re-includes a file; it is removed with the listed ones, in the block below. The others stay: the `*`-only files that pytest or ruff write into their caches and a fresh virtual environment's un-ignore nothing, and a file inside an ignored directory (`build/.gitignore` under a rule `build/`, `out/sub/.gitignore` under `out/*`) is never read by git, so its negation re-includes nothing. Git reads an ignore file exactly when its directory is not ignored, so `out/.gitignore` under `out/*` and an ignore file excluded only by a file rule (`ex/.gitignore` in `.git/info/exclude`) are read and are selected.

Only when the first listing printed a path (a pathspec that matches nothing makes `git add -N` fail), use the next block with those paths: with a saved diff its first two commands, without a saved diff only its third. Each path is its own `:(literal)` argument, as git printed it (a path with `'` has stopped the clean-up above, so every path filled in here is safe inside single quotes): git reads the arguments as pathspecs, so without `:(literal)` a `*`, `?` or `[` in a name would also match other ignore files, among them ignored ones that were there before the run, which `git add -N -f` followed by `git reset --hard` deletes.

```bash
git add -N -- ':(literal)<path>' … && git diff --binary HEAD >> <temporary directory>/abandoned.diff   # with a saved diff
git reset --hard                            # with a saved diff: removes the intent-to-add ignore files again
git clean -f -- ':(literal)<path>' …          # without a saved diff: removes them
```

Only when a path was selected, use the next block with those paths, after the previous one if that ran: they are ignored, so plain `git add -N` refuses them and plain `git clean -f` leaves them. With a saved diff its first command, without a saved diff its second; the third always runs. The paths are `:(literal)` arguments here too, for the reason given above. No `git clean -fx` is run for them: a permission rule for it would need a `*` for the paths and would also allow `git clean -fx -- .`, which deletes every ignored file. The mark and the reset remove them without it, and a file such an ignore file re-included stays as an untracked file.

```bash
git add -N -f -- ':(literal)<path>' … && git diff --binary HEAD >> <temporary directory>/abandoned.diff   # with a saved diff
git add -N -f -- ':(literal)<path>' …       # without a saved diff
git reset --hard                            # removes the intent-to-add ignore files again
```

Then, always, in this order:

```bash
git add -N . && git diff --binary HEAD >> <temporary directory>/abandoned.diff   # with a saved diff only: now only files untracked under the restored rules are marked, so -N makes the run's new files part of the diff
git reset --hard && git clean -fd           # ignored files (dependencies, build output) stay
git branch -D <the branch from step 2>      # only that one: a branch this run did not create is never deleted
```

Run everything after the switch only when the switch exited 0. If it fails (an untracked file the run wrote sits at a path the previous branch tracks), run no further command of this clean-up and do not delete the run's branch: the checkout stays on the run's branch, whose ignore rules would make the listings, `git add -N` and `git clean` act on files that are not the run's own, and the report names the paths git prints. Reason for the order: untracked and ignored files come along with a `git switch`, so a file that only the previous branch ignores (a project `.npmrc`) counts as untracked on the branch step 2 created from `origin/<default>`; there `git clean -fd` deletes it, `git add -N .` writes its content (a registry token, for example) into the saved diff, and the `git reset --hard` that follows removes it. On the previous branch the rules are those step 1's clean check was made under, so exactly the files untracked there are the run's own. A file the run wrote that the previous branch ignores stays and is not in the saved diff: leaving a file is recoverable, deleting one that was there before is not.

Ignored files that were there before the run are protected by step 2's `--no-overwrite-ignore`, not by step 1's clean check. The switch back above has no such option: every path it can replace was tracked and clean when step 1 ran and was removed by step 2's switch, so whatever sits there was written by the run, and with the option the switch would refuse and strand the checkout on the run's branch.

Every saved diff is written with `--binary`: without it git writes a binary file (an image, a fixture) only as `Binary files … differ`, and `git apply` cannot restore that file from the saved diff.

The tracked ignore files are restored and the run's own ignore files removed before `git add -N` because a file the change un-ignored (a project `.npmrc`, for example) would otherwise be marked intent-to-add, deleted by `git reset --hard`, and its content (a registry token, for example) written into the saved diff. Each line is a plain command without pipes. The commands of this clean-up are run as written here, without the trailing `#` comments of the code blocks, as for `git stash create` in step 6: the example permission list of `orchestrate-issue` (`scheduling.md`) allows `git reset --hard` and `git clean -fd` as exact commands.

The first listing sees only ignore files that are not ignored themselves. An ignore file that ignores itself (`*` with `!.npmrc`) is found by the second listing compared with the snapshot of step 2 (taken on the same branch, so both lists are made under the same rules), and the negation test with the check of its directory selects the ones git reads and that re-include a file: left in place, the files they un-ignore would be marked intent-to-add, written into the saved diff and deleted by the clean-up. The comparison is by path, so an ignore file that existed before the run (the one inside a virtual environment) is never selected, and neither is a new one without a negation. The run never creates an ignore file that ignores itself and re-includes a file: when the plan's change needs one, the run stops and reports it.

A branch left behind would make the next run look for started work, and files left behind would make it stop on a dirty working tree. Once step 8 has committed, nothing is discarded any more: a stop after that reports the branch and the PR as they are.
