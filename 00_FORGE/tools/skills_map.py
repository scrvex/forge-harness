"""Skills map (K32-K33): which skill leads each stage of a domain, what is installed, whether a third-party skill is safe.

    skills_map.py list                                   # domain ids and names
    skills_map.py detect "сделай лендинг с анимацией"    # domain(s) of a task by trigger words (0 tokens) -> cards to read
    skills_map.py show invest[,analytics]                # the card: pipeline, primary skill per stage, support, tools,
                                                         # quality bar, verify-by-running, conflicts, gaps (+ combo order)
    skills_map.py audit [--available-file f] [--md out.md] [--json] [--home DIR] [--project DIR]
                                                         # inventory: catalog skills available / missing, tools on PATH,
                                                         # duplicates and overlapping descriptions, hooks/MCP of plugins
    skills_map.py vet <folder> [--json]                  # static vetting of a third-party skill: licence (K16),
                                                         # executables, hooks, MCP, network, keys, instruction overrides
    skills_map.py profile <project> [--available-file f] [--apply-file f] [--restore] [--json]
                                                         # 0.9.3: plugin sets the project's domains use vs the rest;
                                                         # ledger <state>/skill_profile.json; prints the
                                                         # .claude/settings.local.json snippet - never writes settings

The catalog is `<plugin>/domains.json`. Skills provided by the app or Cowork are not on disk: pass the session's skill list
with --available-file (one per line, "name: description" or "- name: description"), or the audit counts disk skills only.
`vet` only reads files - nothing from the vetted folder is executed or imported. Statuses come from checks, not assumptions.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import PLUGIN_DIR  # noqa: E402

CATALOG = PLUGIN_DIR / "domains.json"
STATUS = {"tested": "Installed & Tested", "ready": "Installed, Not Tested", "config": "Requires Configuration",
          "partial": "Partly available", "gap": "Unavailable"}
STOP = set("""when user asks wants this that with from your skill also them into have their only about what more other
each does make after before should would could which using used uses file files trigger triggers phrases including says
like need needs mention mentions request requests work works task tasks help helps without within where there these those
then than they will just even more most must such very into onto over under through across given gives getting""".split())


def load(path: Path = CATALOG) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def base(name: str) -> str:
    return name.rsplit(":", 1)[-1]


# ---------------------------------------------------------------- detect / show / order block
LATIN_SUFFIX = r"(?:s|es|ed|ing|er|ers|or|ors|ment|ments|y|al|ic|ics|ation|ations)?"


def _trigger_re(t: str) -> re.Pattern:
    """Cyrillic triggers are stems: each word starts a word ("оценк компан" finds "оценка компании"); Cyrillic ones of up
    to 3 letters (код, веб) are whole words. Latin triggers are whole words plus an English ending ("invest" finds
    "investing", "investor", never "investigate"; "react" never "reaction"). A trailing "=" makes any trigger a whole word
    ("коде=" never "кодекс"). Slashes count as word edges (a/b vs src/a/b)."""
    whole = t.endswith("=")
    t = t.rstrip("=")
    if re.fullmatch(r"[a-z0-9/.+&' -]+", t) and not whole:
        tail = LATIN_SUFFIX + r"(?![\w/])"
    else:
        tail = r"(?![\w/])" if whole or len(t) <= 3 else ""
    return re.compile(r"(?<![\w/])" + r"\w*[\s-]+".join(re.escape(w) for w in t.split()) + tail, re.I | re.U)


def detect(text: str, cat: dict | None = None) -> list[dict]:
    cat = cat or load()
    low = text.lower()
    out = []
    for did, d in cat["domains"].items():
        hits = [t for t in d["triggers"] if _trigger_re(t.lower()).search(low)]
        if hits:
            out.append({"domain": did, "score": sum(2 if " " in h else 1 for h in hits), "hits": hits})
    return sorted(out, key=lambda x: -x["score"])


def ids_ok(ids: list[str], cat: dict) -> list[str]:
    bad = [i for i in ids if i not in cat["domains"]]
    if bad:
        raise SystemExit(f"unknown domain(s): {', '.join(bad)} - known: {', '.join(cat['domains'])}")
    return ids


def combos_for(ids: list[str], cat: dict) -> list[dict]:
    """Every combo whose domains are all in this order, except those contained in a bigger fitting one."""
    fits = [c for c in cat.get("combos", []) if set(c["domains"]) <= set(ids) and len(c["domains"]) > 1]
    return [c for c in fits if not any(set(c["domains"]) < set(o["domains"]) for o in fits)]


def tool_state(t: dict) -> str:
    """'on PATH' / 'found: <path>' (installed in a standard folder, not on PATH - e.g. Blender) / 'NOT found'."""
    if shutil.which(t["check"]):
        return "on PATH"
    import glob
    import os
    for pattern in t.get("find", []):  # e.g. "%ProgramFiles%/Blender Foundation/*/blender.exe"
        hits = sorted(glob.glob(os.path.expandvars(pattern)),  # natural order: "Blender 5.10" after "Blender 5.2"
                      key=lambda s: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)])
        if hits:
            return f"found: {hits[-1]}"
    return "NOT found"


def primaries(d: dict) -> list[str]:
    """Stage leaders: one skill per stage; a by-format stage (documents) contributes one skill per format."""
    out = []
    for s in d["pipeline"]:
        out += [s["skill"]] if s.get("skill") else [v for v in (s.get("by_format") or {}).values() if re.fullmatch(r"[\w.-]+:[\w.-]+", v)]
    return out


def card(did: str, cat: dict, plugin: str | None = None) -> str:
    d = cat["domains"][did]
    sub = (lambda s: s.replace("<plugin>", plugin)) if plugin else (lambda s: s)
    L = [f"domain: {did} - {d['name']}", f"where: {d['where']}", "pipeline (one primary skill per stage; '-' = Claude itself or a tool):"]
    for i, s in enumerate(d["pipeline"], 1):
        skill = s.get("skill") or "-"
        if s.get("by_format"):
            skill = "by format: " + ", ".join(f"{k} -> {v}" for k, v in s["by_format"].items())
        L.append(f"  {i}. {s['stage']} [{skill}] - {s['do']}")
    L.append("support menu (at most 2 per order): " + (", ".join(d["support"]) or "-"))
    if d["tools"]:
        L.append("tools:")
        for t in d["tools"]:
            state = "" if not t.get("check") else f" [{tool_state(t)}]"
            L.append(f"  - {t['name']}{state}" + (f" - {t['note']}" if t.get("note") else ""))
    L += ["quality bar:"] + [f"  - {q}" for q in d["quality"]]
    L += ["verify by running:"] + [f"  - {sub(v)}" for v in d["verify"]]
    if d["conflicts"]:
        L += ["conflicts (who leads):"] + [f"  - {' | '.join(c['items'])}: {c['rule']}" for c in d["conflicts"]]
    if d.get("forbidden"):
        L += ["forbidden:"] + [f"  - {f}" for f in d["forbidden"]]
    if d.get("gaps"):
        L += ["gaps:"] + [f"  - {g['need']} -> {g['fallback']}" + (f" (candidate: {g['candidate']})" if g.get("candidate") else "") for g in d["gaps"]]
    return "\n".join(L)


def order_block(ids: list[str], cat: dict | None = None) -> dict:
    """What order.py new writes into an order for its domains (K32): header line, quality lines, verify section."""
    cat = cat or load()
    ids_ok(ids, cat)
    skills = []
    for i in ids:
        skills += [s for s in primaries(cat["domains"][i]) if s not in skills]
    header = (f"- **Domain (K32):** {'+'.join(ids)} - primary skills: "
              + (", ".join(f"`{s}`" for s in skills) if skills else "Claude + tools")
              + f" - card: `python <plugin>/tools/skills_map.py show {','.join(ids)}` (pipeline, support, conflicts)")
    quality = [f"[{i}] {q}" for i in ids for q in cat["domains"][i]["quality"]]
    sec = ["## Verify by running (domain checks, K32 - the executor runs them, the reviewer checks the evidence)"]
    for c in combos_for(ids, cat):
        sec.append("- sequence: " + " -> ".join(c["sequence"]) + f" ({c['handoff']})")
    sec += [f"- [ ] [{i}] {v}" for i in ids for v in cat["domains"][i]["verify"]]
    forb = [f"- [{i}] {f}" for i in ids for f in cat["domains"][i].get("forbidden", [])]
    return {"ids": ids, "skills": skills, "header": header, "quality": quality, "section": "\n".join(sec) + "\n", "forbidden": forb}


# ---------------------------------------------------------------- inventory
def _frontmatter(md: Path) -> dict:
    text = md.read_text(encoding="utf-8-sig", errors="replace")
    fm = {"_words": len(text.split())}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip() in ("name", "description", "license") and v.strip():
                fm[k.strip()] = v.strip().strip("'\"")
            if k.strip() in ("allowed-tools", "hooks"):  # pre-approved tools / hooks that run when the skill loads
                fm.setdefault("_risky", []).append(k.strip())
    return fm


def installed(home: Path, project: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Skills on disk: user (~/.claude/skills), installed plugins (installed_plugins.json), the project (.claude/skills)."""
    skills, plugins, seen = [], [], set()

    def add(md: Path, name: str, source: str) -> None:
        fm = _frontmatter(md)
        skills.append({"name": name, "source": source, "description": fm.get("description", ""), "words": fm["_words"], "path": str(md.parent)})

    for md in sorted((home / ".claude" / "skills").glob("*/SKILL.md")):
        add(md, _frontmatter(md).get("name", md.parent.name), "user")
    ip = home / ".claude" / "plugins" / "installed_plugins.json"
    enabled: dict = {}  # enabledPlugins: user settings, then the project's (later wins); an explicit false disables
    for sf in [home / ".claude" / "settings.json"] + ([project / ".claude" / "settings.json", project / ".claude" / "settings.local.json"] if project else []):
        try:
            enabled.update(json.loads(sf.read_text(encoding="utf-8")).get("enabledPlugins") or {})
        except (OSError, ValueError, AttributeError, TypeError):  # missing, broken or not a JSON object: no overrides
            pass

    def in_scope(e: dict) -> bool:
        """User-scope installs apply everywhere; project/local ones only to their own project (N010 rung 3 #6)."""
        if e.get("scope", "user") == "user":
            return True
        try:
            return bool(project and e.get("projectPath") and Path(e["projectPath"]).resolve() == project.resolve())
        except OSError:
            return False

    if ip.is_file():
        try:
            data = json.loads(ip.read_text(encoding="utf-8"))
            plugin_map = (data.get("plugins", data) or {}) if isinstance(data, dict) else {}
        except (OSError, ValueError):
            plugin_map = {}
        for key, entries in plugin_map.items():
            if enabled.get(key) is False:
                continue
            for e in entries if isinstance(entries, list) else [entries]:
                p = Path(e.get("installPath", ""))
                if not in_scope(e) or not p.is_dir() or str(p) in seen:
                    continue
                seen.add(str(p))
                pname = key.split("@")[0]
                pj = p / ".claude-plugin" / "plugin.json"
                meta = json.loads(pj.read_text(encoding="utf-8")) if pj.is_file() else {}
                mds = sorted((p / "skills").glob("*/SKILL.md"))
                for md in mds:
                    add(md, f"{pname}:{_frontmatter(md).get('name', md.parent.name)}", f"plugin {key} {e.get('version', '')}")
                plugins.append({"plugin": key, "version": e.get("version", ""), "skills": len(mds),
                                "hooks": (p / "hooks" / "hooks.json").is_file() or "hooks" in meta,
                                "mcp": (p / ".mcp.json").is_file() or "mcpServers" in meta})
    if project:
        for md in sorted((project / ".claude" / "skills").glob("*/SKILL.md")):
            add(md, _frontmatter(md).get("name", md.parent.name), "project")
    return skills, plugins


def parse_available(path: Path) -> dict[str, str]:
    """The session's skill list as the Lead sees it: 'name: description' per line (a leading '- ' is fine)."""
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("-* ").strip()
        name, sep, desc = line.partition(": ")
        name = name.strip().rstrip(":")
        if re.fullmatch(r"[\w.:-]+", name or "-"):
            out[name] = desc.strip() if sep else ""
    return out


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zа-яё]{4,}", text.lower()) if w not in STOP}


def catalog_names(cat: dict) -> set[str]:
    names = set()
    for d in cat["domains"].values():
        names.update(primaries(d)); names.update(d["support"])
        for s in d["pipeline"]:
            names.update(v for v in (s.get("by_format") or {}).values() if re.fullmatch(r"[\w.-]+:[\w.-]+", v))
        for c in d["conflicts"]:
            names.update(c["items"])
    return names


def resolved(a: str, b: str, cat: dict, mapped: set[str]) -> str:
    for d in cat["domains"].values():
        for c in d["conflicts"]:
            if a in c["items"] and b in c["items"]:
                return "rule"
    return "roles" if a in mapped and b in mapped else ""


def audit(cat: dict, home: Path, project: Path | None, available: dict[str, str] | None) -> dict:
    disk, plugins = installed(home, project)
    have = {s["name"]: s["description"] for s in disk}
    have.update(available or {})
    by_base: dict[str, list[str]] = {}
    for n in have:
        by_base.setdefault(base(n), []).append(n)
    mapped = catalog_names(cat)

    def status_of(name: str) -> str:
        if name in have:
            return "available"
        if ":" not in name and by_base.get(name):
            return "available as " + by_base[name][0]
        return "missing"

    tests_dir = None
    if project and (project / "forge.json").is_file():
        st = json.loads((project / "forge.json").read_text(encoding="utf-8")).get("state_dir") or "01_PROJECT_CORE/state"
        tests_dir = project / st / "skill_tests"
    doms = {}
    for did, d in cat["domains"].items():
        sk = {s: status_of(s) for s in primaries(d) + d["support"]}
        prim = [sk[s] for s in primaries(d)]
        tools = {t["name"]: tool_state(t) if t.get("check") else "not checkable"
                 for t in d["tools"]}
        ev = tests_dir / f"{did}.md" if tests_dir else None  # evidence of a real run: a line "RESULT: PASS"
        if not (ev and ev.is_file() and re.search(r"^RESULT:\s*PASS\b", ev.read_text(encoding="utf-8", errors="replace"), re.M)):
            ev = None
        if prim and all(p == "missing" for p in prim):
            st = "gap"
        elif any(p == "missing" for p in prim):
            st = "partial"
        elif any(v == "NOT found" for v in tools.values()):
            st = "config"
        else:
            st = "tested" if ev else "ready"
        # K34 (0.9.2): a PASS names the loaded skill and the expectation written before the run (templates/evidence.md)
        gaps = [] if not ev else [k for k, pat in (("Skill", r"^Skill:"), ("EXPECTED", r"^EXPECTED:"))
                                  if not re.search(pat, ev.read_text(encoding="utf-8", errors="replace"), re.M)]
        doms[did] = {"status": st, "label": STATUS[st], "skills": sk, "primary": primaries(d), "tools": tools,
                     "evidence": str(ev) if ev else None, "evidence_gaps": gaps}
    dups = [{"base": b, "names": ns, "resolved": all(resolved(x, y, cat, mapped) for i, x in enumerate(ns) for y in ns[i + 1:])}
            for b, ns in sorted(by_base.items()) if len(ns) > 1]
    names = [n for n in have if have[n]]
    ws = {n: _words(have[n]) for n in names}
    over = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if base(a) == base(b) or not ws[a] or not ws[b] or (a.startswith("forge:") and b.startswith("forge:")):
                continue
            j = len(ws[a] & ws[b]) / len(ws[a] | ws[b])
            if j >= 0.28:
                over.append({"a": a, "b": b, "score": round(j, 2), "resolved": resolved(a, b, cat, mapped)})
    over.sort(key=lambda x: -x["score"])
    return {"date": date.today().isoformat(), "session_list": available is not None, "domains": doms, "duplicates": dups,
            "overlaps": over[:20], "unmapped": sorted(n for n in have if n not in mapped and not n.startswith("forge:")),
            "heavy": sorted(({"name": s["name"], "words": s["words"]} for s in disk if s["words"] > 5000), key=lambda x: -x["words"]),
            "plugins": plugins, "disk_skills": len(disk), "no_description": len([n for n in have if not have[n]]),
            "third_party_installed": [{**i, "present": "yes" if (i["skill"] in have or any(n.startswith(i["skill"].rstrip("*")) for n in have
                                                                       if i["skill"].endswith("*"))) else "NO"}
                                      for i in cat.get("third_party", {}).get("installed", [])]}


def audit_md(r: dict) -> str:
    L = [f"# Skills inventory - {r['date']}", "",
         "Generated by `skills_map.py audit` (K32). Statuses come from checks: the session's skill list"
         + (" (given)" if r["session_list"] else " (NOT given - app/Cowork skills are invisible on disk, so they count as missing)")
         + f", {r['disk_skills']} skills on disk, `which` for tools, `state/skill_tests/<domain>.md` with a line `RESULT: PASS` for 'tested'.", "",
         "| domain | status | primary skills (stages) | support menu | tools | evidence |", "|---|---|---|---|---|---|"]
    for did, d in r["domains"].items():
        mark = lambda n: f"{n} ({'ok' if d['skills'][n].startswith('available') else 'MISSING'})"  # noqa: E731
        prim = ", ".join(mark(n) for n in d["primary"]) or "Claude + tools"
        sup = ", ".join(mark(n) for n in d["skills"] if n not in d["primary"]) or "-"
        tl = ", ".join(f"{n} ({v})" for n, v in d["tools"].items()) or "-"
        evd = (d['evidence'] or '-') + (f" (incomplete: no {' / '.join(d['evidence_gaps'])} line)" if d.get("evidence_gaps") else "")
        L.append(f"| {did} | {d['label']} | {prim} | {sup} | {tl} | {evd} |")
    L += ["", "## Same name in several sources", ""]
    L += [f"- `{x['base']}`: {', '.join(x['names'])} - {'roles set in domains.json' if x['resolved'] else 'UNRESOLVED: give each a role or a conflict rule'}"
          for x in r["duplicates"]] or ["- none"]
    L += ["", "## Overlapping descriptions (word overlap >= 0.28)", "",
          (f"{r['no_description']} listed skills had no description and could not be compared - the check is partial."
           if r.get("no_description") else "Every listed skill had a description."), ""]
    L += [f"- {o['a']} ~ {o['b']} ({o['score']}): " + ({"rule": "conflict rule in domains.json", "roles": "both have a role in domains.json"}.get(o["resolved"], "UNRESOLVED"))
          for o in r["overlaps"]] or ["- none"]
    inst = r.get("third_party_installed") or []
    if inst:
        L += ["", "## Installed third-party skills (K33 provenance - how to update)", "",
              "| skill | where | source @ commit | licence | vet | present | update |", "|---|---|---|---|---|---|---|"]
        L += [f"| {i['skill']} | {i['path']} | {i['source']} @ {i['commit']} | {i['licence']} | {i['vet'].split(':')[0]} | "
              f"{i.get('present', '?')} | {i['update']} |" for i in inst]
    L += ["", "## Plugins on disk", ""]
    L += [f"- {p['plugin']} {p['version']}: {p['skills']} skills" + (", hooks" if p["hooks"] else "") + (", MCP" if p["mcp"] else "") for p in r["plugins"]] or ["- none"]
    if r["heavy"]:
        L += ["", "## Heavy skills (> 5000 words loaded when triggered)", ""] + [f"- {h['name']}: {h['words']} words" for h in r["heavy"]]
    L += ["", f"## Available but not in the map ({len(r['unmapped'])})", "",
          "Not routed by Forge: used directly when the user asks (decision B - routine work through Cowork plugins).", "",
          ", ".join(r["unmapped"]) or "-", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- vet
CODE_EXT = {".py", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".cjs", ".ts", ".rb", ".go", ".exe", ".dll", ".so"}
DEP_FILES = {"package.json", "requirements.txt", "pyproject.toml", "Gemfile", "go.mod", "Cargo.toml"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".whl", ".jar", ".7z", ".rar"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".exe", ".dll", ".so", ".mp3", ".mp4", ".wav", ".woff", ".woff2", ".ttf"}
PAT = {
    "remote_exec": re.compile(r"(curl|wget|iwr|invoke-webrequest)[^\n|]{0,200}\|\s*(sudo\s+)?(ba|z)?sh\b|\biex\b|invoke-expression|eval\s*\(\s*(await\s+)?fetch", re.I),
    "override": re.compile(r"ignore (all |any )?(previous|prior|other|the user'?s?) (instructions|rules)|override (the )?(user|claude\.md|system)"
                           r"|disregard (the )?(user|system)|--dangerously|bypass(ing)? (the )?(permissions|sandbox)", re.I),
    "soft_override": re.compile(r"do not ask (the user|for (confirmation|permission))|without asking|always (use|load|activate) this skill"
                                r"|use this skill (for|on) (every|all|any)", re.I),
    "install": re.compile(r"\bnpx\s+(-y\s+)?[@\w][\w./@-]*|\bpip3?\s+install\b[^\n]{0,60}|\bnpm\s+(i|install)\b[^\n]{0,60}|\buvx\s+[\w./@-]+"
                          r"|\bbrew\s+install\b[^\n]{0,40}|\b(go|cargo)\s+install\b[^\n]{0,40}", re.I),
    "dynamic_exec": re.compile(r"!`[^`\n]+`|^\s*```!", re.M),
    "hidden": re.compile("[​-‏‪-‮⁠-⁩]|[\U000e0000-\U000e007f]"),
    "telemetry": re.compile(r"telemetry|posthog|mixpanel|segment\.io|analytics\.(track|identify)", re.I),
    "net_call": re.compile(r"requests\.(get|post|put)|urllib\.request|http\.client|\bfetch\(|axios|socket\.socket|Invoke-RestMethod", re.I),
    "secret": re.compile(r"\b[A-Z][A-Z0-9_]*(API_KEY|TOKEN|SECRET)[A-Z0-9_]*\b"),
    "host": re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})"),
}


def licence_of(folder: Path, skill_meta: list[dict]) -> dict:
    found = None
    ups = [folder, *list(folder.parents)[:6]]
    repo = next((i for i, d in enumerate(ups) if (d / ".git").exists()), None)
    # up to the repository root when there is one (N011: LICENSE 4 levels up), never beyond it; no repo -> 2 levels only
    for d in ups[:repo + 1] if repo is not None else ups[:3]:
        for f in sorted(d.glob("*")):
            if f.is_file() and re.match(r"(licen[cs]e|copying)", f.name, re.I):
                found = f; break
        if found:
            break
    meta = next((m["license"] for m in skill_meta if m.get("license")), "")
    spdx = "none"
    if found:
        t = " ".join(found.read_text(encoding="utf-8-sig", errors="replace")[:8000].split())  # line wraps never hide a clause
        checks = [("CC0-1.0", "CC0"), ("Unlicense", "free and unencumbered software released into the public domain"),
                  ("AGPL", "GNU AFFERO"), ("LGPL", "GNU LESSER"), ("GPL", "GNU GENERAL PUBLIC"), ("MPL-2.0", "Mozilla Public License"),
                  ("Apache-2.0", "Apache License"), ("MIT", "Permission is hereby granted, free of charge"), ("MIT", "MIT License"),
                  ("BSD", "Redistribution and use in source and binary forms")]
        spdx = next((s for s, needle in checks if needle.lower() in t.lower()), "")
        if not spdx and "and/or distribute this software for any purpose" in t:
            spdx = "ISC" if "provided that the above copyright notice" in t else "0BSD"
        spdx = spdx or "unknown text"
    cat = load()["third_party"]
    if spdx in cat["copy_ok"]:
        k16 = "copy allowed (no attribution needed)"
    elif any(spdx.startswith(a.split("-")[0]) for a in cat["attribution"]):
        k16 = "attribution licence: install as an external dependency, or learn and rewrite - do not copy (K16)"
    elif spdx == "none":
        k16 = "no licence: all rights reserved - learn only (K16)"
    else:
        k16 = "unrecognised licence text - the owner reads it before any use"
    return {"file": str(found) if found else None, "spdx": spdx, "frontmatter": meta, "k16": k16}


def scanner_argv(tpl: str, folder: Path, nt: bool | None = None) -> list[str]:
    """1.1.1: FORGE_SKILL_SCANNER split into argv. On Windows shlex (posix=False) keeps the quotes of a quoted token
    ("C:\\...\\python.exe"), so a token wrapped in one matching pair of double quotes loses that pair; inner quotes stay.
    POSIX splitting already removes quotes and is left as it is."""
    import shlex
    nt = os.name == "nt" if nt is None else nt
    out = []
    for x in shlex.split(tpl, posix=not nt):
        if nt and len(x) > 1 and x[0] == x[-1] == '"':
            x = x[1:-1]
        out.append(x.replace("{path}", str(folder)))
    return out


def external_scan(folder: Path) -> dict | None:
    """1.1.0 (E5): an optional second static scanner (e.g. NVIDIA SkillSpector) named by the owner in FORGE_SKILL_SCANNER - a
    command line with `{path}` that prints SARIF on stdout. Its findings can only raise the verdict, never lower it.
    None when no scanner is configured; nothing from the scanned folder is run by Forge either way."""
    tpl = os.environ.get("FORGE_SKILL_SCANNER", "").strip()
    if not tpl:
        return None
    import subprocess
    cmd = scanner_argv(tpl, folder)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, stdin=subprocess.DEVNULL)
        sarif = json.loads(p.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return {"scanner": tpl, "ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "results": []}
    results = []
    for run in sarif.get("runs", []) if isinstance(sarif, dict) else []:
        for res in run.get("results", []) or []:
            loc = ((res.get("locations") or [{}])[0].get("physicalLocation") or {}).get("artifactLocation", {}).get("uri", "")
            results.append({"rule": res.get("ruleId", "?"), "level": res.get("level", "warning"),
                            "message": str((res.get("message") or {}).get("text", ""))[:200], "file": loc})
    return {"scanner": tpl, "ok": True, "error": "", "results": results}


def vet(folder: Path) -> dict:
    every = [f for f in folder.rglob("*") if f.is_file() and ".git" not in f.parts]
    files = every[:3000]
    not_scanned = [f"{len(every) - len(files)} files beyond the 3000-file limit"] if len(every) > len(files) else []
    mds = [f for f in files if f.name == "SKILL.md"]
    metas, problems = [], []
    for md in mds:
        fm = _frontmatter(md)
        metas.append(fm)
        fm.setdefault("name", md.parent.name)  # Claude Code takes the folder name when `name` is absent (InvestSkill, N011)
        if not fm.get("description"):
            problems.append(f"{md.relative_to(folder)}: frontmatter without a description - the skill would never trigger")
        elif len(fm["description"]) > 1024:
            problems.append(f"{md.relative_to(folder)}: description longer than 1024 characters")
    risky_fm = sorted({f"{md.relative_to(folder).as_posix()}: {k}" for md, m in zip(mds, metas) for k in m.get("_risky", [])})
    hits: dict[str, set] = {k: set() for k in PAT}
    shebang, archives = set(), set()
    for f in files:
        rel = f.relative_to(folder).as_posix()
        if f.suffix.lower() in ARCHIVE_EXT:
            archives.add(rel); continue
        if f.suffix.lower() in BINARY_EXT:
            continue
        if f.stat().st_size > 1_000_000:  # not read = not checked: never LOW-RISK on a partial scan (N010 rung 3 #5)
            not_scanned.append(f"{rel} (over 1 MB)"); continue
        t = f.read_text(encoding="utf-8", errors="replace")
        if t.startswith("﻿"):
            t = t[1:]
        if t.startswith("#!"):
            shebang.add(rel)
        for k, p in PAT.items():
            if k == "net_call" and f.suffix.lower() not in CODE_EXT:
                continue
            if k in ("install", "dynamic_exec") and f.suffix.lower() not in (".md", ".txt", ""):
                continue
            for m in p.finditer(t):
                hits[k].add(m.group(1) if k == "host" else m.group(0) if k == "secret" else rel if k in ("net_call", "telemetry", "hidden")
                            else f"{rel}: {m.group(0)[:80]}")
    execs = sorted({f.relative_to(folder).as_posix() for f in files if f.suffix.lower() in CODE_EXT} | shebang)
    deps = sorted(f.relative_to(folder).as_posix() for f in files if f.name in DEP_FILES)
    hooks = sorted(f.relative_to(folder).as_posix() for f in files if f.name == "hooks.json"
                   or (f.name in ("plugin.json", "settings.json") and '"hooks"' in f.read_text(encoding="utf-8", errors="replace")))
    mcp = sorted(f.relative_to(folder).as_posix() for f in files if f.name == ".mcp.json"
                 or (f.suffix == ".json" and '"mcpServers"' in f.read_text(encoding="utf-8", errors="replace")))
    if not mds:
        problems.append("no SKILL.md found")
    tag_chars = any(re.search("[\U000e0000-\U000e007f]", (folder / h).read_text(encoding="utf-8", errors="replace")) for h in hits["hidden"])
    if hits["remote_exec"] or hits["override"] or tag_chars:
        verdict = "REJECT"
    elif (execs or deps or hooks or mcp or archives or risky_fm or not_scanned or hits["net_call"] or hits["secret"]
          or hits["telemetry"] or hits["install"] or hits["dynamic_exec"] or hits["hidden"] or hits["soft_override"]):
        verdict = "REVIEW"
    else:
        verdict = "LOW-RISK"
    ext = external_scan(folder)
    if ext is not None and verdict == "LOW-RISK" and (not ext["ok"] or ext["results"]):
        verdict = "REVIEW"  # a second scanner's finding (or its failure) is for the Lead to read - it never lowers the verdict
    return {"folder": str(folder), "verdict": verdict, "skills": [m.get("name", "?") for m in metas],
            "external": ext,
            "skill_words": [m["_words"] for m in metas], "licence": licence_of(folder, metas), "executables": execs[:40],
            "dependencies": deps, "archives": sorted(archives), "hooks": hooks + risky_fm, "mcp": mcp,
            "remote_exec": sorted(hits["remote_exec"]), "overrides": sorted(hits["override"]),
            "soft_overrides": sorted(hits["soft_override"]), "installs": sorted(hits["install"])[:20],
            "dynamic_exec": sorted(hits["dynamic_exec"])[:20], "hidden_unicode": sorted(hits["hidden"]),
            "network_code": sorted(hits["net_call"])[:20], "hosts": sorted(hits["host"])[:30],
            "keys": sorted(hits["secret"]), "telemetry": sorted(hits["telemetry"])[:10], "not_scanned": not_scanned,
            "problems": problems}


def vet_text(r: dict) -> str:
    L = [f"vet: {r['folder']}", f"verdict: {r['verdict']}  (REJECT = remote install pipe, instruction override or hidden tag "
         "characters; REVIEW = executable parts, installs, load-time hooks/commands, network, keys, hidden Unicode or pushy "
         "wording the Lead must read; LOW-RISK = text only - static checks cannot prove safety)",
         f"skills: {', '.join(r['skills']) or '-'} (words: {', '.join(map(str, r['skill_words'])) or '-'})",
         f"licence: {r['licence']['spdx']} ({r['licence']['file'] or 'no licence file'}) - {r['licence']['k16']}"]
    for k in ("remote_exec", "overrides", "hidden_unicode", "soft_overrides", "hooks", "dynamic_exec", "mcp", "executables", "archives",
              "dependencies", "installs", "network_code", "keys", "telemetry", "hosts", "not_scanned", "problems"):
        if r[k]:
            L.append(f"{k}: " + "; ".join(r[k][:12]) + (" ..." if len(r[k]) > 12 else ""))
    ext = r.get("external")
    if ext is None:
        L.append("second scanner: none (optional - set FORGE_SKILL_SCANNER to a command with {path} that prints SARIF)")
    elif not ext["ok"]:
        L.append(f"second scanner FAILED ({ext['scanner']}): {ext['error']} - not a pass")
    else:
        L.append(f"second scanner ({ext['scanner']}): {len(ext['results'])} finding(s)" + "".join(
            f"\n  - {x['level']} {x['rule']} {x['file']}: {x['message']}" for x in ext["results"][:12]))
    L.append("next: read the SKILL.md for depth, compare with the role holder in domains.json, then the owner's yes (K33).")
    return "\n".join(L)


# ---------------------------------------------------------------- CLI
# ---------- profile (0.9.3, N023): which plugin sets a project needs ----------
PROFILE_FILE = "skill_profile.json"
DESKTOP_NOTE = ("Plugin sets delivered by the Claude desktop app or by an organisation may ignore project settings (assumption, "
                "not verified here). One-line test after applying: open a NEW session in the project and run /context - if a "
                "switched-off set's skills are still listed under Skills, the setting had no effect there (restore it).")


def domain_skills(d: dict) -> set[str]:
    """Every skill a domain card names: the primary skill of each stage (by-format stages too) and the supporting skills."""
    out = set(primaries(d))
    for s in d.get("pipeline") or []:
        if s.get("skill"):
            out.add(s["skill"])
    for x in d.get("support") or []:
        name = x if isinstance(x, str) else (x.get("skill") if isinstance(x, dict) else None)
        if name:
            out.add(name)
    return {x for x in out if isinstance(x, str) and x}


def project_domains(project: Path, cat: dict) -> tuple[list[str], str, list[str]]:
    """forge.json `domains`; else `detect` over CONTEXT.md and PLAN.md. Returns (domains, source, ignored ids) - unknown ids
    in forge.json are reported, never dropped silently (N023 r1 #2)."""
    cfg = json.loads((project / "forge.json").read_text(encoding="utf-8"))
    raw = cfg.get("domains") or []
    raw = raw.split(",") if isinstance(raw, str) else raw
    given = [x for x in dict.fromkeys(str(r).strip().lower() for r in raw) if x and x != "none"]
    doms, ignored = [x for x in given if x in cat["domains"]], [x for x in given if x not in cat["domains"]]
    if doms:
        return doms, "forge.json domains", ignored
    lay = cfg.get("layout") or {}
    tech, docs = project / lay.get("tech", "01_PROJECT_CORE"), project / lay.get("docs", "01_PROJECT_CORE/docs")
    text = " ".join(f.read_text(encoding="utf-8", errors="replace") for f in (docs / "CONTEXT.md", tech / "PLAN.md") if f.is_file())
    found = detect(text, cat) if text.strip() else []
    top = found[0]["score"] if found else 0
    return [x["domain"] for x in found if x["score"] >= max(1, top // 2)][:3], "detect over CONTEXT.md and PLAN.md", ignored


def _in_scope(e: dict, project: Path) -> bool:
    """User-scope installs apply everywhere; project/local ones only to their own project (as in installed())."""
    if e.get("scope", "user") == "user":
        return True
    try:
        return bool(e.get("projectPath") and Path(e["projectPath"]).resolve() == project.resolve())
    except OSError:
        return False


def present_plugins(available: dict[str, str] | None, home: Path, project: Path) -> dict[str, dict]:
    """Plugin sets this project sees, by skill namespace: the session's skill list (prefix before ':') and the CLI registry
    (installed_plugins.json, in scope for this project). A registry entry's namespace is its manifest name and its skills
    are the ones in its installPath - the install key may differ from the namespace (rung 3 #1); every key of a namespace
    goes into the snippet (several marketplaces)."""
    out: dict[str, dict] = {}

    def slot(ns: str) -> dict:
        return out.setdefault(ns, {"skills": set(), "keys": [], "listed": False, "other": []})

    for name in available or {}:
        if ":" in name:
            s = slot(name.split(":", 1)[0]); s["skills"].add(name); s["listed"] = True
    reg = home / ".claude" / "plugins" / "installed_plugins.json"
    try:
        plugins = json.loads(reg.read_text(encoding="utf-8")).get("plugins", {}) if reg.is_file() else {}
    except (json.JSONDecodeError, OSError, AttributeError):
        plugins = {}
    for key, entries in plugins.items():
        for e in entries if isinstance(entries, list) else [entries]:
            if not isinstance(e, dict) or not _in_scope(e, project):
                continue
            raw = str(e.get("installPath") or "")
            p = Path(raw) if raw and Path(raw).is_absolute() else None  # Path("") is the current folder: never read it as a plugin
            meta = {}
            try:
                meta = json.loads((p / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")) if p and p.is_dir() else {}
            except (OSError, ValueError):
                pass
            ns = str(meta.get("name") or key.split("@", 1)[0])
            s = slot(ns)
            if key not in s["keys"]:
                s["keys"].append(key)
            for md in sorted((p / "skills").glob("*/SKILL.md")) if p and p.is_dir() else []:
                s["skills"].add(f"{ns}:{_frontmatter(md).get('name', md.parent.name)}")
            if p and p.is_dir():  # what else it brings: the skills map cannot judge MCP servers, hooks, commands or agents
                s["other"] += [x for x, there in (("MCP", (p / ".mcp.json").is_file() or "mcpServers" in meta),
                                                  ("hooks", (p / "hooks" / "hooks.json").is_file() or "hooks" in meta),
                                                  ("commands", (p / "commands").is_dir()), ("agents", (p / "agents").is_dir()))
                               if there and x not in s["other"]]
    return out


def _local_plugins(project: Path) -> dict:
    """enabledPlugins of the project's settings.local.json (read only) - the restore step gives these values back."""
    f = project / ".claude" / "settings.local.json"
    try:
        v = json.loads(f.read_text(encoding="utf-8")).get("enabledPlugins") if f.is_file() else {}
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def profile(project: Path, cat: dict, available: dict[str, str] | None, home: Path, previous: dict | None = None) -> dict:
    doms, source, ignored = project_domains(project, cat)
    need_skills = set().union(*(domain_skills(cat["domains"][d]) for d in doms)) if doms else set()
    need = {s.split(":", 1)[0] for s in need_skills if ":" in s} | {"forge"}
    present = present_plugins(available, home, project)
    local = _local_plugins(project)
    keep, off, snippet = [], [], {}
    for ns in sorted(present):
        info = present[ns]
        total = len(info["skills"])
        named = sorted(s for s in need_skills if s.startswith(ns + ":"))
        if ns not in need and doms and info["other"] and not info["skills"]:
            keep.append({"plugin": ns, "used": [], "not_seen": [], "skills": 0,
                         "why": f"brings {', '.join(info['other'])}, no skills - not judged by the skills map"})
            continue
        if ns in need or not doms:  # no usable domain: nothing is proposed off (r1 #2) - a set the cards name always stays
            used = [s for s in named if s in info["skills"]] if info["skills"] else named
            unseen = [s for s in named if info["skills"] and s not in info["skills"]]
            why = ("Forge itself" if ns == "forge" else "no usable domain - kept" if not doms else
                   f"cards of {', '.join(doms)} use {len(used)} of its {total or '?'} skills"
                   + (f"; named in the cards but not seen installed: {', '.join(unseen[:3])}" if unseen else ""))
            keep.append({"plugin": ns, "used": used, "not_seen": unseen, "skills": total, "why": why})
        else:
            keys = info["keys"] or [f"{ns}@synced"]
            why = f"no skill of {ns} is in the cards of {', '.join(doms)}"
            if info["other"]:
                why += f"; it also brings {', '.join(info['other'])} - check those before switching it off"
            off.append({"plugin": ns, "skills": total, "keys": keys, "key_known": bool(info["keys"]), "also": info["other"], "why": why})
            snippet.update({k: False for k in keys})
    # restore = the values these keys had in settings.local.json before (or "remove" when absent); the earliest record of a
    # key wins across runs, so re-running never loses the original state (r1 #1, rung 3 #2)
    restore_set = dict((previous or {}).get("restore", {}).get("set") or {})
    restore_remove = list((previous or {}).get("restore", {}).get("remove") or [])
    for k in ({} if ignored else snippet):
        if k in restore_set or k in restore_remove:
            continue
        if k in local:
            restore_set[k] = local[k]
        else:
            restore_remove.append(k)
    withheld = bool(ignored)  # an unknown id may be a typo of a domain this project needs: nothing ready to apply until fixed
    return {"project": str(project), "date": date.today().isoformat(), "domains": doms, "source": source, "ignored_ids": ignored,
            "needed_skills": sorted(need_skills), "keep": keep, "off": off,
            "snippet": {"enabledPlugins": {} if withheld else snippet}, "withheld": withheld,
            "restore": {"how": "in <project>/.claude/settings.local.json set the `set` keys back to these values and delete the "
                               "`remove` keys, then start a new session in the project", "set": restore_set, "remove": restore_remove},
            "desktop_note": DESKTOP_NOTE, "unknown_keys": [k for x in off if not x["key_known"] for k in x["keys"]],
            "runs": int((previous or {}).get("runs", 0)) + 1}


def is_settings_path(p: Path) -> bool:
    """The command never writes Claude Code settings: settings*.json anywhere or anything inside a .claude folder - judged on
    the path as given AND on where it really points (a symlink to settings is refused too - rung 3 #3)."""
    for q in (Path(p), Path(os.path.realpath(p))):
        if ".claude" in [x.lower() for x in q.parts] or re.fullmatch(r"settings(\.local)?\.json", q.name.lower()):
            return True
    return False


def profile_text(r: dict) -> str:
    lines = [f"profile of {r['project']} - domains {', '.join(r['domains']) or 'none'} ({r['source']})"]
    if r["ignored_ids"]:
        lines.append(f"  ignored in forge.json (not in domains.json): {', '.join(r['ignored_ids'])}")
    if not r["domains"]:
        lines.append("  no usable domain: nothing is proposed off - set forge.json \"domains\" or describe the project in CONTEXT.md")
    lines += [f"  keep  {k['plugin']:<18} {k['why']}" for k in r["keep"]]
    lines += [f"  off?  {o['plugin']:<18} {o['why']} ({o['skills'] or '?'} skills)" for o in r["off"]]
    if not r["keep"] and not r["off"]:
        lines.append("  no plugin set visible: pass --available-file (the session's skill list); the CLI registry has none in scope")
    if r.get("withheld"):
        lines.append("snippet withheld: fix the unknown domain ids in forge.json first (a typo may hide a domain this project needs)")
    lines.append("snippet for <project>/.claude/settings.local.json (the owner applies it; this command writes no settings):")
    lines.append(json.dumps(r["snippet"], indent=2))
    if r["unknown_keys"]:
        lines.append(f"keys not in the CLI registry (desktop-delivered sets, form name@synced - check with the test below): "
                     f"{', '.join(r['unknown_keys'])}")
    lines.append("restore: " + r["restore"]["how"] + f" (set: {json.dumps(r['restore']['set'])}; remove: {', '.join(r['restore']['remove']) or '-'})")
    lines.append(r["desktop_note"])
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Forge skills map (K32-K33)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list")
    p = sp.add_parser("detect"); p.add_argument("text"); p.add_argument("--json", action="store_true")
    p = sp.add_parser("show"); p.add_argument("ids")
    p = sp.add_parser("audit"); p.add_argument("--available-file"); p.add_argument("--md"); p.add_argument("--json", action="store_true")
    p.add_argument("--home", default=str(Path.home())); p.add_argument("--project")
    p = sp.add_parser("vet"); p.add_argument("folder"); p.add_argument("--json", action="store_true")
    p = sp.add_parser("profile", help="0.9.3: which plugin sets a project needs; ledger <state>/skill_profile.json; prints the settings snippet")
    p.add_argument("project"); p.add_argument("--available-file", help="the session's skill list (as for audit)")
    p.add_argument("--apply-file", help="also write the snippet to this file - never a settings file or anything under .claude/")
    p.add_argument("--restore", action="store_true", help="print the restore snippet recorded in the ledger (writes nothing)")
    p.add_argument("--home", default=str(Path.home())); p.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cat = load()
    if a.cmd == "list":
        for did, d in cat["domains"].items():
            print(f"{did:10} {d['name']}")
    elif a.cmd == "detect":
        r = detect(a.text, cat)
        if a.json:
            print(json.dumps(r, ensure_ascii=False))
        elif not r:
            print("no domain matched - pick one with `skills_map.py list`, or treat it as general work")
        else:
            for x in r:
                print(f"{x['domain']:10} score {x['score']}  ({', '.join(x['hits'][:6])})")
            ids = [x["domain"] for x in r if x["score"] >= max(1, r[0]["score"] // 2)][:3]
            print(f"next: skills_map.py show {','.join(ids)}   then order.py new ... --domain {','.join(ids)}")
    elif a.cmd == "show":
        ids = ids_ok([i.strip() for i in a.ids.split(",") if i.strip()], cat)
        print("\n\n".join(card(i, cat, str(PLUGIN_DIR)) for i in ids))
        for c in combos_for(ids, cat):
            print("\ncombo sequence: " + " -> ".join(c["sequence"]) + f"\nhandoff: {c['handoff']}")
        print("\nselection rules: " + " / ".join(cat["selection"]))
    elif a.cmd == "audit":
        avail = parse_available(Path(a.available_file)) if a.available_file else None
        proj = Path(a.project) if a.project else None
        if proj is None:
            for d in [Path.cwd(), *Path.cwd().parents]:
                if (d / "forge.json").is_file():
                    proj = d; break
        r = audit(cat, Path(a.home), proj, avail)
        if a.md:
            Path(a.md).write_text(audit_md(r), encoding="utf-8", newline="\n")
            print(f"written {a.md}")
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        elif not a.md:
            print(audit_md(r))
        if not avail:
            print("note: no --available-file - app/Cowork skills are invisible on disk and count as missing", file=sys.stderr)
    elif a.cmd == "profile":
        project = Path(a.project).resolve()
        if not (project / "forge.json").is_file():
            raise SystemExit(f"not a Forge project (no forge.json): {project}")
        lay = json.loads((project / "forge.json").read_text(encoding="utf-8")).get("layout") or {}
        ledger = project / lay.get("state", "01_PROJECT_CORE/state") / PROFILE_FILE
        if is_settings_path(ledger) or ledger.is_symlink():  # the ledger path itself never lands in settings (r1 #3, rung 3 #3)
            raise SystemExit(f"refused: the ledger path {ledger} is a settings path or a link - this command never writes settings")
        previous = None
        if ledger.is_file():
            try:
                previous = json.loads(ledger.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = None
            if not (isinstance(previous, dict) and isinstance(previous.get("restore"), dict)):
                raise SystemExit(f"refused: the ledger {ledger} exists but cannot be read - it holds the restore record; fix or move "
                                 f"it by hand, then run again")
        if a.restore:
            if previous is None:
                raise SystemExit(f"no readable ledger {ledger}: nothing was profiled, nothing to restore")
            print("restore (" + previous["restore"].get("how", "") + "):")
            print(json.dumps({"enabledPlugins": previous["restore"].get("set") or {}}, indent=2))
            print("remove these keys: " + (", ".join(previous["restore"].get("remove") or []) or "-"))
            return 0
        if a.apply_file:
            target = Path(a.apply_file)
            if is_settings_path(target):
                raise SystemExit(f"refused: {a.apply_file} is a Claude Code settings path - this command never writes settings; "
                                 f"the owner applies the snippet")
            if target.exists() or os.path.realpath(target) == os.path.realpath(ledger):
                raise SystemExit(f"refused: {a.apply_file} exists (or is the ledger) - the snippet file is written only as a new file")
        r = profile(project, cat, parse_available(Path(a.available_file)) if a.available_file else None, Path(a.home), previous)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        if a.apply_file:
            Path(a.apply_file).write_text(json.dumps(r["snippet"], indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else profile_text(r))
        print(f"ledger: {ledger}" + (f" (run {r['runs']}; earlier restore values kept)" if r["runs"] > 1 else "")
              + (f"; snippet file: {a.apply_file}" if a.apply_file else ""))
        if not a.available_file:
            print("note: no --available-file - only plugins in the CLI registry are seen (app/Cowork sets are invisible on disk)", file=sys.stderr)
    elif a.cmd == "vet":
        folder = Path(a.folder)
        if not folder.is_dir():
            raise SystemExit(f"not a folder: {folder}")
        r = vet(folder)
        print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else vet_text(r))
        return 1 if r["verdict"] == "REJECT" else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
