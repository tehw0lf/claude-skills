#!/usr/bin/env python3
"""One row per issue: the statistics of issue-stats.py joined with the tokens and estimated cost of issue-cost.py.
usage: issue-report.py --stats <file> --cost [<label>=]<file> [--cost [<label>=]<file> ...]
  --stats  the saved output of scripts/issue-stats.py, exactly once
  --cost   the saved output of scripts/issue-cost.py, once per machine (the transcripts exist on one machine
           only), at least once. `<label>=<file>` names the file in the output and splits on the first `=`; without a
           label the label is the path as given, or `stdin` for `-`. Labels have to differ (a path that contains `=`
           needs an explicit label)
  `-` reads standard input and may be used at most once, for --stats or for one --cost.
Read-only, stdlib only: no network, no gh, no subprocess. It reads the files it is given and calls neither
issue-stats.py nor issue-cost.py, so the two runs, their ranges and the machines are the caller's choice. Use the
same --since/--until for both, otherwise issues fall into STATSONLY and COSTONLY.
The join key is (owner/repo lowercased, n): issue-cost.py lowercases the repository (prompts are free text),
issue-stats.py prints it in GitHub's case, which the joined rows keep. The `TOKENS` rows of all cost files are summed
per key, agent and model; the `ISSUE` lines of the cost files are checked for format and not used, because only
the `TOKENS` rows carry agent and model. A money figure is the estimate of issue-cost.py, not a bill: a row whose
usd is `-` is never counted as 0. An issue total is `-` when any of its rows is `-`. Means and medians of usd use the
issues with a priced total only, `priced` says how many. A TOTAL line sums the usd of the priced rows and gives the
messages of the unpriced rows separately.
Input is checked, not guessed at: the first line of --stats must be the header of issue-stats.py (`# owner `), the
first line of a cost file the header of issue-cost.py (`# issue-cost.py prices`), every other line either starts
with `#` or is one of the accepted input kinds with its field count and numeric fields (stats: `ISSUE`, `UNPARSED`, `QROUNDS`,
`OUTCOME`, `ROUNDS`, `SUMMARY`; cost: `TOKENS`, `ISSUE`, `UNMATCHED`, `SESSION`), and a stats `ISSUE` key may not
appear twice. Lines starting with `#` that are not `# SKIPPED` are ignored as commentary; `# SKIPPED` lines and the
`# versions` line of a cost file are passed on.
Output, tab separated; lines starting with `#` are the header and notes:
  # issue-report.py stats <label> cost <label>,<label>...
  # cost <label> <the `issue-cost.py prices` and `versions` lines of that file, without their `# `>
  # <label> SKIPPED ...                      a `# SKIPPED` line of an input, with the label of the file
  # DUPLICATE sessions: ...                  only when a DUPLICATE line follows: the totals may count them twice
  ISSUE      <owner/repo> <n> <state> <outcome> <reason> <qrounds> <plans> <rounds> <findings> <messages> <input>
             <output> <cache_write> <cache_read> <usd or ->
             the issues present on both sides, the repository as issue-stats.py printed it
  AGENT      <owner/repo> <n> <agent> <model> <messages> <input> <output> <cache_write> <cache_read> <usd or ->
             the summed TOKENS rows of each joined issue
  STATSONLY  <owner/repo> <n> <state> <outcome> <reason> <qrounds> <plans> <rounds> <findings>
  COSTONLY   <owner/repo> <n> <messages> <input> <output> <cache_write> <cache_read> <usd or ->
             rows without a partner are shown, never dropped
  UNMATCHED  <reason> <agent> <model> <messages> <input> <output> <cache_write> <cache_read> <usd or ->
             summed over the cost files
  DUPLICATE  <session id> <labels, comma separated>
             the same session id in the SESSION lines of more than one cost file (the same id twice in one file
             is a main and a subagent transcript of one session and does not count). The rows are still summed
  TOTAL      <joined|costonly|unmatched|all> <messages> <input> <output> <cache_write> <cache_read>
             <usd of the priced rows> <messages of the unpriced rows>
  SHARE      unmatched <usd of the unmatched priced rows / usd of all priced rows, or ->
  SUMMARY    <merged|not-merged|all> <name> <value>   over the joined issues of the group (`merged` = outcome
             merged); names: issues, priced, mean_usd, median_usd (priced issues only), mean_rounds, median_rounds,
             mean_question_rounds, median_question_rounds (all issues of the group); `-` where there is nothing
  AGENTMEAN  <group> <agent> <issues with that agent> <mean messages> <mean input> <mean output> <mean cache_write>
             <mean cache_read>      averaged over all joined issues of the group, an issue without the agent
             counting as 0, so the means of the agents add up to the mean of the issue
Money is computed with Decimal and printed with 4 decimals, means and medians of counts with 2.
Exit 0 on a completed run, 1 when an input cannot be read or is malformed (the message names the label and the
line), 2 on invalid arguments."""
import argparse, re, statistics, sys
from collections import defaultdict
from decimal import Decimal

NUM = re.compile(r"\d+")
USD = re.compile(r"-|\d+(?:\.\d+)?")
# line kind -> (field count, indexes of integer fields, indexes of usd fields)
COST_KINDS = {
    "TOKENS": (11, (2, 5, 6, 7, 8, 9), (10,)),
    "ISSUE": (9, (2, 3, 4, 5, 6, 7), (8,)),
    "UNMATCHED": (10, (4, 5, 6, 7, 8), (9,)),
    "SESSION": (5, (4,), ()),
}
STATS_KINDS = {
    "ISSUE": (12, (2, 5, 6, 10), ()),
    "UNPARSED": (5, (2, 3, 4), ()),
    "QROUNDS": (3, (1, 2), ()),
    "OUTCOME": (3, (2,), ()),
    "ROUNDS": (3, (1, 2), ()),
    "SUMMARY": (3, (), ()),
}


def die(msg, code=1):
    print(f"issue-report.py: {msg}", file=sys.stderr)
    sys.exit(code)


def parse_arg(spec):
    """(label, path) of a --cost value."""
    label, path = None, spec
    if "=" in spec:
        label, path = spec.split("=", 1)
        if not label:
            die(f"empty label in --cost {spec}", 2)
    if not path:
        die(f"empty file in --cost {spec}", 2)
    if label is None:
        label = "stdin" if path == "-" else path
    if re.search(r"[\t\r\n]", label):
        die("a label may not contain a tab or a line break", 2)
    return label, path


def read_input(label, path):
    try:
        if path == "-":
            return sys.stdin.read().splitlines()
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        die(f"{label}: cannot read: {e}")


def parse_input(label, lines, kinds, header_start, header_name):
    """([(kind, fields)], [notes passed on], [header lines kept]) or exit 1."""
    records, notes, kept = [], [], []
    first = next(((k, x) for k, x in enumerate(lines, 1) if x.strip()), None)
    if first is None or not first[1].startswith(header_start):
        die(f"{label}: line {first[0] if first else 1}: not the output of {header_name} (expected a first line starting with `{header_start}`)")
    for k, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        if raw.startswith("#"):
            if raw.startswith("# SKIPPED"):
                notes.append(f"# {label} " + raw[2:])
            elif k == first[0] or raw.startswith("# versions "):
                kept.append(raw[2:])
            continue
        f = raw.split("\t")
        spec = kinds.get(f[0])
        if spec is None:
            die(f"{label}: line {k}: unknown line kind `{f[0]}`")
        count, ints, usds = spec
        if len(f) != count:
            die(f"{label}: line {k}: {f[0]} has {len(f)} fields, expected {count}")
        for i in ints:
            if not NUM.fullmatch(f[i]):
                die(f"{label}: line {k}: field {i + 1} of {f[0]} is not a number: {f[i]!r}")
        for i in usds:
            if not USD.fullmatch(f[i]):
                die(f"{label}: line {k}: field {i + 1} of {f[0]} is not a number or `-`: {f[i]!r}")
        records.append((f[0], f, k))
    return records, notes, kept


class Sum:
    """Counts and money of rows; usd None as soon as one row is unpriced."""

    def __init__(self):
        self.msgs, self.tok, self.usd, self.priced = 0, [0, 0, 0, 0], Decimal(0), True

    def add(self, msgs, tok, usd):
        self.msgs += msgs
        self.tok = [a + b for a, b in zip(self.tok, tok)]
        if usd is None:
            self.priced = False
        else:
            self.usd += usd

    def cols(self):
        return [str(self.msgs)] + [str(x) for x in self.tok] + [self.usd_text()]

    def usd_text(self):
        return f"{self.usd:.4f}" if self.priced else "-"


def num(text):
    return int(text)


def usd_of(text):
    return None if text == "-" else Decimal(text)


def mean(values, places=2):
    return "-" if not values else f"{sum(values) / len(values):.{places}f}"


def median(values, places=2):
    return "-" if not values else f"{statistics.median(values):.{places}f}"


def main():
    ap = argparse.ArgumentParser(prog="issue-report.py", description="join the output of issue-stats.py and issue-cost.py per issue")
    ap.add_argument("--stats", action="append", default=[], metavar="FILE")
    ap.add_argument("--cost", action="append", default=[], metavar="[LABEL=]FILE")
    args = ap.parse_args()
    if len(args.stats) != 1:
        die("--stats is needed exactly once", 2)
    if not args.cost:
        die("--cost is needed at least once", 2)
    stats_label, stats_path = ("stdin" if args.stats[0] == "-" else args.stats[0]), args.stats[0]
    if re.search(r"[\t\r\n]", stats_label):
        die("a path may not contain a tab or a line break", 2)
    costs = [parse_arg(spec) for spec in args.cost]
    labels = [label for label, _ in costs]
    for label in labels:
        if labels.count(label) > 1:
            die(f"two --cost files have the label `{label}`: give each one `<label>=<file>`", 2)
    if sum(1 for p in [stats_path] + [p for _, p in costs] if p == "-") > 1:
        die("`-` (standard input) may be used once", 2)

    notes, header = [], []
    srecs, snotes, _ = parse_input(stats_label, read_input(stats_label, stats_path), STATS_KINDS, "# owner ", "issue-stats.py")
    notes += snotes
    stats = {}
    for kind, f, k in srecs:
        if kind != "ISSUE":
            continue
        key = (f[1].lower(), int(f[2]))
        if key in stats:
            die(f"{stats_label}: line {k}: the issue {f[1]}#{f[2]} appears twice")
        stats[key] = f

    tokens = defaultdict(Sum)  # (repo, n, agent, model)
    unmatched = defaultdict(Sum)  # (reason, agent, model)
    sessions = defaultdict(set)  # session id -> labels
    rows_all = []  # (kind, key, msgs, tok, usd) of every counted row, for the totals
    for label, path in costs:
        crecs, cnotes, kept = parse_input(label, read_input(label, path), COST_KINDS, "# issue-cost.py prices", "issue-cost.py")
        notes += cnotes
        header += [f"# cost {label} {line}" for line in kept]
        for kind, f, k in crecs:
            if kind == "TOKENS":
                msgs, tok, usd = num(f[5]), [num(x) for x in f[6:10]], usd_of(f[10])
                tokens[(f[1].lower(), int(f[2]), f[3], f[4])].add(msgs, tok, usd)
                rows_all.append(("T", (f[1].lower(), int(f[2])), msgs, tok, usd))
            elif kind == "UNMATCHED":
                msgs, tok, usd = num(f[4]), [num(x) for x in f[5:9]], usd_of(f[9])
                unmatched[(f[1], f[2], f[3])].add(msgs, tok, usd)
                rows_all.append(("U", None, msgs, tok, usd))
            elif kind == "SESSION":
                sessions[f[1]].add(label)

    per_issue = defaultdict(Sum)
    agents = defaultdict(dict)  # key -> {agent: Sum over models}
    agent_rows = defaultdict(list)
    for (repo, n, agent, model), s in sorted(tokens.items()):
        per_issue[(repo, n)].add(s.msgs, s.tok, s.usd if s.priced else None)
        agent_rows[(repo, n)].append((agent, model, s))
        a = agents[(repo, n)].setdefault(agent, Sum())
        a.add(s.msgs, s.tok, s.usd if s.priced else None)

    joined = sorted(set(stats) & set(per_issue))
    duplicates = sorted((sid, [l for l in labels if l in ls]) for sid, ls in sessions.items() if len(ls) > 1)

    out = [f"# issue-report.py stats {stats_label} cost {','.join(labels)}"]
    out += header
    out += sorted(notes)
    if duplicates:
        out.append("# DUPLICATE sessions: the same session id is in more than one cost file; its rows are summed, so the totals may count it twice")

    def stats_cols(f):
        return [f[3], f[7], f[8], f[5], f[6], f[10], f[11]]

    for key in joined:
        f = stats[key]
        out.append("\t".join(["ISSUE", f[1], f[2]] + stats_cols(f) + per_issue[key].cols()))
    for key in joined:
        for agent, model, s in agent_rows[key]:
            out.append("\t".join(["AGENT", stats[key][1], stats[key][2], agent, model] + s.cols()))
    for key in sorted(set(stats) - set(per_issue)):
        f = stats[key]
        out.append("\t".join(["STATSONLY", f[1], f[2]] + stats_cols(f)))
    for key in sorted(set(per_issue) - set(stats)):
        out.append("\t".join(["COSTONLY", key[0], str(key[1])] + per_issue[key].cols()))
    for (reason, agent, model), s in sorted(unmatched.items()):
        out.append("\t".join(["UNMATCHED", reason, agent, model] + s.cols()))
    for sid, ls in duplicates:
        out.append("\t".join(["DUPLICATE", sid, ",".join(ls)]))

    joined_keys = set(joined)
    groups = {
        "joined": lambda kind, key: kind == "T" and key in joined_keys,
        "costonly": lambda kind, key: kind == "T" and key not in joined_keys,
        "unmatched": lambda kind, key: kind == "U",
        "all": lambda kind, key: True,
    }
    usd_by = {}
    for name, pick in groups.items():
        msgs, tok, usd, unpriced = 0, [0, 0, 0, 0], Decimal(0), 0
        for kind, key, m, t, u in rows_all:
            if not pick(kind, key):
                continue
            msgs += m
            tok = [a + b for a, b in zip(tok, t)]
            if u is None:
                unpriced += m
            else:
                usd += u
        usd_by[name] = usd
        out.append("\t".join(["TOTAL", name, str(msgs)] + [str(x) for x in tok] + [f"{usd:.4f}", str(unpriced)]))
    out.append("SHARE\tunmatched\t" + ("-" if not usd_by["all"] else f"{usd_by['unmatched'] / usd_by['all']:.4f}"))

    by_group = {"merged": [], "not-merged": [], "all": list(joined)}
    for key in joined:
        by_group["merged" if stats[key][7] == "merged" else "not-merged"].append(key)
    for name, keys in by_group.items():
        priced = [per_issue[k].usd for k in keys if per_issue[k].priced]
        rounds = [int(stats[k][10]) for k in keys]
        qrounds = [int(stats[k][5]) for k in keys]
        for field, value in (
            ("issues", str(len(keys))), ("priced", str(len(priced))),
            ("mean_usd", mean(priced, 4)), ("median_usd", median(priced, 4)),
            ("mean_rounds", mean(rounds)), ("median_rounds", median(rounds)),
            ("mean_question_rounds", mean(qrounds)), ("median_question_rounds", median(qrounds)),
        ):
            out.append(f"SUMMARY\t{name}\t{field}\t{value}")
    for name, keys in by_group.items():
        names = sorted({a for k in keys for a in agents[k]})
        for agent in names:
            have = [agents[k][agent] for k in keys if agent in agents[k]]
            n = len(keys)
            cols = [sum(s.msgs for s in have)] + [sum(s.tok[i] for s in have) for i in range(4)]
            out.append("\t".join(["AGENTMEAN", name, agent, str(len(have))] + [f"{c / n:.2f}" for c in cols]))
    print("\n".join(out))


if __name__ == "__main__":
    main()
