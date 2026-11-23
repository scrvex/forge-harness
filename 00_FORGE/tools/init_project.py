"""Create a new Forge project from a Lead-written plan — the mechanical half of the `forge-new` skill (K19–K25).

    init_project.py C:/ai/mytask --name mytask --plan plan.json --mode cheap|expensive --request "..." [--lang ru]
                    [--sources f1,dir --sources-dir Исходники] [--git]

The plan is the play-back JSON the Lead writes after the interview (shape: templates/plan.example.json; K21: the
architecture stays with the Lead, never with the local model). Layout (owner's law 2026-09-17, plugin form 0.6.0):
    <project>/
    ├── CLAUDE.md · README.md · forge.json      entry points (forge.json records the plugin version and the mode)
    ├── 01_PROJECT_CORE/                        CORE 2 — the project core that learns: circle/ state/ tools/ config/ docs/
    │                                           PLAN.md, PROJECT_RULES.md
    └── 02_Для_пользователя/ (02_For_user/)     RESULTS — numbered context folders, final documents, the guide (md + pdf)
Core 1 = the installed Forge plugin; nothing of it is copied into the project.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import CORE2, CORE_DIRS, PLUGIN_DIR, gitignore_lines, kernel_version, layout  # noqa: E402

PY = sys.executable
TPL = PLUGIN_DIR / "templates"
NAMES = {"ru": ("02_Для_пользователя", "Инструкция_по_использованию", "Исходники"), "en": ("02_For_user", "User_guide", "Sources"),
         "de": ("02_Fuer_den_Nutzer", "Anleitung", "Quellen"), "es": ("02_Para_el_usuario", "Guia", "Fuentes"), "fr": ("02_Pour_l_utilisateur", "Guide", "Sources")}
TEXT_EXT = {".md", ".txt", ".csv", ".json", ".html", ".yaml", ".yml"}


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def detect_lang(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    return "ru" if letters and sum("Ѐ" <= c <= "ӿ" for c in letters) / len(letters) > 0.3 else "en"


def safe_folder(name: str) -> str:
    """A folder name in the user's language: letters, digits, spaces->_, no path tricks."""
    n = re.sub(r"[^\w\- ]", "", str(name), flags=re.U).strip().replace(" ", "_")
    return n[:40] or "Folder"


def validate_plan(d: dict) -> dict:
    """Shape check for the Lead's plan JSON (L001: never trust shape)."""
    out = {"task_restated": str(d.get("task_restated", "")), "domain": str(d.get("domain", "other")).lower()[:20],
           "folders": [], "automations": [], "first_orders": [], "red_lines": [], "benefit": str(d.get("benefit", "")),
           "required_from_user": []}
    for f in (d.get("folders") or [])[:8]:
        if isinstance(f, dict) and f.get("path"):
            out["folders"].append({"path": safe_folder(f["path"]), "purpose": str(f.get("purpose", ""))})
    for a in (d.get("automations") or [])[:8]:
        if isinstance(a, dict) and a.get("phrase"):
            out["automations"].append({"phrase": str(a["phrase"]), "what": str(a.get("what", "")),
                                       "tier": a.get("tier") if a.get("tier") in ("LOCAL", "FAST", "HEAVY", "LEAD") else "HEAVY"})
    for o in (d.get("first_orders") or [])[:5]:
        if isinstance(o, dict) and o.get("title"):
            dod = o.get("dod") if isinstance(o.get("dod"), list) else [str(o.get("dod", ""))]
            out["first_orders"].append({"title": str(o["title"])[:80], "goal": str(o.get("goal", "")), "dod": [str(x) for x in dod][:6],
                                        "context": [str(x) for x in (o.get("context") or [])][:8],
                                        "deliverable": str(o.get("deliverable") or "") or None,
                                        "folder": safe_folder(o["folder"]) if o.get("folder") else None,
                                        "tier": o.get("tier") if o.get("tier") in ("LOCAL", "FAST", "HEAVY", "LEAD") else "HEAVY",
                                        "quality": [str(x) for x in (o.get("quality") or [])][:4]})
    if re.search(r"\d+\s*(%|раз|x)", out["benefit"]):  # R5: invented numbers are not promises
        out["benefit"] += " (Числовые ожидания не измерены — эффект будет проверен по ходу работы.)"
    out["red_lines"] = [str(x) for x in (d.get("red_lines") or []) if str(x).strip()][:8]
    out["required_from_user"] = [str(x) for x in (d.get("required_from_user") or []) if str(x).strip()][:8]
    # K32 (0.9): skills-map domains of the project -> forge.json "domains" -> every order gets their card by default
    known = json.loads((PLUGIN_DIR / "domains.json").read_text(encoding="utf-8"))["domains"]
    raw = d.get("domains") if isinstance(d.get("domains"), list) else [out["domain"]]
    out["domains"] = [x for x in dict.fromkeys(str(r).strip().lower() for r in raw) if x in known][:3]
    return out


def numbered(folders: list[dict]) -> list[dict]:
    """User folders get NN_ prefixes in order (01_Стратегия, 02_Портфель ...)."""
    out = []
    for i, f in enumerate(folders, 1):
        path = f["path"]
        if not re.match(r"^\d\d_", path):
            path = f"{i:02d}_{path}"
        out.append({**f, "path": path})
    return out


def normalise_sources(dst: Path, src_root: Path, max_kb: int = 60) -> None:
    """R14: copies of the owner's materials are read-only inputs — LF/UTF-8 normalised, big text files split into
    <name>.parts/NN.md chunks; the folder is excluded from gates (K15: raw inputs)."""
    for p in list(src_root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT or any(part.endswith(".parts") for part in p.parts):
            continue
        text = p.read_bytes().decode("utf-8", errors="replace").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        p.write_text(text, encoding="utf-8", newline="\n")
        if len(text.encode("utf-8")) > max_kb * 1024:
            parts_dir = p.with_suffix(p.suffix + ".parts"); parts_dir.mkdir(exist_ok=True)
            chunk, n, size = [], 1, 0
            for line in text.splitlines(keepends=True):
                chunk.append(line); size += len(line.encode("utf-8"))
                if size > max_kb * 1024 * 0.9:
                    w(parts_dir / f"{n:02d}.md", "".join(chunk)); chunk, n, size = [], n + 1, 0
            if chunk:
                w(parts_dir / f"{n:02d}.md", "".join(chunk))
    cfg_path = dst / "forge.json"; cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    rel = src_root.relative_to(dst).as_posix()
    if rel not in cfg.get("exclude", []):
        cfg.setdefault("exclude", []).append(rel)
    index = ["# Sources index (read-only copies; normalised to UTF-8/LF; files > 60 KB also split into .parts/)", ""]
    for p in sorted(src_root.rglob("*")):
        if p.is_file() and not any(part.endswith(".parts") for part in p.parts) and p.name != "_INDEX.md":
            index.append(f"- `{p.relative_to(dst).as_posix()}` — {p.stat().st_size // 1024} KB")
    w(src_root / "_INDEX.md", "\n".join(index) + "\n")
    w(cfg_path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir"); ap.add_argument("--name", required=True); ap.add_argument("--plan", required=True, help="Lead-written play-back JSON")
    ap.add_argument("--request", default="", help="the person's request (sets the language when --lang is omitted)")
    ap.add_argument("--lang", help="user language for deliverables, folders and the guide (ru|en|de|es|fr)")
    ap.add_argument("--mode", required=True, choices=["cheap", "expensive"], help="K25: the owner's answer")
    ap.add_argument("--sources", help="comma-separated files/dirs to copy into the user part (read-only copies)")
    ap.add_argument("--sources-dir", help="user sub-folder for the copies (default: the language's Sources name)")
    ap.add_argument("--git", action="store_true")
    ap.add_argument("--gpt", choices=["allowed", "denied"], default="denied",
                    help="K15/K29: may Codex/ChatGPT execute code tasks, review, advise and make images here? Ask the owner (default denied)")
    ap.add_argument("--code", nargs="?", const="", default=None,
                    help="K35 (1.0): a code project; optional value = the test command (e.g. 'python -m pytest -q')")
    a = ap.parse_args()
    from forge_common import refuse_in_codex
    refuse_in_codex("init_project.py")  # K29: the Lead's job, never ChatGPT's
    dst = Path(a.dir).resolve()
    if (dst / "forge.json").exists():
        raise SystemExit(f"{dst} already is a Forge project")
    plan = validate_plan(json.loads(Path(a.plan).read_text(encoding="utf-8")))
    lang = a.lang or detect_lang(a.request or plan["task_restated"])
    user, guide_name, sources_name = NAMES.get(lang, NAMES["en"])
    today = datetime.date.today().isoformat(); kver = kernel_version()
    lay = {"tech": CORE2, "circle": f"{CORE2}/circle", "state": f"{CORE2}/state", "tools": f"{CORE2}/tools", "docs": f"{CORE2}/docs",
           "config": f"{CORE2}/config", "read": user, "sources": f"{user}/{a.sources_dir or sources_name}", "projects": f"{CORE2}/projects"}
    dst.mkdir(parents=True, exist_ok=True)
    for d in CORE_DIRS:  # 0.9.2: the swarm folders too (R-28: three hand migrations added them later)
        (dst / CORE2 / d).mkdir(parents=True, exist_ok=True)
    (dst / user).mkdir(exist_ok=True)
    w(dst / lay["circle"] / "STATE.md", f"STATE: READY\nUPDATED: -\nCURRENT_ORDER: -\nNOTE: created by the Forge plugin v{kver}\n")
    w(dst / lay["circle"] / "orders" / "00_INDEX.md", "# Orders index\n\n| N | file | tier | changes | state |\n|---|---|---|---|---|\n")
    w(dst / "forge.json", json.dumps({"project": a.name, "user_language": lang, "kernel_version": kver, "mode": a.mode, "created": today,
                                      "gpt": a.gpt, "layout": lay, "exclude": [".git", "__pycache__", f"{CORE2}/state/cache", "publish", "build", "dist", "bin", "node_modules"],  # R-18: owner drop-ins
                                      "tool_dirs": [f"{CORE2}/tools"], "state_dir": f"{CORE2}/state",
                                      **({"domains": plan["domains"]} if plan.get("domains") else {}),
                                      **({"code": {"test": a.code, "codex_effort": "medium", "codex_context7": False}}
                                         if a.code is not None else {})}, ensure_ascii=False, indent=2) + "\n")
    w(dst / lay["tools"] / "TOOLS.md", "# Project tools registry (Core 2 scripts; Core 1 scripts live in the Forge plugin)\n\n| script | purpose | usage |\n|---|---|---|\n")
    w(dst / ".gitignore", "".join(x + "\n" for x in gitignore_lines() + ([".serena/cache/"] if a.code is not None else [])))
    w(dst / lay["config"] / "swarm_modes.json", (TPL / "swarm_modes.json").read_text(encoding="utf-8").replace("{{DATE}}", today))
    rep = {"{{PROJECT}}": a.name, "{{TECH}}": CORE2, "{{READ}}": user, "{{LANG}}": lang, "{{MODE}}": a.mode, "{{KERNEL_VERSION}}": kver,
           "{{DATE}}": today, "{{PLUGIN}}": PLUGIN_DIR.as_posix()}

    def fill(text: str) -> str:
        for k, v in rep.items():
            text = text.replace(k, v)
        return text
    w(dst / "CLAUDE.md", fill((TPL / "CLAUDE.project.md").read_text(encoding="utf-8")))
    from gpt_setup import write_agents_md  # K29 (0.8): the role file Codex reads - ChatGPT audits/advises/draws, never leads
    write_agents_md(dst, json.loads((dst / "forge.json").read_text(encoding="utf-8")))
    # plan → PLAN.md, PROJECT_RULES.md, user folders, guide, first orders
    plan["folders"] = [{**f, "path": f"{user}/{f['path']}"} for f in numbered(plan["folders"])]
    by_name = {f["path"].split("/")[-1][3:]: f["path"] for f in plan["folders"]}
    folders = "\n".join(f"- `{f['path']}/` — {f['purpose']}" for f in plan["folders"]) or "- (none proposed)"
    autos = "\n".join(f"| «{x['phrase']}» | {x['what']} | {x['tier']} |" for x in plan["automations"]) or "| — | — | — |"
    orders = "\n".join(f"{i + 1}. **{o['title']}** — {o['goal']} DoD: {'; '.join(o['dod'])}" for i, o in enumerate(plan["first_orders"]))
    w(dst / lay["tech"] / "PLAN.md", f"# PLAN — {a.name} (domain: {plan['domain']}; written by the Lead, approved by the owner)\n\n"
      f"## Task as understood\n{plan['task_restated']}\n\n## Structure (user folders, user language)\n{folders}\n\n"
      f"## Automations (phrase → what happens)\n| phrase | what | tier |\n|---|---|---|\n{autos}\n\n"
      f"## First orders\n{orders}\n\n## Red lines\n" + "\n".join(f"- {x}" for x in plan["red_lines"]) + "\n")
    w(dst / lay["tech"] / "PROJECT_RULES.md", "# Project rules (beyond the Forge constitution)\n\n## Red lines (from the interview)\n" +
      "\n".join(f"- {x}" for x in plan["red_lines"]) + f"\n\n## Conventions\n- deliverables go to the matching user folder; "
      f"reports and everything the user reads go to `{user}/` as .md + .pdf; sources stay untouched\n")
    for f in plan["folders"]:
        d = dst / f["path"]; d.mkdir(parents=True, exist_ok=True)
        if not (d / "README.md").exists():
            w(d / "README.md", f"# {f['path']}\n\n{f['purpose']}\n")
    if a.request:
        w(dst / lay["docs"] / "brief.md", f"# Brief — {a.name}\n\n## Request\n{a.request}\n\n## Task as understood\n{plan['task_restated']}\n")
    g = fill((TPL / "GUIDE.ru.md").read_text(encoding="utf-8"))
    g = (g.replace("{{BENEFIT}}", plan["benefit"] or "(заполнить)")
          .replace("{{REQUIRED_FROM_USER}}", "\n".join(f"- {x}" for x in plan["required_from_user"]) or "- (заполнить)")
          .replace("{{FOLDERS}}", folders).replace("{{PROMPTS}}", "| фраза | что произойдёт | эшелон |\n|---|---|---|\n" + autos))
    guide = dst / user / f"{guide_name}.md"
    w(guide, re.sub(r"<!--.*?-->\n?", "", g, flags=re.S))
    subprocess.run([PY, str(PLUGIN_DIR / "tools" / "md2pdf.py"), str(guide)], cwd=dst, capture_output=True)
    w(dst / "README.md", f"# {a.name}\n\n| папка | что это | для кого |\n|---|---|---|\n"
                          f"| `{user}/` | результаты, таблицы, инструкция (pdf) — по разделам вашей задачи | **вы** |\n"
                          f"| `{CORE2}/` | ядро 2 — правила, план, задачи, скрипты и уроки этого проекта (учится) | ведущий |\n"
                          f"| `CLAUDE.md`, `forge.json` | инструктаж ведущего и настройки (режим: {a.mode}; плагин Forge v{kver}) | ведущий |\n\n"
                          f"Ядро 1 = плагин Forge v{kver}, установлен в Claude Code / Cowork (в папке не лежит). Создано {today}. "
                          f"Начните с `{user}/{guide_name}.pdf`.\n")
    w(dst / lay["docs"] / "CONTEXT.md", f"# CONTEXT — {a.name}\n\nUpdated: {today}. Bootstrapped by the Forge plugin v{kver}; mode {a.mode}; "
      f"PLAN.md written, {len(plan['first_orders'])} orders OPEN; nothing executed yet.\n")
    w(dst / lay["state"] / "session_log.md", "# Session log\n\n| date | done | tokens / limits | blockers |\n|---|---|---|---|\n")
    w(dst / lay["state"] / "project.json", json.dumps({
        "project": f"{a.name} — {plan['task_restated'][:80]}", "version": "0.1", "updated": today, "phase_current": 0,
        "phases": [{"id": 0, "name": "Сборка проекта", "status": "in_progress", "done_when": "Первые наряды закрыты, инструкция заполнена"},
                   {"id": 1, "name": "Работа по задачам", "status": "todo", "done_when": "Владелец пользуется фразами из инструкции"}],
        "tasks": [], "decisions": [{"date": today, "what": x, "who": "владелец (интервью)"} for x in plan["red_lines"]],
        "resources": {"kernel": f"Forge plugin v{kver}", "mode": a.mode}}, ensure_ascii=False, indent=2) + "\n")
    src_root = dst / lay["sources"]
    for src in [Path(x.strip()) for x in (a.sources or "").split(",") if x.strip()]:
        tgt = src_root if src.is_file() else src_root / src.name
        tgt.mkdir(parents=True, exist_ok=True)
        (shutil.copy2(src, tgt / src.name) if src.is_file() else shutil.copytree(src, tgt, dirs_exist_ok=True))
    if a.sources:
        normalise_sources(dst, src_root)
    if a.git:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dst)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=dst)
        for key in ("user.name", "user.email"):
            v = subprocess.run(["git", "config", key], cwd=PLUGIN_DIR, capture_output=True, text=True).stdout.strip()
            if v:
                subprocess.run(["git", "config", key, v], cwd=dst)
    ctx_files = [f"{CORE2}/PLAN.md", f"{CORE2}/PROJECT_RULES.md"]
    for i, o in enumerate(plan["first_orders"], 1):
        deliverable = o.get("deliverable")
        if not deliverable:
            folder = by_name.get(o.get("folder") or "", plan["folders"][0]["path"] if plan["folders"] else user)
            ext = ".html" if "html" in o["title"].lower() else (".py" if "script" in o["title"].lower() or "python" in o["title"].lower() else ".md")
            deliverable = f"{folder}/N{i:03d}_{re.sub(r'[^a-z0-9]+', '_', o['title'].lower()).strip('_')[:40]}{ext}"
        subprocess.run([PY, str(PLUGIN_DIR / "tools" / "order.py"), "new", o["title"], "--tier", o["tier"], "--changes", o["title"], "--goal", o["goal"],
                        "--scope", ",".join(ctx_files + o.get("context", [])), "--dod", ";".join(o["dod"]), "--deliverable", deliverable]
                       + (["--quality", ";".join(o["quality"])] if o.get("quality") else []), cwd=dst, capture_output=True)
    g = subprocess.run([PY, str(PLUGIN_DIR / "gates" / "run_gates.py")], cwd=dst, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(g.stdout.strip())
    print(f"project {a.name} at {dst}: {CORE2}/ (Core 2) · {user}/ ({len(plan['folders'])} folders, guide .md + .pdf) · "
          f"{len(plan['first_orders'])} orders OPEN · mode {a.mode} · plugin v{kver}")
    return g.returncode


if __name__ == "__main__":
    sys.exit(main())
