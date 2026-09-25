---
name: trivy-fix
description: Fix Trivy CVE findings from SARIF files — every occurrence of each vulnerable version across the codebase, including binaries built inside Dockerfiles, in one pass. Use when the user says "trivy findings", "trivy fix", "fix CVEs", "fix sarif" or similar.
argument-hint: [path/to/file.sarif]
allowed-tools: Bash, Read, Edit, Write, TodoWrite
---

# Trivy CVE Fix

## Steps

### 1. Parse findings

```bash
python3 scripts/parse-sarif.py [<file.sarif>]   # default: every *.sarif in the current directory
```

If it finds no SARIF files, tell the user and stop. Output: one row per `package  installed  fixed  artifact  CVEs`. The **artifact** says where the vulnerable code lives — `yaft` is the app binary itself (go.mod + Dockerfile build stage); `usr/local/bin/gosu` is a tool compiled in some Dockerfile stage.

### 2. Find every occurrence

One CVE, many files: do not assume the fix is only where Trivy reported it. For each vulnerable version, search the whole repo:

- **Go stdlib** — `FROM golang:<ver>` in every `Dockerfile*` and `*.dockerfile` (including subdirectories like `db/Dockerfile`), `ARG GO_VERSION=<ver>`, and `go <ver>` in every `go.mod`. Each `FROM ... AS <stage>` using the toolchain is updated independently.
- **Indirect artifacts** — find the Dockerfile stage that *builds* the reported binary, not just the final image's base.
- **npm** — `package.json`, `package-lock.json`
- **Python** — `requirements*.txt`, `pyproject.toml`, `Pipfile`
- **OS packages** — `apk add` / `apt-get install` lines in Dockerfiles

Done when every occurrence of every vulnerable version is listed against a file.

### 3. Fix

| Finding | Fix |
|---|---|
| Go stdlib in app binary | `go X.Y.Z` in `go.mod` + `FROM golang:X.Y.Z` in the Dockerfile |
| Go stdlib in compiled tool (e.g. gosu) | the builder stage that compiles it |
| npm package | `package.json`, then `npm install` for the lockfile |
| Python package | `requirements.txt` / `pyproject.toml` / `Pipfile` |
| Alpine base image | nothing, if `apk --no-cache upgrade` is already present — rebuild |

Use the exact `Fixed Version`; if it lists several (`1.25.10, 1.26.3`), take the patch release on the current minor branch. Apply only the minimal version bump, every affected file in one pass, nothing else.

### 4. Validate

Go projects:

```bash
docker run --rm -v $(pwd):/app -w /app golang:<new-version> bash -c "go mod tidy && go vet ./... && go test -race -cover ./... && go build -buildvcs=false"
# without Docker:
go mod tidy && go vet ./... && go test ./... && go build
```

For every changed Dockerfile: `docker build -t <image>:test <context-dir>/`. Other stacks: the project's own validation command.

### 5. Commit and push

Summarize files changed and CVEs fixed, then on the current branch commit only the intentionally changed files — never SARIF files, build artifacts, or test binaries:

```bash
git add <changed files>
git commit -m "chore: bump <package> to <version> to fix <N> HIGH CVEs

Fixes: CVE-XXXX-XXXXX, CVE-XXXX-XXXXX, ..."
git push -u origin HEAD
```
