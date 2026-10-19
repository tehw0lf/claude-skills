#!/usr/bin/env node
// Pin check of bump-subpackages.sh: finds every dependency spec that stops resolving to the workspace
// package once that package is bumped, deciding by npm's own rules (npm's bundled semver,
// npm-package-arg and @npmcli/map-workspaces, loaded from the npm that is on PATH).
// usage: check-pins.js <path of npm> < records.json   run in the Nx workspace root
//   stdin:  JSON array of {"kind": "bump"|"already", "dir": "<package dir, relative>", "head": "<HEAD version>",
//           "work": "<working-tree version, only for already>"}
//   stdout: one line per hit: "  <manifest>: <field> pins <name> to <spec> (<new version> does not satisfy it)"
//   stderr: "# note: ..." lines for what is not checked
//   exit:   0 = checked (hits or not: the caller decides on stdout); 1 = failure, message on stderr
//           (npm's modules cannot be loaded, workspaces cannot be mapped, bad input)
// A hit: the HEAD version satisfies the spec and the new version does not. A spec the HEAD version
// already misses resolves to the registry before the bump too: a note (only when the new version misses
// it as well), not a hit. An alias of the package onto itself ("a": "npm:a@1.0.0") is checked like its sub-spec.
'use strict';
const fs = require('fs');
const path = require('path');

function fail(message) {
  process.stderr.write(`check-pins: ${message}\n`);
  process.exit(1);
}
function note(message) {
  process.stderr.write(`# note: ${message}\n`);
}

const npmBin = process.argv[2];
if (!npmBin) fail('usage: check-pins.js <path of npm> < records.json');
function npmPackageDir(real) {
  const dir = path.dirname(path.dirname(real));
  const name = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8')).name;
  if (name !== 'npm') throw new Error(`${dir} is not the npm package`);
  return dir;
}
let npmDir;
try {
  npmDir = npmPackageDir(fs.realpathSync(npmBin));
} catch (first) {
  // npm on PATH is a shim (a version manager's wrapper): ask npm where it lives.
  try {
    const out = require('child_process').execFileSync(npmBin, ['exec', '-c', 'echo "$npm_execpath"'], { encoding: 'utf8' });
    npmDir = npmPackageDir(fs.realpathSync(out.trim().split('\n').pop()));
  } catch (second) {
    fail(`cannot find the npm package directory from ${npmBin} (${first.message}; ${second.message}); the pins are not checked, so nothing is bumped`);
  }
}
function load(name) {
  try {
    return require(require.resolve(name, { paths: [npmDir] }));
  } catch (e) {
    return fail(`cannot load ${name} from the npm package directory (${e.message}); the pins are not checked, so nothing is bumped`);
  }
}
const semver = load('semver');
const npa = load('npm-package-arg');
const mapWorkspaces = load('@npmcli/map-workspaces');

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

async function main() {
  let records;
  try {
    records = JSON.parse(fs.readFileSync(0, 'utf8'));
    if (!Array.isArray(records)) throw new Error('not an array');
  } catch (e) {
    return fail(`cannot read the package records from stdin (${e.message})`);
  }
  const root = process.cwd();
  let rootPkg;
  try {
    rootPkg = readJson(path.join(root, 'package.json'));
  } catch (e) {
    return fail(`cannot read package.json (${e.message})`);
  }
  let workspaces;
  try {
    workspaces = await mapWorkspaces({ cwd: root, pkg: rootPkg });
  } catch (e) {
    return fail(`cannot map the workspaces of package.json as npm does (${e.message}); this run bumped nothing`);
  }
  const wsDirs = new Set([...workspaces.values()].map((p) => path.resolve(p)));

  // package name -> {head, next}
  const moving = new Map();
  for (const rec of records) {
    const abs = path.resolve(root, rec.dir);
    if (!wsDirs.has(abs)) {
      note(`${rec.dir} is not covered by the workspaces of package.json, so a pin on it is not checked`);
      continue;
    }
    let name;
    try {
      name = readJson(path.join(abs, 'package.json')).name;
    } catch (e) {
      note(`cannot read ${rec.dir}/package.json, so a pin on it is not checked`);
      continue;
    }
    if (typeof name !== 'string' || name === '') {
      note(`${rec.dir}/package.json has no name, so a pin on it is not checked`);
      continue;
    }
    const next = rec.kind === 'already' ? rec.work : semver.inc(rec.head, 'patch', true);
    if (!semver.valid(rec.head, true) || !semver.valid(next, true)) {
      return fail(`${rec.dir}: cannot compute the new version from ${rec.head}`);
    }
    moving.set(name, { head: rec.head, next });
  }
  if (moving.size === 0) return;

  const manifests = [path.join(root, 'package.json'), ...[...wsDirs].map((d) => path.join(d, 'package.json'))];
  for (const file of manifests) {
    const rel = path.relative(root, file);
    let doc;
    try {
      if (fs.lstatSync(file).isSymbolicLink()) continue;
      doc = readJson(file);
    } catch (e) {
      if (e.code !== 'ENOENT') note(`cannot read the dependencies of ${rel}, so its pins are not checked`);
      continue;
    }
    for (const field of ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']) {
      const deps = doc[field];
      if (deps === null || typeof deps !== 'object' || Array.isArray(deps)) continue;
      for (const [dep, spec] of Object.entries(deps)) {
        const target = moving.get(dep);
        if (!target || typeof spec !== 'string') continue;
        let parsed;
        try {
          parsed = npa.resolve(dep, spec);
        } catch (e) {
          continue; // npm reports an unparsable spec itself at install time
        }
        // npm decides an alias of the package onto itself by the version of its sub-spec (dep-valid.js)
        if (parsed.type === 'alias' && parsed.subSpec && parsed.subSpec.name === dep) parsed = parsed.subSpec;
        if (parsed.type !== 'range' && parsed.type !== 'version') continue;
        if (parsed.fetchSpec === '*') continue; // npm treats * as always valid
        const headOk = semver.satisfies(target.head, parsed.fetchSpec, true);
        const nextOk = semver.satisfies(target.next, parsed.fetchSpec, true);
        if (!headOk && !nextOk) {
          note(`${rel}: ${field} ${dep} "${spec}" is satisfied neither by the version HEAD has (${target.head}) nor by the new one (${target.next}), so the bump changes nothing for it`);
        } else if (headOk && !nextOk) {
          process.stdout.write(`  ${rel}: ${field} pins ${dep} to ${spec} (${target.next} does not satisfy it)\n`);
        }
      }
    }
  }
}
main().catch((e) => fail(e.message));
