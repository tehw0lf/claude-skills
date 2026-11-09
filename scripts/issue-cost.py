#!/usr/bin/env python3
"""Tokens and an estimate in money per issue, read from the local Claude Code session transcripts.
usage: issue-cost.py --since YYYY-MM-DD [--until YYYY-MM-DD] [--projects <dir>] [--prices <file>]
  --since     first date to read (required: nothing is stored between runs)
  --until     last date, default today (UTC)
  --projects  the transcript directory, default ~/.claude/projects; read as <dir>/*/<session>.jsonl and
              <dir>/*/<session>/subagents/agent-*.jsonl, with the agent-*.meta.json beside each subagent file
  --prices    the dated price table, default scripts/issue-cost-prices.json beside this file
Read-only, stdlib only, no network and no gh: the transcripts exist on one machine only, so the rows are per
machine. They are additive (counts and USD), so the rows of several machines are combined by summing per key.
The money figure is an estimate, not a bill: the list prices of the table applied to the token counts (a
subscription has no per-token bill). The transcript format is an undocumented local file format: there is no
check of its `version` field (the values seen are printed), a message that lacks a field is reported, not
filled with zero.
A file counts in the range by the date of the `timestamp` of its first line (UTC).
Matching a file to an issue, the first rule that applies:
  1. the first user message names exactly one issue, as `owner/repo#n` or `#n in owner/repo` (also
     `#n in the repository owner/repo`); a number after `PR ` or `pull request ` is a pull request, not an issue
  2. the file is a subagent whose meta file has `parentAgentId` and the transcript `agent-<id>.jsonl` of that
     parent in the same directory is matched: it inherits the issue (a reviewer is spawned with a PR, not an issue)
  3. the lines whose `gitBranch` has the form `<type>/<n>-...` all name the same <n> and the first user message
     names exactly one `owner/repo` (any form above, or `pull request #m in owner/repo`)
Everything else is unmatched, with a reason: no-reference (no rule applies), several-branches (several numbers
in the branches), no-repository (one number, but no or several repositories in the prompt). Main sessions
(lines without `attributionAgent`) may cover several issues and are mostly unmatched.
A message is counted once per file: one API response is written over several lines with the same `message.id`
and `usage`; the first line counts. Messages whose four token fields are all 0 are ignored.
Output, tab separated; lines starting with `#` are the header and notes:
  # issue-cost.py prices as_of <date> source <url>; estimate at list prices, not a bill
  # versions <comma separated `version` values seen>
  TOKENS     <owner/repo> <n> <agent> <model> <messages> <input> <output> <cache_write> <cache_read> <usd or ->
             agent = `attributionAgent`, else `agentType` of the meta file, else `-`
  ISSUE      <owner/repo> <n> <messages> <input> <output> <cache_write> <cache_read> <usd or ->
             the sum over the TOKENS rows of the issue; `-` when any of them is `-`
  UNMATCHED  <reason> <agent> <model> <messages> <input> <output> <cache_write> <cache_read> <usd or ->
  # SKIPPED file <file>: <reason>              a file that could not be read or parsed (it counts nothing)
  # SKIPPED fields <file>: <k> assistant messages without <field>
  # SKIPPED price <model>: <k> messages (<reason>)
             tokens are counted, the usd of their row is `-`. Reasons: not in price table; no cache
             write breakdown (cache_creation_input_tokens above 0 without a matching
             usage.cache_creation 5m and 1h split); speed or service_tier is not standard
usd = tokens * price per million tokens of the table (`input`, `output`, `cache_write_5m`, `cache_write_1h`,
`cache_read`), the cache write split by TTL from usage.cache_creation.
Not covered: server tool requests (usage.server_tool_use) and advisor model calls (`advisorModel`).
Exit 0 on a completed run (also when nothing was found), 1 when the price table cannot be read or is
invalid, 2 on invalid arguments."""
import argparse, datetime, json, re, sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
PRICE_FIELDS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
REPO = r"[\w.-]+/[\w.-]*[\w-]"
NOT_PR = r"(?<!pull request )(?<!PR )(?<![\w./-])"
REF_A = re.compile(NOT_PR + r"(" + REPO + r")#(\d+)\b", re.I)
REF_B = re.compile(r"(?<!pull request )(?<!PR )#(\d+) in (?:the repository )?(" + REPO + r")", re.I)
REPO_ANY = [
    re.compile(r"(" + REPO + r")#\d+\b"),
    re.compile(r"#\d+ in (?:the repository )?(" + REPO + r")", re.I),
]
BRANCH = re.compile(r"^[a-z]+/(\d+)-")
MILLION = Decimal(1000000)


def die(msg, code=1):
    print(f"issue-cost.py: {msg}", file=sys.stderr)
    sys.exit(code)


def parse_date(text):
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        die(f"not a date (YYYY-MM-DD): {text}", 2)


def load_prices(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal, parse_int=Decimal)
    except (OSError, ValueError) as e:
        die(f"cannot read the price table {path}: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        die(f"invalid price table {path}: needs an object with `models`")
    as_of, source = data.get("as_of"), data.get("source")
    try:
        datetime.date.fromisoformat(as_of)
    except (TypeError, ValueError):
        die(f"invalid price table {path}: `as_of` must be YYYY-MM-DD")
    if not isinstance(source, str) or not source:
        die(f"invalid price table {path}: `source` must be a non-empty string")
    for model, row in data["models"].items():
        if not isinstance(row, dict):
            die(f"invalid price table {path}: `{model}` is not an object")
        for field in PRICE_FIELDS:
            v = row.get(field)
            if not isinstance(v, Decimal) or v < 0:
                die(f"invalid price table {path}: `{model}`.`{field}` must be a number >= 0")
    return as_of, source, data["models"]


def read_lines(path):
    """Parsed lines of a transcript, or (None, reason)."""
    lines = []
    try:
        with open(path, encoding="utf-8") as fh:
            for k, raw in enumerate(fh, 1):
                if not raw.strip():
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    return None, f"line {k}: invalid JSON"
                if not isinstance(d, dict):
                    return None, f"line {k}: not a JSON object"
                lines.append(d)
    except (OSError, UnicodeDecodeError) as e:
        return None, f"cannot read: {e}"
    return lines, None


def first_prompt(lines):
    for d in lines:
        m = d.get("message")
        if d.get("type") == "user" and not d.get("isMeta") and isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and isinstance(b.get("text"), str))
            return ""
    return ""


def prompt_refs(text):
    refs = {(r.lower(), int(n)) for r, n in REF_A.findall(text)}
    refs |= {(r.lower(), int(n)) for n, r in REF_B.findall(text)}
    return refs


def prompt_repos(text):
    repos = {r.lower() for r, _ in prompt_refs(text)}
    for rx in REPO_ANY:
        repos |= {r.lower() for r in rx.findall(text)}
    repos |= {r.lower() for r in re.findall(r"(?:pull request|PR) #\d+ in (?:the repository )?(" + REPO + ")", text, re.I)}
    return repos


def first_date(lines):
    for d in lines:
        ts = d.get("timestamp")
        if isinstance(ts, str):
            try:
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                return None
            if dt.tzinfo is not None:
                dt = dt.astimezone(datetime.timezone.utc)
            return dt.date()
        return None
    return None


def main():
    ap = argparse.ArgumentParser(prog="issue-cost.py", description="tokens and estimated cost per issue")
    ap.add_argument("--since", required=True)
    ap.add_argument("--until")
    ap.add_argument("--projects", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--prices", default=str(Path(__file__).with_name("issue-cost-prices.json")))
    args = ap.parse_args()
    since = parse_date(args.since)
    until = parse_date(args.until) if args.until else datetime.datetime.now(datetime.timezone.utc).date()
    if since > until:
        die("--since is after --until", 2)
    as_of, source, models = load_prices(args.prices)
    root = Path(args.projects)

    files = sorted(root.glob("*/*.jsonl")) + sorted(root.glob("*/*/subagents/agent-*.jsonl"))
    out, notes = [], []
    parsed = {}
    for f in files:
        lines, why = read_lines(f)
        parsed[f] = (lines, why)

    def rel(p):
        return str(p.relative_to(root))

    def meta_of(f):
        if f.parent.name != "subagents":
            return {}
        mp = f.with_suffix(".meta.json")
        if not mp.exists():
            return {}
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
            if not isinstance(m, dict):
                raise ValueError("not a JSON object")
            return m
        except (OSError, ValueError) as e:
            notes.append(f"# SKIPPED file {rel(mp)}: {e}")
            return {}

    metas = {f: meta_of(f) for f in files}
    memo = {}

    def own_rules(f):
        lines = parsed[f][0]
        text = first_prompt(lines)
        refs = prompt_refs(text)
        if len(refs) == 1:
            return next(iter(refs)), None
        return None, text

    def issue_of(f, seen=()):
        """((repo, n), None) or (None, reason)."""
        if f in memo:
            return memo[f]
        res = None
        ref, text = own_rules(f)
        if ref:
            res = (ref, None)
        else:
            parent = metas[f].get("parentAgentId")
            if isinstance(parent, str) and f.parent.name == "subagents" and f not in seen:
                pf = f.parent / f"agent-{parent}.jsonl"
                if pf in parsed and parsed[pf][0] is not None:
                    pres = issue_of(pf, seen + (f,))
                    if pres[0]:
                        res = pres
            if res is None:
                nums = set()
                for d in parsed[f][0]:
                    b = d.get("gitBranch")
                    mm = BRANCH.match(b) if isinstance(b, str) else None
                    if mm:
                        nums.add(int(mm.group(1)))
                if not nums:
                    res = (None, "no-reference")
                elif len(nums) > 1:
                    res = (None, "several-branches")
                else:
                    repos = prompt_repos(text)
                    if len(repos) == 1:
                        res = ((next(iter(repos)), next(iter(nums))), None)
                    else:
                        res = (None, "no-repository")
        memo[f] = res
        return res

    class Row:
        def __init__(self):
            self.msgs = 0
            self.tok = [0, 0, 0, 0]
            self.usd = Decimal(0)
            self.priced = True

    rows = defaultdict(Row)
    price_skips = defaultdict(int)
    versions = set()

    for f in files:
        lines, why = parsed[f]
        if lines is None:
            notes.append(f"# SKIPPED file {rel(f)}: {why}")
            continue
        day = first_date(lines)
        if day is None:
            notes.append(f"# SKIPPED file {rel(f)}: no usable timestamp on the first line")
            continue
        if not since <= day <= until:
            continue
        for d in lines:
            if isinstance(d.get("version"), str):
                versions.add(d["version"])
        issue, reason = issue_of(f)
        seen_ids = set()
        missing = defaultdict(int)
        for d in lines:
            if d.get("type") != "assistant" or not isinstance(d.get("message"), dict):
                continue
            m = d["message"]
            usage = m.get("usage")
            gap = None
            if not isinstance(m.get("id"), str):
                gap = "message.id"
            elif not isinstance(m.get("model"), str):
                gap = "message.model"
            elif not isinstance(usage, dict):
                gap = "message.usage"
            else:
                for field in TOKEN_FIELDS:
                    if not isinstance(usage.get(field), int) or isinstance(usage.get(field), bool):
                        gap = f"usage.{field}"
                        break
            if gap:
                missing[gap] += 1
                continue
            if m["id"] in seen_ids:
                continue
            seen_ids.add(m["id"])
            tok = [usage[x] for x in TOKEN_FIELDS]
            if not any(tok):
                continue
            model = m["model"]
            agent = d.get("attributionAgent")
            if not isinstance(agent, str) or not agent:
                agent = metas[f].get("agentType")
            if not isinstance(agent, str) or not agent:
                agent = "-"
            key = (("T", issue[0], issue[1]) if issue else ("U", reason, None), agent, model)
            row = rows[key]
            row.msgs += 1
            for i, v in enumerate(tok):
                row.tok[i] += v
            # price
            why_not = None
            p = models.get(model)
            cc = usage.get("cache_creation")
            w5 = w1 = Decimal(0)
            if p is None:
                why_not = "not in price table"
            elif usage.get("speed", "standard") != "standard" or usage.get("service_tier", "standard") != "standard":
                why_not = "speed or service_tier is not standard"
            elif tok[2] > 0:
                if (isinstance(cc, dict) and isinstance(cc.get("ephemeral_5m_input_tokens"), int)
                        and isinstance(cc.get("ephemeral_1h_input_tokens"), int)
                        and cc["ephemeral_5m_input_tokens"] + cc["ephemeral_1h_input_tokens"] == tok[2]):
                    w5, w1 = Decimal(cc["ephemeral_5m_input_tokens"]), Decimal(cc["ephemeral_1h_input_tokens"])
                else:
                    why_not = "no cache write breakdown"
            if why_not:
                row.priced = False
                price_skips[(model, why_not)] += 1
            else:
                row.usd += (tok[0] * p["input"] + tok[1] * p["output"] + w5 * p["cache_write_5m"]
                            + w1 * p["cache_write_1h"] + tok[3] * p["cache_read"]) / MILLION
        for gap, k in sorted(missing.items()):
            notes.append(f"# SKIPPED fields {rel(f)}: {k} assistant messages without {gap}")

    def usd(r):
        return f"{r.usd:.4f}" if r.priced else "-"

    def counts(r):
        return [str(r.msgs)] + [str(x) for x in r.tok]

    out.append(f"# issue-cost.py prices as_of {as_of} source {source}; estimate at list prices, not a bill")
    out.append("# versions " + (",".join(sorted(versions)) if versions else "-"))
    per_issue = {}
    for key in sorted((k for k in rows if k[0][0] == "T"), key=lambda k: (k[0][1], k[0][2], k[1], k[2])):
        r = rows[key]
        (_, repo, n), agent, model = key
        out.append("\t".join(["TOKENS", repo, str(n), agent, model] + counts(r) + [usd(r)]))
        acc = per_issue.setdefault((repo, n), Row())
        acc.msgs += r.msgs
        acc.tok = [a + b for a, b in zip(acc.tok, r.tok)]
        acc.usd += r.usd
        acc.priced = acc.priced and r.priced
    for (repo, n), r in sorted(per_issue.items()):
        out.append("\t".join(["ISSUE", repo, str(n)] + counts(r) + [usd(r)]))
    for key in sorted((k for k in rows if k[0][0] == "U"), key=lambda k: (k[0][1], k[1], k[2])):
        r = rows[key]
        out.append("\t".join(["UNMATCHED", key[0][1], key[1], key[2]] + counts(r) + [usd(r)]))
    out.extend(sorted(notes))
    for (model, why_not), k in sorted(price_skips.items()):
        out.append(f"# SKIPPED price {model}: {k} messages ({why_not})")
    print("\n".join(out))


if __name__ == "__main__":
    main()
