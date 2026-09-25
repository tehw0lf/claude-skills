#!/usr/bin/env python3
"""De-duplicate Trivy SARIF findings into package / installed / fixed / artifact / CVEs.
usage: parse-sarif.py [<file.sarif>...]   (default: every *.sarif in the current directory)"""
import glob, json, re, sys
from collections import defaultdict

def field(text, name):
    m = re.search(rf"^{name}:\s*(.+)$", text or "", re.M)
    return m.group(1).strip() if m else "?"

files = sys.argv[1:] or sorted(glob.glob("*.sarif"))
if not files:
    sys.exit("No SARIF files found")

findings = defaultdict(set)  # (package, installed, fixed, artifact) -> CVEs
for f in files:
    for run in json.load(open(f)).get("runs", []):
        pkg = {r["id"]: field(r.get("help", {}).get("text"), "Package") for r in run["tool"]["driver"].get("rules", [])}
        for res in run.get("results", []):
            msg = res.get("message", {}).get("text", "")
            for loc in res.get("locations") or [{}]:
                uri = loc.get("physicalLocation", {}).get("artifactLocation", {}).get("uri", "?")
                key = (pkg.get(res["ruleId"], "?"), field(msg, "Installed Version"), field(msg, "Fixed Version"), uri)
                findings[key].add(res["ruleId"])

print("package\tinstalled\tfixed\tartifact\tCVEs")
for (p, i, fx, uri), cves in sorted(findings.items()):
    print(f"{p}\t{i}\t{fx}\t{uri}\t{','.join(sorted(cves))}")
