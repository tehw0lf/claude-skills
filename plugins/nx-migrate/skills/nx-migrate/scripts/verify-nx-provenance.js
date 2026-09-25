#!/usr/bin/env node
// Verify an nx release's SLSA provenance the way npm's tooling would.
// usage: node verify-nx-provenance.js [<version>]   (default: npm's `latest`)
// Exit 0 only when repository, workflow, ref and digest all match.
const cp = require('child_process');

(async () => {
  const V = process.argv[2] || cp.execSync('npm view nx version', { encoding: 'utf8' }).trim();
  const view = JSON.parse(cp.execSync(`npm view "nx@${V}" --json --silent`, { encoding: 'utf8' }));
  const v = Array.isArray(view) ? view[0] : view;
  const att = await (await fetch(v.dist.attestations.url)).json();
  const prov = att.attestations.find(a => a.predicateType === 'https://slsa.dev/provenance/v1');
  const payload = JSON.parse(Buffer.from(prov.bundle.dsseEnvelope.payload, 'base64').toString());
  const wf = payload.predicate.buildDefinition.externalParameters.workflow;
  const distSha = Buffer.from(v.dist.integrity.replace('sha512-', ''), 'base64').toString('hex');

  const checks = [
    ['repository', wf.repository, wf.repository === 'https://github.com/nrwl/nx'],
    ['workflow', wf.path, wf.path === '.github/workflows/publish.yml'],
    ['ref', wf.ref, wf.ref === `refs/tags/${v.version}`],
    ['digest', distSha.slice(0, 16) + '…', distSha === payload.subject[0].digest.sha512],
  ];
  for (const [name, val, ok] of checks) console.log(`${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(10)} ${val}`);
  const pass = checks.every(c => c[2]);
  console.log(pass ? `nx@${v.version}: VERIFIED` : `nx@${v.version}: NOT VERIFIED — stop and report`);
  process.exit(pass ? 0 : 1);
})().catch(e => { console.error('verification error:', e.message); process.exit(1); });
