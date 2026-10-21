#!/usr/bin/env python3
"""Start one instance of a systemd user template unit per repository that has work, at most --parallel at a
time. Reads GitHub only through has-work.py (read-only); starts units, writes nothing else. Meant for the
`ExecStart=` of a oneshot dispatcher unit that a timer starts; see scheduling.md.

usage: dispatch.py [--parallel <n>] [--template <name>@.service] [--dry-run] [--label <name>] [--owner <login>]... [<owner/repo>]...

  --parallel  how many instances of the template may run at once (default 1)
  --template  the template unit (default `work-issue@.service`); its instance name is the repository,
              escaped with `systemd-escape --template` (`tehw0lf/claude-skills` becomes
              `work-issue@tehw0lf-claude\\x2dskills.service`), so `%I` in the template is `owner/repo` again
  --dry-run   start nothing, print the same lines
  the rest    passed to `has-work.py --all` as they are (scope and label)

One instance per repository: the session of an instance works in the repository's checkout and the worker
switches its branches, so a second session on the same repository would disrupt the first. An instance
that is running (`activating`, `active`, `deactivating` or `reloading`, from `systemctl --user list-units`)
takes a slot; its repository is skipped even when it has work, and the rest of the slots go to the other
repositories in the order of has-work.py --all (`owner/repo` arguments first, in the given order, then the
repositories of the `--owner` search by their oldest issue). What does not fit waits for the next tick.
This unit and the template should not run next to a single `work-issue.service` on the same scope: nothing
here sees that unit.

Output, one tab-separated line each:
  SLOTS  <running>  <parallel>                         first line: instances running before any start
  START  <unit>  <owner/repo#n>  <kind>                started (or, with --dry-run, would be)
  SKIP   <unit>  <owner/repo#n>  running|limit         its instance runs already, or no slot was left
  ERROR  <owner/repo>  <reason>                        from has-work.py: the check of that repository failed
  ERROR  <owner/repo>  systemd-escape failed: <reason> no instance name for that repository
  ERROR  <unit>  start failed: <reason>                `systemctl --user start` failed
  ERROR  has-work.py  unexpected line: <line>          a line of has-work.py that is none of the above
  NONE                                                 no repository has work and none failed

Exit codes: 0 done; 1 at least one ERROR line (every other repository was still dispatched); 255 nothing was
started: a bad argument, has-work.py failing as a whole (its reason follows on stderr; with `owner/repo`
arguments a missing `gh` is an ERROR line per repository instead), or the running instances that cannot be
listed. Every `systemctl` and `systemd-escape` call is given 60 seconds; has-work.py
bounds its own calls.
"""
import os
import re
import subprocess
import sys

TIMEOUT = 60
RUNNING = "activating,active,deactivating,reloading"
HAS_WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "has-work.py")


def fail(message):
    print(f"dispatch.py: {message}", file=sys.stderr)
    sys.exit(255)


def run(command):
    """(stdout, None) or (None, reason)."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return None, f"{command[0]} not found"
    except subprocess.TimeoutExpired:
        return None, f"{' '.join(command[:3])} timed out after {TIMEOUT} seconds"
    if done.returncode != 0:
        return None, (done.stderr.strip() or done.stdout.strip() or f"exit {done.returncode}").splitlines()[-1]
    return done.stdout, None


def parse(argv):
    parallel, template, dry, rest, index = 1, "work-issue@.service", False, [], 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--dry-run":
            dry = True
            index += 1
        elif arg in ("--parallel", "--template"):
            if index + 1 >= len(argv):
                fail(f"{arg} needs a value")
            value = argv[index + 1]
            if arg == "--parallel":
                if not value.isdigit() or int(value) < 1:
                    fail(f"--parallel needs a number of at least 1: {value!r}")
                parallel = int(value)
            else:
                if not re.fullmatch(r"[\w:.-]+@\.service", value):
                    fail(f"--template needs a template unit name like work-issue@.service: {value!r}")
                template = value
            index += 2
        elif arg == "--all":
            fail("--all is always passed to has-work.py")
        else:
            rest.append(arg)
            index += 1
    return parallel, template, dry, rest


def main():
    parallel, template, dry, rest = parse(sys.argv[1:])
    try:
        found = subprocess.run([sys.executable, HAS_WORK, "--all", *rest], capture_output=True, text=True)
    except OSError as error:
        fail(f"cannot run has-work.py: {error}")
    if found.returncode not in (0, 1, 2):
        sys.stderr.write(found.stderr)
        fail(f"has-work.py failed with exit {found.returncode}; nothing was started")
    out, reason = run(["systemctl", "--user", "list-units", template.replace("@.", "@*."), f"--state={RUNNING}",
                       "--plain", "--no-legend", "--no-pager"])
    if out is None:
        fail(f"cannot list the running instances: {reason}")
    running = {line.split()[0] for line in out.splitlines() if line.strip()}
    print(f"SLOTS\t{len(running)}\t{parallel}", flush=True)
    free, errors = parallel - len(running), 0
    for line in found.stdout.splitlines():
        fields = line.split("\t")
        if fields[0] == "ERROR":
            print(line, flush=True)
            errors += 1
        elif fields[0] == "NONE":
            print(line, flush=True)
        elif fields[0] == "WORK" and len(fields) == 3:
            repo = fields[1].split("#")[0]
            unit, reason = run(["systemd-escape", f"--template={template}", repo])
            if unit is None or not unit.strip():
                print(f"ERROR\t{repo}\tsystemd-escape failed: {' '.join((reason or 'no output').split())}", flush=True)
                errors += 1
                continue
            unit = unit.strip()
            if unit in running:
                print(f"SKIP\t{unit}\t{fields[1]}\trunning", flush=True)
            elif free < 1:
                print(f"SKIP\t{unit}\t{fields[1]}\tlimit", flush=True)
            else:
                if not dry:
                    _, reason = run(["systemctl", "--user", "start", "--no-block", unit])
                    if reason is not None:
                        print(f"ERROR\t{unit}\tstart failed: {' '.join(reason.split())}", flush=True)
                        errors += 1
                        continue
                free -= 1
                print(f"START\t{unit}\t{fields[1]}\t{fields[2]}", flush=True)
        else:
            print(f"ERROR\thas-work.py\tunexpected line: {' '.join(line.split())}", flush=True)
            errors += 1
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
