#!/usr/bin/env node
// Target check of the nx-migrate skill: decides whether a given target version can be migrated to and
// reports how it relates to the installed version and to the newest stable release. The target is
// validated as strict semver before it is used anywhere else, so a value from a prompt never reaches a
// registry query, a branch name or verify-nx-provenance.js unchecked. Uses npm's bundled semver,
// loaded from the npm that is on PATH (as check-pins.js does); starts npm without a shell.
// usage: check-target.js <installed version | -> <target version>   run in the Nx workspace root (reads the registry)
//   stdout: five lines, tab-separated:
//     TARGET <target>
//     INSTALLED <installed>
//     ORDER newer|equal|older|unknown (target against installed, semver precedence; unknown when the
//                                    installed version is given as "-", i.e. could not be read)
//     PRERELEASE yes|no              (of the target)
//     LATEST <newest stable>         (npm view nx version)
//   stderr: the reason of a failure
//   exit:   0 = the lines are complete (the caller decides on them); 1 = failure, nothing on stdout
//           (bad arguments, target not strict semver or not published, npm or its semver not usable)
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

function fail(message) {
  process.stderr.write(`check-target: ${message}\n`);
  process.exit(1);
}

const [installed, target, ...rest] = process.argv.slice(2);
if (!installed || !target || rest.length) fail('usage: check-target.js <installed version | -> <target version>');

function findNpm() {
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    if (!dir) continue;
    const candidate = path.join(dir, 'npm');
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch (e) {
      // next entry
    }
  }
  return fail('npm is not on PATH; the target is not checked');
}
function npmPackageDir(real) {
  const dir = path.dirname(path.dirname(real));
  const name = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8')).name;
  if (name !== 'npm') throw new Error(`${dir} is not the npm package`);
  return dir;
}

const npmBin = findNpm();
let npmDir;
try {
  npmDir = npmPackageDir(fs.realpathSync(npmBin));
} catch (first) {
  // npm on PATH is a shim (a version manager's wrapper): ask npm where it lives.
  try {
    const out = execFileSync(npmBin, ['exec', '-c', 'echo "$npm_execpath"'], { encoding: 'utf8' });
    npmDir = npmPackageDir(fs.realpathSync(out.trim().split('\n').pop()));
  } catch (second) {
    fail(`cannot find the npm package directory from ${npmBin} (${first.message}; ${second.message}); the target is not checked`);
  }
}
let semver;
try {
  semver = require(require.resolve('semver', { paths: [npmDir] }));
} catch (e) {
  fail(`cannot load semver from the npm package directory (${e.message}); the target is not checked`);
}

if (installed !== '-' && semver.valid(installed) !== installed) fail(`the installed version "${installed}" is not strict semver`);
if (semver.valid(target) !== target) {
  fail(`the target "${target}" is not an exact version (no range, dist-tag, "v" prefix or partial version)`);
}

function npmView(args) {
  try {
    const value = JSON.parse(execFileSync(npmBin, ['view', ...args, '--json'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }));
    // npm prints a string or, depending on its version, an array of that one string.
    return Array.isArray(value) && value.length === 1 ? value[0] : value;
  } catch (e) {
    return fail(`npm view ${args.join(' ')} failed (${(e.stderr || e.message).toString().trim().split('\n')[0]})`);
  }
}
const published = npmView([`nx@${target}`, 'version']);
if (published !== target) fail(`nx ${target} is not a published version (npm view printed ${JSON.stringify(published)})`);
const latest = npmView(['nx', 'version']);
if (typeof latest !== 'string' || semver.valid(latest) !== latest) fail(`npm view nx version printed ${JSON.stringify(latest)}`);

const cmp = installed === '-' ? null : semver.compare(target, installed);
process.stdout.write(
  [
    `TARGET\t${target}`,
    `INSTALLED\t${installed}`,
    `ORDER\t${cmp === null ? 'unknown' : cmp > 0 ? 'newer' : cmp === 0 ? 'equal' : 'older'}`,
    `PRERELEASE\t${semver.prerelease(target) ? 'yes' : 'no'}`,
    `LATEST\t${latest}`,
  ].join('\n') + '\n'
);
