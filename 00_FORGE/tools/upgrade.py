"""Upgrade an existing Forge project to the installed plugin version (R-28, Forge 0.9.2) - idempotent, nothing is deleted.

    upgrade.py <project_root> --dry-run          # print what would change, write nothing
    upgrade.py <project_root> [--domains a,b]    # apply; then run the gates and commit in the project

What it does (each step checks before it writes):
  1. forge.json: kernel_version -> the plugin's version, an `upgrades` record {from, to, date}, default excludes merged,
     `domains` set when --domains is given.
  2. Core 2 folders that projects of this version have (circle/plans, state/staging, state/swarm, ...), the swarm mode
     table config/swarm_modes.json, the .gitignore lines.
  3. CLAUDE.md made from the project template: the version in the title line; every section of the current template that
     the project's CLAUDE.md lacks is inserted (rendered for this project) before "## Session routine". Sections that exist
     are kept as they are (hand edits survive), except plugin-owned "Two model families" and "Skills": changed sections
     are replaced with the rendered template after backing up the original to <state>/upgrade_backup/. Hand-made files
     below MIN_SHARED keep the existing title-only update; a non-template CLAUDE.md (the Forge workspace) is not touched.
  4. AGENTS.md (the plugin-owned role file for Codex) is re-rendered when the project has one.
Never touched: the user part, CONTEXT.md, PROJECT_RULES.md, orders, lessons, the owner's sources.
Canary order (K34f): upgrade ONE project first, run its gates and `step.py`, then the others. Three hand migrations
(mesh-launch upgrade_to_071.py / upgrade_to_090.py, ank-launch) are why this exists.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import CORE_DIRS, DEFAULT_LAYOUT, PLUGIN_DIR, gitignore_lines, kernel_version  # noqa: E402
from forge_common import layout as project_layout  # noqa: E402

TPL = PLUGIN_DIR / "templates"
TEMPLATE_TITLE = re.compile(r"^# CLAUDE\.md — project `")
VERSION_IN_TITLE = re.compile(r"\(Forge plugin v[0-9][0-9.]*;")


def key(heading: str) -> str:
    """'## Swarm and the machine (K26–K28, plugin 0.7.1 — upgraded …)' -> 'swarm and the machine'; '## 10. Session routine'
    -> 'session routine' (a numbered hand-made CLAUDE.md, invest - N013 verifier2 #2)."""
    h = re.sub(r"^\d+[.)]\s*", "", heading.lstrip("#").strip())
    return re.split(r"\s+[(—]", h, maxsplit=1)[0].strip().lower()


PLUGIN_OWNED = ("two model families", "skills")
MIN_SHARED = 0.5  # below this share of the template's sections the CLAUDE.md is hand-made: it is not touched


def sections(md: str) -> list[tuple[str, str]]:
    """[(heading line, whole section text incl. heading)] for every '## ' section."""
    parts = re.split(r"(?m)^(?=## )", md)
    return [(p.splitlines()[0], p) for p in parts if p.startswith("## ")]


def render_template(cfg: dict, root: Path, kver: str, today: str) -> str:
    lay = {**DEFAULT_LAYOUT, **(cfg.get("layout") or {})}
    rep = {"{{PROJECT}}": str(cfg.get("project", root.name)), "{{TECH}}": lay["tech"], "{{READ}}": lay["read"],
           "{{LANG}}": str(cfg.get("user_language", "ru")), "{{MODE}}": str(cfg.get("mode") or "NOT SET"),
           "{{KERNEL_VERSION}}": kver, "{{DATE}}": today, "{{PLUGIN}}": PLUGIN_DIR.as_posix()}
    text = (TPL / "CLAUDE.project.md").read_text(encoding="utf-8")
    for k, v in rep.items():
        text = text.replace(k, v)
    return text


def plan_claude(root: Path, cfg: dict, kver: str, today: str) -> tuple[str | None, list[str], list[str], list[str]]:
    """-> (new CLAUDE.md text or None, changes, notes, replaced headings)."""
    f = root / "CLAUDE.md"
    if not f.is_file():
        return None, [], ["CLAUDE.md missing - not created here (init_project.py makes new projects)"], []
    old = f.read_text(encoding="utf-8")
    if not TEMPLATE_TITLE.match(old):
        return None, [], ["CLAUDE.md is not made from the project template (e.g. the Forge workspace) - not touched"], []
    new, changes = old, []
    if VERSION_IN_TITLE.search(new) and f"(Forge plugin v{kver};" not in new:
        new = VERSION_IN_TITLE.sub(f"(Forge plugin v{kver};", new, count=1)
        changes.append(f"CLAUDE.md title -> plugin v{kver}")
    own = sections(new)
    have = {key(h) for h, _ in own}
    tpl = sections(render_template(cfg, root, kver, today))
    shared = sum(key(h) in have for h, _ in tpl) / max(len(tpl), 1)
    if shared < MIN_SHARED:  # a hand-made file under the template title: inserting would duplicate its own sections
        return (new if new != old else None), changes, [
            f"CLAUDE.md shares {shared:.0%} of the template's sections (< {MIN_SHARED:.0%}): a hand-made file - sections not "
            f"inserted; add what is new by hand (template: {(TPL / 'CLAUDE.project.md').as_posix()})"], []
    owned = {key(h): (h, body) for h, body in tpl if key(h) in PLUGIN_OWNED}
    replaced = []
    parts = re.split(r"(?m)^(?=## )", new)
    for i, body in enumerate(parts):
        if not body.startswith("## "):
            continue
        k = key(body.splitlines()[0])
        if k in owned and body != owned[k][1]:
            h, parts[i] = owned[k]
            replaced.append(h[3:])
    new = "".join(parts)
    if replaced:
        changes.append("CLAUDE.md plugin-owned sections updated: " + ", ".join(replaced))
    missing = [(h, body) for h, body in tpl if key(h) not in have]
    if missing:
        block = "".join(body.rstrip("\n") + "\n\n" for _, body in missing)
        anchor = next((h for k in ("session routine", "project rules") for h, _ in own if key(h) == k), None)
        if anchor:  # at the start of a line only: "### Session routine" must not match (N013 r2 #4)
            new = re.sub(r"(?m)^" + re.escape(anchor), lambda m: block + m.group(0), new, count=1)
        else:
            new = new.rstrip("\n") + "\n\n" + block
        changes.append("CLAUDE.md + sections: " + ", ".join(h[3:].split(" (")[0] for h, _ in missing))
    kept = [h[3:].split(" (")[0] for h, _ in tpl if key(h) in have and key(h) not in PLUGIN_OWNED]
    notes = [f"CLAUDE.md sections kept as they are (compare with the template by hand if needed): {', '.join(kept)}"] if kept else []
    return (new if new != old else None), changes, notes, replaced


def backup_claude(root: Path, state: Path, from_version: str) -> Path:
    """Keep the original bytes; an exclusive create prevents overwriting an earlier backup."""
    original = (root / "CLAUDE.md").read_bytes()
    folder = state / "upgrade_backup"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"CLAUDE.md.{from_version}-{datetime.datetime.now():%Y%m%d-%H%M}"
    suffix = 0
    while True:
        backup = folder / (stem + (f"-{suffix}" if suffix else "") + ".md")
        try:
            with backup.open("xb") as f:
                f.write(original)
            return backup
        except FileExistsError:
            suffix += 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--domains", help="forge.json default domains of the skills map (comma-separated, at most 3)")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    fj = root / "forge.json"
    if not fj.is_file():
        raise SystemExit(f"{root}: no forge.json - not a Forge project")
    cfg = json.loads(fj.read_text(encoding="utf-8"))
    kver, today = kernel_version(), datetime.date.today().isoformat()
    writes: list[tuple[Path, str]] = []
    changes: list[str] = []
    notes: list[str] = []

    # 1. forge.json
    new_cfg = json.loads(json.dumps(cfg))
    if new_cfg.get("kernel_version") != kver:
        new_cfg.setdefault("upgrades", []).append({"from": cfg.get("kernel_version"), "to": kver, "date": today, "by": "upgrade.py"})
        new_cfg["kernel_version"] = kver
        changes.append(f"forge.json kernel_version {cfg.get('kernel_version')} -> {kver}")
    lay = project_layout(root)  # the same resolver every Forge tool uses ("" = the project root) - N013 r3 #4, r4 #5

    def rel(p: Path) -> str:
        return "" if p == root else p.relative_to(root).as_posix()
    state_rel = rel(lay["state"])  # the project's own state folder, also for excludes and .gitignore
    excl = list(new_cfg.get("exclude") or [])
    add_ex = [x for x in (".git", "__pycache__", f"{state_rel}/cache".lstrip("/")) if x not in excl]
    if add_ex:
        new_cfg["exclude"] = excl + add_ex; changes.append("forge.json exclude + " + ", ".join(add_ex))
    if a.domains is not None:
        doms = [d.strip() for d in a.domains.split(",") if d.strip()][:3]
        if new_cfg.get("domains") != doms:
            new_cfg["domains"] = doms; changes.append(f"forge.json domains -> {doms}")
    if new_cfg != cfg:
        writes.append((fj, json.dumps(new_cfg, ensure_ascii=False, indent=2) + "\n"))

    # 2. folders, swarm modes, .gitignore
    def where(d: str) -> str:  # "circle/plans" -> the project's own layout["circle"] + "/plans" (rung 3 #6: custom layouts)
        head, _, rest = d.partition("/")
        return rel(lay[head] / rest if rest else lay[head])
    mk = [where(d) for d in CORE_DIRS if where(d) and not (root / where(d)).is_dir()]
    if mk:
        changes.append("folders + " + ", ".join(mk))
    cfg_dir = lay["config"]
    modes = cfg_dir / "swarm_modes.json"
    if not modes.is_file():
        writes.append((modes, (TPL / "swarm_modes.json").read_text(encoding="utf-8").replace("{{DATE}}", today)))
        changes.append(f"{modes.relative_to(root).as_posix()} (swarm mode table)")
    gi = root / ".gitignore"
    have = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    add_gi = [x for x in gitignore_lines(state_rel) if x not in have]
    if add_gi:
        writes.append((gi, "\n".join(have + add_gi).strip("\n") + "\n")); changes.append(".gitignore + " + ", ".join(add_gi))

    # 3. CLAUDE.md
    text, ch, nt, replaced = plan_claude(root, new_cfg, kver, today)
    changes += ch; notes += nt
    if text is not None:
        writes.append((root / "CLAUDE.md", text))

    # 4. AGENTS.md (plugin-owned)
    if (root / "AGENTS.md").is_file():
        from gpt_setup import render_agents_md
        if render_agents_md(root, new_cfg) != (root / "AGENTS.md").read_text(encoding="utf-8", errors="replace"):
            changes.append("AGENTS.md re-rendered for this plugin version")
            writes.append((root / "AGENTS.md", None))  # written by gpt_setup.write_agents_md (keeps a backup of a foreign file)

    print(f"{root.name}: plugin {cfg.get('kernel_version')} -> {kver}" + (" (dry run, nothing written)" if a.dry_run else ""))
    for c in changes or ["nothing to do - already at " + kver]:
        print("  - " + c)
    for n in notes:
        print("  note: " + n)
    if a.dry_run:
        for h in replaced:
            print(f"would replace section: {h}")
        return 0
    if not changes:
        return 0
    for d in mk:
        (root / d).mkdir(parents=True, exist_ok=True)
    for path, body in writes:
        if body is None:
            from gpt_setup import write_agents_md
            write_agents_md(root, new_cfg); continue
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if path == root / "CLAUDE.md" and replaced:
            backup = backup_claude(root, lay["state"], str(cfg.get("kernel_version") or "unknown"))
        path.write_text(body, encoding="utf-8", newline="\n")
        if backup is not None:
            for h in replaced:
                print(f"replaced section: {h} (backup: {backup})")
    print(f"done. Next in {root}: python {PLUGIN_DIR.as_posix()}/gates/run_gates.py ; python {PLUGIN_DIR.as_posix()}/tools/step.py ; "
          f"commit (one project first - canary, K34f)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
