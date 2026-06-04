---
name: trivy-fix
description: Fix Trivy CVE findings from SARIF files. Reads all *.sarif files in the current directory, extracts vulnerable packages and versions, then searches the entire codebase (Dockerfiles, go.mod, package.json, Pipfile, pyproject.toml, etc.) for all occurrences — including indirect ones like binaries built inside Dockerfiles — and fixes them in one pass. Use when the user says "trivy findings", "trivy fix", "fix CVEs", "fix sarif" or similar.
argument-hint: [path/to/file.sarif]
allowed-tools: Bash, Read, Edit, Write, TodoWrite
---

# Trivy CVE Fix

Read all Trivy SARIF findings and fix every occurrence of vulnerable package versions across the entire codebase.

## Steps

### 1. Collect SARIF files

If an argument was provided, use that file. Otherwise glob for all `*.sarif` files in the current directory:

```bash
ls *.sarif 2>/dev/null || echo "No SARIF files found"
```

If no SARIF files are found, tell the user and stop.

### 2. Parse findings

Read each SARIF file and extract:
- CVE ID (`rules[].id`)
- Affected package name (`rules[].help.text` → `Package:` line)
- Installed version (`results[].message.text` → `Installed Version:` line)
- Fixed version (`results[].message.text` → `Fixed Version:` line)
- Artifact location (`results[].locations[].physicalLocation.artifactLocation.uri`) — this tells us which binary/file inside the image was scanned

Build a de-duplicated list of: `package → installed_version → fixed_version`.

**Important**: The artifact URI reveals where the vulnerable code lives. Examples:
- `yaft` → the app binary itself (fix: go.mod + Dockerfile build stage)
- `usr/local/bin/gosu` → a tool built from source inside a Dockerfile
- `usr/local/bin/some-tool` → another Go/Rust/etc. binary compiled in a build stage

### 3. Search the codebase for all affected files

For each vulnerable `package@version`, search broadly — do not assume the fix is only in the file Trivy reported. The same version string can appear in multiple places:

**Go stdlib CVEs** (package: `stdlib`, version e.g. `1.25.9`):
```bash
# Find all FROM lines referencing that Go version in any Dockerfile
grep -r "golang:1.25.9" --include="Dockerfile*" -l .
grep -r "golang:1.25.9" --include="*.dockerfile" -l .
# Find go.mod files pinning that version
grep -r "^go 1.25.9" --include="go.mod" -l .
```

**npm/Node packages** (e.g. `lodash@4.17.20`):
```bash
grep -r '"lodash"' --include="package.json" -l .
grep -r '"lodash"' --include="package-lock.json" -l .
```

**Python packages** (e.g. `requests@2.28.0`):
```bash
grep -r "requests" --include="requirements*.txt" -l .
grep -r "requests" --include="pyproject.toml" -l .
grep -r "requests" --include="Pipfile" -l .
```

**OS packages** (Alpine apk / Debian apt):
```bash
grep -r "apk add.*<package>" --include="Dockerfile*" -rn .
grep -r "apt-get install.*<package>" --include="Dockerfile*" -rn .
```

Always check **all Dockerfiles** (including subdirectories like `db/Dockerfile`) not just the root one — multi-image repos often have separate build contexts with their own base images and toolchain versions.

### 4. Determine the correct fix for each finding

Apply the appropriate fix type:

| Finding type | Fix location | Fix action |
|---|---|---|
| Go stdlib in app binary | `go.mod` (`go X.Y.Z`) + `Dockerfile` (`FROM golang:X.Y.Z`) | Bump to fixed version |
| Go stdlib in compiled tool (e.g. gosu) | The `Dockerfile` stage that builds the tool (`FROM golang:X.Y.Z AS builder`) | Bump builder image |
| Go stdlib in all locations | Both `go.mod` and every `Dockerfile` using that Go version | Bump all of them |
| npm package | `package.json` + run `npm install` to update lock file | Bump version, reinstall |
| Python package | `requirements.txt` / `pyproject.toml` / `Pipfile` | Bump version |
| Alpine base image CVE | `FROM alpine:X.Y` in Dockerfile + `apk --no-cache upgrade` already present | Rebuild (no change needed if apk upgrade is already there) |

### 5. Apply all fixes atomically

Edit every affected file in one pass. For each file:

- Read the current content first
- Apply the minimal version bump (exact string replacement of old version → new version)
- Do not change anything else

After all edits, run the project's validation command to confirm nothing broke. For Go projects:

```bash
docker run --rm -v $(pwd):/app -w /app golang:<new-version> bash -c "go mod tidy && go vet ./... && go test -race -cover ./... && go build -buildvcs=false"
```

If Docker is not available, fall back to local toolchain:
```bash
go mod tidy && go vet ./... && go test ./... && go build
```

For Docker image builds (when a Dockerfile was changed):
```bash
docker build -t <image>:test <context-dir>/
```

### 6. Report and commit

Summarize all files changed and CVEs fixed. Then commit on the current branch:

```bash
git add <all changed files>
git commit -m "chore: bump <package> to <version> to fix <N> HIGH CVEs

Fixes: CVE-XXXX-XXXXX, CVE-XXXX-XXXXX, ..."
```

Only commit files that were intentionally changed. Never commit SARIF files, build artifacts, or test binaries.

Then push the current branch to remote:

```bash
git push origin HEAD
```

If the branch has no upstream yet, set it:

```bash
git push -u origin HEAD
```

## Key heuristics

- **One CVE, many files**: A Go stdlib CVE will appear in every Go binary compiled in any Dockerfile. Check all `FROM golang:` lines across the entire repo.
- **Indirect artifacts**: If Trivy reports `usr/local/bin/gosu` as vulnerable, look for the Dockerfile stage that builds gosu — not just the final image's base.
- **Version pinning**: Some Dockerfiles pin versions in `ARG GO_VERSION=1.25.9` — check for that pattern too.
- **Multi-stage builds**: Each `FROM ... AS stage` that uses a vulnerable toolchain must be updated independently.
- **Fixed version format**: Use the exact version from `Fixed Version:` in the SARIF. If it lists multiple (e.g. `1.25.10, 1.26.3`), pick the patch release on the same minor branch (e.g. `1.25.10` if currently on `1.25.x`).
