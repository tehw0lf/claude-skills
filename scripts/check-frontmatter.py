#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Check the front matter of every plugin agent (plugins/*/agents/**/*.md) and skill
(plugins/*/skills/*/SKILL.md): delimiters, strict YAML parse (repeated keys are errors), a mapping,
`name` and `description` non-empty strings, keys against the documented field sets, `model` and
`effort` against fixed values, and the shape (not the existence) of tool names.
usage: check-frontmatter.py [<repo-root>]      default: .
Output: one line per finding, `<path>\t<key or ->\t<message>`. Exit 1 on any finding, 0 on none,
2 when the root is missing or holds no agent or skill (a check that found nothing must not pass).
Why: `claude plugin validate` reads plugin.json only, and Claude Code ignores an unknown or
misspelled key silently at run time. The key and value lists below are copied from the Claude Code
documentation; when it adds a field or a value, add it here (one line)."""
import collections.abc, re, sys, pathlib
import yaml

COMMON = {"name", "description", "model", "effort", "background"}
AGENT_KEYS = COMMON | {"tools", "disallowedTools", "maxTurns", "skills", "memory", "omitClaudeMd",
                       "isolation", "color", "experimental"}
# Documented agent fields that Claude Code ignores for agents shipped in a plugin.
AGENT_IGNORED = {"hooks", "mcpServers", "permissionMode", "initialPrompt"}
SKILL_KEYS = COMMON | {"when_to_use", "argument-hint", "arguments", "disable-model-invocation",
                       "user-invocable", "allowed-tools", "disallowed-tools", "context", "agent",
                       "hooks", "paths", "shell", "metadata", "license", "compatibility"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
MODELS = {"sonnet", "opus", "haiku", "fable", "inherit", "default", "best", "opusplan"}
# an alias or a full id, optionally with the 1M-context suffix
MODEL_ID = re.compile(r"^(claude-[a-z0-9.-]+|sonnet|opus)(\[1m\])?$")
TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\(.+\))?$")


class Loader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    seen = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=True)
        if not isinstance(key, collections.abc.Hashable):
            raise yaml.constructor.ConstructorError(None, None, "unhashable mapping key", k.start_mark)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", k.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def split_tools(s):
    parts, cur, depth = [], "", 0
    for ch in s:
        depth += ch == "("
        depth -= ch == ")"
        if depth <= 0 and (ch == "," or ch.isspace()):
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    # whitespace runs give empty parts; a comma gives one too, so only commas can make a real empty entry
    return parts


def tool_entries(s):
    # split on commas first so that an empty entry between commas is seen, then on whitespace
    out, depth, cur, entries = [], 0, "", []
    for ch in s:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth <= 0:
            entries.append(cur)
            cur = ""
        else:
            cur += ch
    entries.append(cur)
    for e in entries:
        e = e.strip()
        if not e:
            out.append("")
            continue
        out.extend(p for p in split_tools(e) if p)
    return out


def check_tools(key, val, find):
    if isinstance(val, str):
        items = tool_entries(val)
    elif isinstance(val, list) and all(isinstance(x, str) for x in val):
        items = val
    else:
        return find(key, "must be a string or a list of strings")
    for it in items:
        if not TOOL.match(it):
            find(key, f"invalid tool name {it!r}")


def check(path, kind):
    out = []
    find = lambda key, msg: out.append((key, msg))
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        find("-", "file must start with a --- line")
        return out
    end = next((i for i, l in enumerate(lines[1:], 1) if l.rstrip() == "---"), None)
    if end is None:
        find("-", "front matter is not closed by a --- line")
        return out
    try:
        fm = yaml.load("\n" + "\n".join(lines[1:end]), Loader=Loader)
    except yaml.YAMLError as e:
        find("-", "front matter does not parse: " + " ".join(str(e).split()))
        return out
    if not isinstance(fm, dict):
        find("-", "front matter is not a mapping")
        return out
    for k in ("name", "description"):
        if k not in fm:
            find(k, "missing")
        elif not isinstance(fm[k], str) or not fm[k].strip():
            find(k, "must be a non-empty string")
    allowed = AGENT_KEYS if kind == "agent" else SKILL_KEYS
    for k in fm:
        if kind == "agent" and k in AGENT_IGNORED:
            find(k, "ignored for agents that come from a plugin")
        elif k not in allowed:
            find(str(k), f"unknown {kind} key (ignored silently at run time)")
    if kind == "agent" and isinstance(fm.get("name"), str) and ":" in fm["name"]:
        find("name", "must not contain ':'")
    if "model" in fm:
        m = fm["model"]
        if not isinstance(m, str) or not (m in MODELS or MODEL_ID.match(m)):
            find("model", f"unknown model {m!r}")
    if "effort" in fm and not (isinstance(fm["effort"], str) and fm["effort"] in EFFORTS):
        find("effort", f"unknown effort {fm['effort']!r}")
    tool_keys = ("tools", "disallowedTools") if kind == "agent" else ("allowed-tools", "disallowed-tools")
    for k in tool_keys:
        if k in fm:
            check_tools(k, fm[k], find)
    if kind == "skill" and "argument-hint" in fm:
        h = fm["argument-hint"]
        if not (isinstance(h, str) or (isinstance(h, list) and all(isinstance(x, str) for x in h))):
            find("argument-hint", "must be a string or a list of strings")
    return out


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    if not root.is_dir():
        print(f"root not found: {root}", file=sys.stderr)
        sys.exit(2)
    files = [(p, "agent") for p in sorted(root.glob("plugins/*/agents/**/*.md"))]
    files += [(p, "skill") for p in sorted(root.glob("plugins/*/skills/*/SKILL.md"))]
    if not files:
        print(f"no agent or skill file found under {root}", file=sys.stderr)
        sys.exit(2)
    bad = False
    for p, kind in files:
        for key, msg in check(p, kind):
            bad = True
            print(f"{p.relative_to(root)}\t{key}\t{msg}")
    sys.exit(1 if bad else 0)


main()
