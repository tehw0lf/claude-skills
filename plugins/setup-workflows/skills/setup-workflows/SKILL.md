---
name: setup-workflows
description: Set up tehw0lf/workflows reusable CI/CD in a repository — detects the project type and writes a validated caller workflow from the orchestrator's live input list. Use when the user says "set up workflows", "add CI", "workflows einrichten", "add the reusable workflow", or asks to wire a repo up to tehw0lf/workflows.
argument-hint: [path] [--force]
allowed-tools: Bash, Read, Write, Edit, TodoWrite
---

# Set Up Reusable Workflows

## Arguments

- no argument — the repository containing the current working directory
- `path` — the repository at that path
- `--force` — overwrite an existing caller (see step 1)

**The input list comes from the orchestrator's `workflow_call.inputs`, never from the README or memory.** The README once documented a nonexistent `event_name` input in a copy-pasteable example; the orchestrator reads `github.event_name` itself.

## Steps

### 1. Check for an existing caller

If `.github/workflows/` already calls `tehw0lf/workflows`, stop and show it. Overwrite only with `--force` or explicit confirmation. Migrating an existing caller is a different task — hand it back rather than guessing which inputs to keep.

### 2. Fetch the live input list and permissions

```bash
scripts/fetch-inputs.sh          # writes /tmp/valid_inputs.txt
scripts/fetch-permissions.py     # writes /tmp/required_permissions.txt
```

If either fails, **stop and say so** — never fall back to a remembered list. Every key under `with:` must appear in that list. If `e2e` seems missing, the extraction is wrong, not the orchestrator — never route E2E through `post_build_script`.

### 3. Detect the project type

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

For npm/yarn, run `python3 scripts/check-scripts.py <repo>` and paste its output into the report. Pass `lint`/`test`/`e2e`/… as `run <name>` only for scripts marked present. The `install`/`lint`/`test`/`build_*` values are appended to the tool, so write the *subcommand* (`"run build"`, not `"npm run build"`).

**An Nx target is not an npm script.** `project.json` often defines `lint`/`test` while `package.json` does not, and a non-empty input runs unconditionally — `Missing script: "lint"` fails the first push. For a target without a script, either leave the input unset or use `lint: "exec nx lint"`, and say which and why.

### 4. Confirm scope before writing

Default to build-and-test only. Detection shows what is *possible*, not intended, and publishing pushes to public registries — **ask** before enabling any target:

- Docker image → `docker_meta` and the image name
- npm libraries → `library_path`, and the namespace if not `@tehw0lf`
- PyPI / crates.io → Trusted Publishing must be configured on the registry first
- GitHub release → `publish_github_release: "true"` **and** `artifact_path`

Three values are decisions, not detectable properties — **ask, never default:**

| Value | Why | Real example |
|---|---|---|
| `root_dir` | ambiguous once a repo holds more than one project; wrong → the build fails looking like a broken project | `unix-socket-bridge` builds `server/` |
| `library_path` | which build output is published is a choice | `wp2md` publishes `dist` |
| `platforms` | default `linux/amd64,linux/arm64`; narrowing is deliberate | `color` builds `linux/arm64` only |

### 5. Get the job gates right

Every publishing job needs a `push` event **plus** its own non-empty input; miss the second and the job silently does not run:

| Job | Gated on |
|---|---|
| Docker | `docker_meta` |
| npm | `library_path` — **not** `libraries` |
| PyPI | `tool: uv` **and** `publish_python_libraries: "true"` |
| Firefox | `addon_guid` **and** `xpi_path` |
| Android | `app_root` |
| GitHub release | `artifact_path` **and** `publish_github_release: "true"` |
| crates.io | `tool: cargo` |

`libraries` is optional and selects the mode — do not add it reflexively. Empty: publish the single package at `library_path` (the common case). Set: publish each `<library_path>/<name>/package.json`. The only broken combination is `libraries` set with `library_path` empty: green run, nothing published.

### 6. Write the caller

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
    permissions:             # exactly the scopes in /tmp/required_permissions.txt
      id-token: write        # OIDC Trusted Publishing
      attestations: write    # build provenance attestation
      actions: write
      contents: write
      packages: write
      security-events: write # SARIF upload to the Security tab
      pull-requests: write   # npm-audit-autofix, called from security-scan-source
    with:
      tool: npm
      # ... detected inputs
```

**Every scope in `/tmp/required_permissions.txt`, whatever you publish.** The block above is what the orchestrator needed when this was written; the file is what it needs now. GitHub checks the whole call tree before starting — including jobs that will be skipped, like npm-audit-autofix in a Gradle repo — and a scope the caller does not grant ends the run in `startup_failure` with no job and no log. That is how a six-scope list broke `yaft-java`'s first run after npm-audit-autofix started requesting `pull-requests: write`.

**No `secrets: inherit`.** Everything authenticates through OIDC. Add a `secrets:` block only for a Firefox (`AMO_API_KEY`, `AMO_API_SECRET`) or Android (`ANDROID_STOREPASS`) release, naming just those secrets:

```yaml
    secrets:
      AMO_API_KEY: ${{ secrets.AMO_API_KEY }}
      AMO_API_SECRET: ${{ secrets.AMO_API_SECRET }}
```

Set `head_ref: ${{ github.head_ref }}` if the build needs the triggering branch name.

### 7. Validate

```bash
actionlint .github/workflows/build.yml          # if installed
uv run scripts/validate-caller.py <repo>        # input names, run-script values, permissions; exits 1 on any problem
```

`validate-caller.py` is mandatory even when actionlint passes: actionlint does not look into a remote reusable workflow, so an invalid key fails only at dispatch and a missing permission only as a log-less `startup_failure`. Done when it exits 0.

Report what was written, which publishing targets are active, and which registry setup (Trusted Publishing) the user still has to do by hand.
