"""Gate: the plugin's documents agree with the plugin on disk (one fact - one place).

The versioned files are primary: `.claude-plugin/plugin.json` version == `kernel.json` kernel_version == the version in the
README.md and TOOLS.md headers (and the forge-rules index header); the last kernel.json changelog entry is the current
version; the counts and lists README.md states (rules K1-Kn, N domains, and the skills, agents, gates and tools of its file
tree - each listed once, none missing, none invented) equal what is on disk / in CONSTITUTION.md / domains.json; every gate and
tool on disk has a row (first column) in TOOLS.md.

A statement the gate cannot find (the "rules K1-Kn" phrase, the "N domains" phrase, a tree entry) is a problem too: a document
that was reworded must not switch its own check off.

It checks the plugin SOURCE only: when the plugin folder is not inside the project tree (an installed project) it is a no-op.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

SKIP_TOOLS = {"forge_common", "__init__"}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _json(p: Path):
    try:
        return json.loads(_read(p))
    except ValueError:
        return None


def in_project_tree(plugin: Path, root: Path) -> bool:
    try:
        plugin.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def tree_block(readme: str, folder: str) -> str:
    """Text of the README tree entry `├── <folder>/ ...` including its continuation lines (those that start with │)."""
    lines, out, on = readme.splitlines(), [], False
    for ln in lines:
        if re.match(r"^[├└]── ", ln):
            if on:
                break
            on = ln[4:].lstrip().startswith(folder + "/")
        if on:
            out.append(ln)
    return "\n".join(out)


def _names(block: str) -> list[str]:
    """First word of each `·`-separated item of a tree entry (the entry's own `folder/` label and `(notes)` dropped)."""
    text = re.sub(r"\([^)]*\)", " ", block)
    text = re.sub(r"^[├└]── \S+\s+", "", text.replace("│", " "))
    out = []
    for seg in re.split(r"·|\n", text):
        m = re.match(r"\s*([A-Za-z0-9_-]+)", seg)
        if m:
            out.append(m.group(1))
    return out


def _gate_names(block: str) -> set[str]:
    """Gate names of the README `gates/` entry; `gate_encoding/size/json` is shorthand for gate_encoding, gate_size, gate_json."""
    text = re.sub(r"\([^)]*\)", " ", block).replace("│", " ")
    text = re.sub(r"^[├└]── \S+\s+", "", text)
    out: set[str] = set()
    for tok in re.findall(r"[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*", text):
        parts = tok.split("/")
        base = parts[0]
        for i, part in enumerate(parts):
            out.add(base if i == 0 else base.rsplit("_", 1)[0] + "_" + part)
    return out


def tools_md_rows(tools_md: str) -> set[str]:
    """Names of the scripts that have a table row in TOOLS.md: the `x.py` words of the FIRST column of each row. A name that
    only appears in prose, in a later column or inside another name (setup.py in gpt_setup.py) does not count."""
    out: set[str] = set()
    for ln in tools_md.splitlines():
        if ln.startswith("|"):
            cells = ln.split("|")
            if len(cells) > 1:
                out.update(re.findall(r"(?<![A-Za-z0-9_])([A-Za-z0-9_]+)\.py\b", cells[1]))
    return out


def _tree_lists(readme: str, problems: list[str], listing: dict[str, set[str]], skip: set[str] = frozenset()) -> None:
    """README tree entries of skills/, agents/, tools/ against the disk: every item once, none missing, none invented."""
    for folder, real in listing.items():
        block = tree_block(readme, folder)
        if not block:
            problems.append(f"README.md: no `{folder}/` entry in the file tree, expected one listing the {folder} on disk")
            continue
        names = _names(block)
        listed = set(names)
        for n in sorted({x for x in names if names.count(x) > 1}):
            problems.append(f"README.md: {folder}/ entry lists {n!r} more than once, expected it once")
        for n in sorted(real - listed - skip):
            problems.append(f"README.md: {folder}/ entry does not list {n!r}, expected it (exists on disk)")
        for n in sorted(listed - real):
            problems.append(f"README.md: {folder}/ entry lists {n!r}, expected it to exist in {folder}/")


def check_plugin(plugin: Path) -> list[str]:
    """All document-vs-disk problems of the plugin folder `plugin`; each names the file and the expected value."""
    problems: list[str] = []
    pj = _json(plugin / ".claude-plugin" / "plugin.json") or {}
    kj = _json(plugin / "kernel.json") or {}
    version = kj.get("kernel_version")
    if not version:
        return ["kernel.json: kernel_version missing"]
    if pj.get("version") != version:
        problems.append(f".claude-plugin/plugin.json: version is {pj.get('version')!r}, expected {version!r} (kernel.json kernel_version)")
    log = kj.get("changelog") or []
    if not log or log[-1].get("version") != version:
        problems.append(f"kernel.json: last changelog entry is {log[-1].get('version') if log else None!r}, expected {version!r}")
    readme, tools_md = _read(plugin / "README.md"), _read(plugin / "TOOLS.md")
    rules_md = _read(plugin / "skills" / "forge-rules" / "SKILL.md")
    heads = [("README.md", readme, r"^# Forge plugin v(\d[\w.\-+]*)"),
             ("TOOLS.md", tools_md, r"^# .*?\bv(\d[\w.\-+]*)\)"),
             ("skills/forge-rules/SKILL.md", rules_md, r"^# .*?plugin (\d[\w.\-+]*)")]
    for name, text, pat in heads:
        m = re.search(pat, text, re.M)
        if not m:
            problems.append(f"{name}: no version in the header, expected {version!r}")
        elif m.group(1) != version:
            problems.append(f"{name}: header names version {m.group(1)!r}, expected {version!r}")
    # rule count: README "rules K1-Kn" vs the highest **Kn** in CONSTITUTION.md
    ids = {int(x) for x in re.findall(r"\*\*K(\d+)\b", _read(plugin / "CONSTITUTION.md"))}
    m = re.search(r"rules K1[–-]K(\d+)", readme)
    if not ids:
        problems.append("CONSTITUTION.md: no rule ids (**Kn**) found, expected K1..Kn")
    else:
        if not m:
            problems.append(f"README.md: no 'rules K1-Kn' statement found, expected 'rules K1-K{max(ids)}' (CONSTITUTION.md)")
        elif int(m.group(1)) != max(ids):
            problems.append(f"README.md: states rules K1-K{m.group(1)}, expected K1-K{max(ids)} (CONSTITUTION.md)")
        if sorted(ids) != list(range(1, max(ids) + 1)):
            gaps = sorted(set(range(1, max(ids) + 1)) - ids)
            problems.append(f"CONSTITUTION.md: rule ids are not contiguous from K1, missing {['K%d' % g for g in gaps]}")
    # domains
    dom = (_json(plugin / "domains.json") or {}).get("domains")
    m = re.search(r"(\d+) domains", readme)
    if not isinstance(dom, dict):
        problems.append("domains.json: no 'domains' object found, expected the skills map")
    elif not m:
        problems.append(f"README.md: no 'N domains' statement found, expected '{len(dom)} domains' (domains.json)")
    elif int(m.group(1)) != len(dom):
        problems.append(f"README.md: states {m.group(1)} domains, expected {len(dom)} (domains.json)")
    # skills, agents and tools: the README tree lists exactly the folders / files on disk
    skills = {s.parent.name for s in (plugin / "skills").glob("*/SKILL.md")}
    agents = {a.stem for a in (plugin / "agents").glob("*.md")}
    gates = {g.stem for g in (plugin / "gates").glob("*.py")} - {"__init__"}
    tools = {x.stem for x in (plugin / "tools").glob("*.py")} - {"__init__"}
    _tree_lists(readme, problems, {"skills": skills, "agents": agents, "tools": tools})
    # gates: names in the README tree must exist (a gate_/check_/run_/gpt_ name), and every gate on disk must be in the tree
    gblock = tree_block(readme, "gates")
    if not gblock:
        problems.append("README.md: no `gates/` entry in the file tree, expected one listing the gates on disk")
    else:
        listed = _gate_names(gblock)
        for name in sorted(listed):
            if name.startswith(("gate_", "check_", "run_", "gpt_")) and name not in gates:
                problems.append(f"README.md: gates/ entry names {name!r}, expected gates/{name}.py to exist")
        for g in sorted(gates - listed):
            problems.append(f"README.md: gates/ entry does not list {g!r}, expected it (exists on disk)")
    # every gate and tool on disk has a row in TOOLS.md
    rows = tools_md_rows(tools_md)
    for g in sorted(gates):
        if g not in rows:
            problems.append(f"TOOLS.md: gate {g}.py is not listed, expected a row for it")
    for t in sorted(tools - SKIP_TOOLS):
        if t not in rows:
            problems.append(f"TOOLS.md: tool {t}.py is not listed, expected a row for it")
    return problems


def check(cfg: dict) -> list[str]:
    plugin = Path(cfg["_kernel_dir"])
    if not in_project_tree(plugin, Path(cfg["_root"])):
        return []  # installed project: the plugin is not in the project tree
    return check_plugin(plugin)


if __name__ == "__main__":
    probs = check_plugin(Path(__file__).resolve().parents[1])
    print("\n".join(probs) or "OK")
    sys.exit(1 if probs else 0)
