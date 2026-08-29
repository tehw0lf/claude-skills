---
name: setup-workflows
description: Set up tehw0lf/workflows reusable CI/CD in a repository. Detects the project type, reads the live input list from build-test-publish.yml, and writes a caller workflow with correct permissions and job gates. Use when the user says "set up workflows", "add CI", "workflows einrichten", "add the reusable workflow", or asks to wire a repo up to tehw0lf/workflows.
argument-hint: [path] [--force]
allowed-tools: Bash, Read, Write, Edit, TodoWrite
---

# Set Up Reusable Workflows

Generate a caller workflow for `tehw0lf/workflows` in a target repository.

## Arguments

- no argument — set up the repository containing the current working directory
- `path` — set up the repository at that path
- `--force` — overwrite an existing caller workflow (default: refuse, see step 6)

## The one rule that matters

**Read the input list from the orchestrator, never from documentation and never
from memory.** The README has been wrong before — it once documented an
`event_name` input that does not exist, inside a copy-pasteable example, so
following it produced a caller GitHub rejects. Prose drifts; `workflow_call.inputs`
is the contract.

## Steps

### 1. Fetch the live input list

```bash
gh api repos/tehw0lf/workflows/contents/.github/workflows/build-test-publish.yml \
  --jq '.content' | base64 -d > /tmp/btp.yml
```

If `gh` is unavailable or unauthenticated, fall back to:

```bash
curl -fsSL https://raw.githubusercontent.com/tehw0lf/workflows/main/.github/workflows/build-test-publish.yml -o /tmp/btp.yml
```

If both fail, **stop and say so.** Do not fall back to a remembered input list —
that is exactly the failure mode this skill exists to prevent.

Extract the valid input names and defaults:

```bash
sed -n '/^  workflow_call:/,/^jobs:/p' /tmp/btp.yml \
  | grep -E "^      [a-z0-9_]+:" | sed 's/[ :]//g' | tee /tmp/valid_inputs.txt
wc -l < /tmp/valid_inputs.txt   # expect 42 as of 2026-08
```

**The character class must include digits.** `[a-z_]+` silently drops `e2e` —
the only input with a digit in its name. That is not a harmless miscount: the
missing name reads as "this input was removed", and the natural next step is to
route E2E through `post_build_script` to work around a problem that does not
exist. If the count comes out at 41, the regex is wrong, not the orchestrator.

Every key you write under `with:` must appear in that list. There is **no
`event_name` input** — the orchestrator reads `github.event_name` internally.

### 2. Detect the project type

Inspect the target repository root. Detection drives `tool` and which publishing
inputs are relevant:

| Found | `tool` | Notes |
|---|---|---|
| `package.json` + `nx.json` | `npm` | Nx monorepo — `root_dir` and `library_path` usually needed |
| `package.json` (+ `yarn.lock`) | `yarn` | otherwise `npm` |
| `pyproject.toml` | `uv` | PyPI publishing needs `publish_python_libraries: "true"` |
| `Cargo.toml` | `cargo` | crates.io publishing is gated on `tool: cargo` alone |
| `build.gradle*` | `./gradlew` | |
| `pom.xml` | `mvn` | |
| `Dockerfile` | — | adds `docker_meta`, orthogonal to `tool` |
| `manifest.json` with `browser_specific_settings` | — | Firefox add-on |

Read the actual scripts out of `package.json` rather than assuming names — pass
`lint: "run lint"` only if a `lint` script exists. Map an existing e2e script to
the `e2e` input (it exists; see the digit warning in step 1), not to
`post_build_script`.

**Verify each script input instead of trusting the reading, and paste the check
into the report:**

```bash
python3 - <<'EOF'
import json
s = json.load(open("package.json")).get("scripts", {})
for name in ("install","format","lint","test","e2e","build"):
    print(f"  {name}: {'present' if name in s else 'ABSENT — do not pass this input'}")
EOF
```

**An Nx target is not an npm script.** `project.json` routinely defines `lint`
and `test` targets while `package.json` exposes neither, and `npm run lint` then
fails with `Missing script: "lint"`. The lint step runs `<tool> <input>`
unconditionally when the input is non-empty, so a wrong value is not ignored —
it fails the build on the first push.

Two correct options when a target exists without a matching script: leave the
input unset, or reach the target explicitly with `lint: "exec nx lint"`. Say
which you chose and why; never write `run <name>` for a name absent from
`scripts`. The `install`/`lint`/`test`/
`build_*` inputs are appended to the tool, so the value is the *subcommand*
(`"run build"`, not `"npm run build"`).

For a monorepo, set `root_dir` to the project root; it defaults to `.`.

### 3. Confirm scope before writing

Detection infers the build; it cannot infer intent. Publishing is irreversible in
the sense that it pushes artifacts to public registries, so **ask** before
enabling any publishing target that detection merely made *possible*:

- Docker image → needs `docker_meta`, and the image name
- npm libraries → needs `library_path`, and the namespace if not `@tehw0lf`
- PyPI / crates.io → needs Trusted Publishing configured on the registry first
- GitHub release → needs `publish_github_release: "true"` **and** `artifact_path`

A repo that merely *has* a Dockerfile does not necessarily want to publish an
image. Default to build-and-test only, and add publishing on confirmation.

**Three values cannot be detected at all — they are decisions, not properties of
the repo. Ask; never guess a default and move on:**

| Value | Why detection fails | Real example |
|---|---|---|
| `root_dir` | Ambiguous as soon as a repo holds more than one project | `unix-socket-bridge` builds `server/`, not the repo root |
| `library_path` | Which build output gets published is a choice | `wp2md` publishes `dist` |
| `platforms` | The default is `linux/amd64,linux/arm64`; narrowing is deliberate | `color` builds `linux/arm64` only |

Getting `root_dir` wrong is the worst of the three: the build runs against the
wrong directory and fails in a way that looks like a broken project rather than a
misconfigured caller. If the repo has more than one plausible project root, ask
instead of assuming `.`.

### 4. Get the job gates right

Every publishing job requires a `push` event **plus** its own non-empty input.
Missing the second half means the job silently does not run — no error, no output:

| Job | Gated on |
|---|---|
| Docker | `docker_meta` |
| npm | `library_path` — **not** `libraries` |
| PyPI | `tool: uv` **and** `publish_python_libraries: "true"` |
| Firefox | `addon_guid` **and** `xpi_path` |
| Android | `app_root` |
| GitHub release | `artifact_path` **and** `publish_github_release: "true"` |
| crates.io | `tool: cargo` |

**`libraries` is optional and selects the mode**, so do not add it reflexively:

- `libraries` **empty** — publish the single package at `library_path`. This is
  the common case; `wp2md` uses it with `library_path: dist` and no `libraries`.
- `libraries` **set** — publish each named library from
  `<library_path>/<name>/package.json`. For monorepos publishing several packages.

Only one combination is wrong: **`libraries` set while `library_path` is empty.**
The job is gated on `library_path`, so it never runs — no error, no failed check,
the run is green and nothing is published. Setting only `library_path` is correct
and needs no warning.

### 5. Write the caller

`.github/workflows/build.yml` in the target repo:

```yaml
name: Build

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  build_and_publish:
    uses: tehw0lf/workflows/.github/workflows/build-test-publish.yml@main
    permissions:
      id-token: write        # REQUIRED always — OIDC Trusted Publishing
      attestations: write    # REQUIRED — build provenance attestation
      actions: write
      contents: write
      packages: write
      security-events: write # SARIF upload to the Security tab
    with:
      tool: npm
      # ... detected inputs
```

**Do not add `secrets: inherit`.** The orchestrator declares exactly three
optional secrets — `ANDROID_STOREPASS`, `AMO_API_KEY`, `AMO_API_SECRET` — and
everything else authenticates through OIDC, so there is nothing for a normal
build to inherit. None of the existing callers in this workspace use it.

Add a `secrets:` block only for a Firefox or Android release, and name just the
secrets that target needs:

```yaml
    secrets:
      AMO_API_KEY: ${{ secrets.AMO_API_KEY }}
      AMO_API_SECRET: ${{ secrets.AMO_API_SECRET }}
```

**All six permissions are required regardless of what you publish.** GitHub
evaluates permissions before job execution, so they cannot be granted
conditionally inside a reusable workflow. `id-token` and `attestations` are the
two that get forgotten; without `attestations: write` the build step fails when
it tries to attest provenance.

Set `head_ref: ${{ github.head_ref }}` if the build needs the triggering branch
name.

### 6. Never silently overwrite

If `.github/workflows/` already contains a caller for `tehw0lf/workflows`, stop
and show the user what exists. Overwrite only with `--force` or explicit
confirmation. Migrating an existing caller is a different task than setting one
up — hand it back rather than guessing which inputs to keep.

### 7. Validate before finishing

```bash
actionlint .github/workflows/build.yml
```

If `actionlint` is not installed, at minimum verify the YAML parses:

```bash
python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build.yml'))"
```

Then re-check every `with:` key against the list from step 1:

```bash
python3 - <<'EOF'
import yaml
w = yaml.safe_load(open(".github/workflows/build.yml"))["jobs"]["build_and_publish"]["with"]
valid = set(open("/tmp/valid_inputs.txt").read().split())
for k in w:
    print(("ok      " if k in valid else "INVALID "), k)
EOF
```

Then confirm every `run <name>` value resolves to a real script — the key check
above validates input *names*, not their *values*:

```bash
python3 - <<'EOF'
import json, yaml
s = json.load(open("package.json")).get("scripts", {})
w = yaml.safe_load(open(".github/workflows/build.yml"))["jobs"]["build_and_publish"]["with"]
for k, v in w.items():
    if isinstance(v, str) and v.startswith("run "):
        n = v[4:].split()[0]
        print(("ok      " if n in s else "BROKEN  "), f"{k}: npm {v}")
EOF
```

**This step is not redundant with actionlint.** Verified by experiment: inserting
`event_name:` into an otherwise valid caller, `actionlint` reports no problem —
it does not validate inputs against a *remote* reusable workflow. The run then
fails at dispatch with an unknown-input error. The key check is the only thing
between a generated caller and that failure.

Report what was written, which publishing targets are active, and which registry
setup (Trusted Publishing) the user still has to do by hand.
