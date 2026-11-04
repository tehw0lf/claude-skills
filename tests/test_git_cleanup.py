"""The Git sequences of the stop and resume clean-ups, run in throwaway repositories.

tests/fixtures/git_cleanup/scenarios.sh runs the documented Git commands (work-issue: SKILL.md steps 2, 6, 7 and 8,
stopping.md, resume.md, issue-worker.md "Back to the starting branch"; nx-migrate: SKILL.md step 2) against a
local bare "origin" and prints `<scenario>\t<check>\t<value>` lines; EXPECTED below holds what Git must answer.
The script runs once with the host's `git` and once in every local container image that has Git.

Needs `sh` and `git` on PATH. `docker` is optional: without it, or when `docker info` fails, the image part is
skipped with that reason. Containers start with `--network none --pull never` and only from images already
listed by `docker image ls`; the test never pulls an image and never uses the network. A Git without `git switch`
and an image without `sh` or Git are skipped with the Git version or the reason; no minimum version is implied.
Run from the repository root: python3 -m unittest discover -s tests
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "git_cleanup"
SCRIPT = FIXTURE_DIR / "scenarios.sh"
TIMEOUT = 120
# Git translates its usage line; the probes read it in English
C_LOCALE = dict(os.environ, LC_ALL="C", LANGUAGE="")

# (scenario, check, value). `nonzero` stands for any non-zero exit code; `status` values are
# `git status --porcelain --untracked-files=all --ignored` lines joined by `|`.
EXPECTED = [
    ('dirty-tracked', 'switch-run', '0'),
    ('dirty-tracked', 'reset', '0'),
    ('dirty-tracked', 'switch-back', '0'),
    ('dirty-tracked', 'content', 'base'),
    ('dirty-tracked', 'branch', 'main'),
    ('dirty-tracked', 'status', ''),
    ('untracked', 'switch-run', '0'),
    ('untracked', 'diff-saved', '0'),
    ('untracked', 'reset', '0'),
    ('untracked', 'switch-back', '0'),
    ('untracked', 'add-n', '0'),
    ('untracked', 'diff-has-tracked-edit', '1'),
    ('untracked', 'diff-has-new-file', '1'),
    ('untracked', 'diff-has-ignored-file', '0'),
    ('untracked', 'reset-clean', '0'),
    ('untracked', 'new-file', 'no'),
    ('untracked', 'ignored-content', 'secret'),
    ('untracked', 'tracked-content', 'base'),
    ('untracked', 'branch-delete', '0'),
    ('untracked', 'run-branch', 'no'),
    ('untracked', 'status', '!! ignored.txt|'),
    ('ignored-target-tracks', 'switch-refused', 'nonzero'),
    ('ignored-target-tracks', 'switch-run-branch', 'no'),
    ('ignored-target-tracks', 'switch-content', 'local'),
    ('ignored-target-tracks', 'merge-refused', 'nonzero'),
    ('ignored-target-tracks', 'merge-head', 'unmoved'),
    ('ignored-target-tracks', 'merge-content', 'local'),
    ('ignored-target-tracks', 'resume-switch-refused', 'nonzero'),
    ('ignored-target-tracks', 'resume-branch', 'no'),
    ('ignored-target-tracks', 'resume-content', 'local'),
    ('ignored-target-tracks', 'branch', 'main'),
    ('ignored-target-tracks', 'control-switch', '0'),
    ('ignored-target-tracks', 'control-content', 'remote'),
    ('ignore-files', 'switch-run', '0'),
    ('ignore-files', 'reset', '0'),
    ('ignore-files', 'switch-back', '0'),
    ('ignore-files', 'created', 'd*/.gitignore|sub/.gitignore|'),
    ('ignore-files', 'self-ignoring-new', 's*/.gitignore|si/.gitignore|'),
    ('ignore-files', 'check-ignore-dir', '1'),
    ('ignore-files', 'mark', '0'),
    ('ignore-files', 'reset-1', '0'),
    ('ignore-files', 'mark-self-ignoring', '0'),
    ('ignore-files', 'reset-2', '0'),
    ('ignore-files', 'diff-has-sub', '1'),
    ('ignore-files', 'diff-has-star', '1'),
    ('ignore-files', 'diff-has-self-ignoring', '1'),
    ('ignore-files', 'diff-has-preexisting', '0'),
    ('ignore-files', 'removed-sub', 'no'),
    ('ignore-files', 'removed-star', 'no'),
    ('ignore-files', 'removed-self-ignoring', 'no'),
    ('ignore-files', 'kept-venv', 'yes'),
    ('ignore-files', 'kept-dx', 'yes'),
    ('ignore-files', 'removed-star-self-ignoring', 'no'),
    ('ignore-files', 'kept-sx', 'yes'),
    ('ignore-files', 'add-n-all', '0'),
    ('ignore-files', 'reset-clean', '0'),
    ('ignore-files', 'removed-keep', 'no'),
    ('ignore-files', 'kept-venv-lib', 'yes'),
    ('ignore-files', 'branch-delete', '0'),
    ('ignore-files', 'status', '!! dx/.gitignore|!! sx/.gitignore|!! venv/.gitignore|!! venv/lib.txt|'),
    ('ignore-files-nodiff', 'switch-run', '0'),
    ('ignore-files-nodiff', 'reset', '0'),
    ('ignore-files-nodiff', 'switch-back', '0'),
    ('ignore-files-nodiff', 'created', 'd*/.gitignore|'),
    ('ignore-files-nodiff', 'clean', '0'),
    ('ignore-files-nodiff', 'removed-star', 'no'),
    ('ignore-files-nodiff', 'kept-dx', 'yes'),
    ('ignore-files-nodiff', 'status', '!! dx/.gitignore|'),
    ('bump-patch', 'switch-run', '0'),
    ('bump-patch', 'stash-create-clean', ''),
    ('bump-patch', 'bump-files', 's.txt|v.txt|'),
    ('bump-patch', 'patch-saved', '0'),
    ('bump-patch', 'restore-bump', '0'),
    ('bump-patch', 'restore-content', 'version=2'),
    ('bump-patch', 'stage', '0'),
    ('bump-patch', 'apply-reverse', '0'),
    ('bump-patch', 'index-files', 'a.txt|s.txt|'),
    ('bump-patch', 'index-s', 'CHANGED|version=1|'),
    ('bump-patch', 'index-v', 'version=1'),
    ('bump-patch', 'tree-s', 'CHANGED|version=2|'),
    ('bump-patch', 'tree-v', 'version=2'),
    ('bump-patch', 'change-commit', 'a.txt|s.txt|'),
    ('bump-patch', 'bump-commit', 's.txt|v.txt|'),
    ('bump-patch', 'status', ''),
    ('switch-back', 'switch-run', '0'),
    ('switch-back', 'switch-default', '0'),
    ('switch-back', 'branch', 'main'),
    ('switch-back', 'merge', '0'),
    ('switch-back', 'main-updated', 'yes'),
    ('switch-back', 'new-file', 'yes'),
    ('switch-back', 'ahead-equal', '0'),
    ('switch-back', 'ahead-one', '1'),
    ('switch-back', 'switch-detach', '0'),
    ('switch-back', 'detached', 'detached'),
    ('switch-back', 'switch-start', '0'),
    ('switch-back', 'start', 'main'),
]


def parse(stdout):
    rows = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append(tuple(parts))
    return rows


def compare(case, where, rows):
    """One subTest per check; a mismatch names the Git, the scenario, the check, the expected and actual value."""
    actual = {(s, c): v for s, c, v in rows}
    for scenario, check, expected in EXPECTED:
        with case.subTest(git=where, scenario=scenario, check=check):
            case.assertEqual(
                actual.get((scenario, check), "<missing>"), expected,
                "%s: scenario %s, check %s" % (where, scenario, check))
    known = {(s, c) for s, c, _ in EXPECTED}
    for scenario, check in sorted(set(actual) - known):
        with case.subTest(git=where, scenario=scenario, check=check):
            case.fail("%s: scenario %s, check %s has no expected value" % (where, scenario, check))


def say(message):
    print(message, file=sys.stderr)


class GitCleanupSequences(unittest.TestCase):
    def test_host_git(self):
        sh = shutil.which("sh")
        if not (sh and shutil.which("git")):
            self.skipTest("host: sh or git not on PATH")
        version = subprocess.run(["git", "--version"], capture_output=True, text=True, env=C_LOCALE).stdout.strip()
        probe = subprocess.run(["git", "switch", "-h"], capture_output=True, text=True, env=C_LOCALE)
        if "usage: git switch" not in probe.stdout + probe.stderr:
            self.skipTest("host: %s has no `git switch`" % version)
        with tempfile.TemporaryDirectory() as work:
            done = subprocess.run([sh, str(SCRIPT), work], capture_output=True, text=True, timeout=TIMEOUT,
                                  env=C_LOCALE)
        if done.returncode == 3:
            self.skipTest("host: %s" % done.stderr.strip())
        self.assertEqual(done.returncode, 0, "host: scenarios.sh failed: " + done.stderr)
        say("git-cleanup: host (%s): ran" % version)
        compare(self, "host (%s)" % version, parse(done.stdout))

    def test_local_images(self):
        if not shutil.which("docker"):
            self.skipTest("images: docker is not installed")
        info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=TIMEOUT)
        if info.returncode != 0:
            self.skipTest("images: `docker info` failed")
        listing = subprocess.run(["docker", "image", "ls", "--format", "{{.ID}} {{.Repository}}:{{.Tag}}"],
                                 capture_output=True, text=True, timeout=TIMEOUT).stdout
        images = {}
        for line in listing.splitlines():
            image_id, _, name = line.partition(" ")
            if image_id and "<none>" not in name:
                images.setdefault(image_id, name)
        if not images:
            self.skipTest("images: no local image")
        base = ["docker", "run", "--rm", "--network", "none", "--pull", "never", "--entrypoint", "sh"]
        probe = ('export LC_ALL=C LANGUAGE=; git --version || exit 3; case "$(git switch -h 2>&1)" in "usage: git switch"*) '
                 'echo has-switch;; *) echo no-switch;; esac')
        ran = 0
        for image_id, name in sorted(images.items(), key=lambda kv: kv[1]):
            label = "image %s (%s)" % (name, image_id)
            try:
                found = subprocess.run(base + [image_id, "-c", probe], capture_output=True, text=True,
                                       timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                say("git-cleanup: %s: skipped, probe timed out" % label)
                continue
            if found.returncode != 0:
                say("git-cleanup: %s: skipped, no sh or git in the image or the container did not start" % label)
                continue
            lines = found.stdout.strip().splitlines()
            version = lines[0] if lines else "git ?"
            if lines[-1:] != ["has-switch"]:
                say("git-cleanup: %s: skipped, %s has no `git switch`" % (label, version))
                continue
            with self.subTest(image=name):
                run = subprocess.run(
                    base + ["-v", "%s:/scenarios:ro" % FIXTURE_DIR, image_id, "-c",
                            'd=/tmp/scn.$$ && mkdir "$d" && sh /scenarios/scenarios.sh "$d"'],
                    capture_output=True, text=True, timeout=TIMEOUT)
                if run.returncode == 3:
                    say("git-cleanup: %s: skipped, %s" % (label, run.stderr.strip()))
                    continue
                self.assertEqual(run.returncode, 0, "%s: scenarios.sh failed: %s" % (label, run.stderr))
                say("git-cleanup: %s (%s): ran" % (label, version))
                ran += 1
                compare(self, "%s (%s)" % (label, version), parse(run.stdout))
        say("git-cleanup: %d of %d local images ran" % (ran, len(images)))


if __name__ == "__main__":
    unittest.main()
