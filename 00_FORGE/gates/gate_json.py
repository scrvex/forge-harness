"""Gate: every JSON file parses; answers and lessons carry the required keys (K7, K10, lesson L001:
"JSON mode keeps syntax, not schema — validate shape")."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from forge_common import iter_files  # noqa: E402


def check(cfg: dict) -> list[str]:
    root: Path = cfg["_root"]
    problems = []
    for p, rel in iter_files(cfg):
        if p.suffix.lower() != ".json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            problems.append(f"{rel}: invalid JSON ({e})")
            continue
        parent = rel.parts[-2] if len(rel.parts) > 1 else ""
        in_circle = p.resolve().is_relative_to(cfg["_layout"]["circle"].resolve())
        if parent == "answers" and in_circle and re.fullmatch(r"N\d{3}\.json", p.name):  # review/output side files are not answers
            missing = [k for k in cfg["answer_keys"] if k not in data]
            if missing:
                problems.append(f"{rel}: answer missing keys {missing}")
            elif data.get("tier") not in cfg["tiers"]:
                problems.append(f"{rel}: tier {data.get('tier')!r} invalid")
        if parent == "lessons":
            missing = [k for k in cfg["lesson_keys"] if k not in data]
            if missing:
                problems.append(f"{rel}: lesson missing keys {missing}")
            if not (data.get("what_failed") or data.get("what_worked")):
                problems.append(f"{rel}: lesson needs what_failed or what_worked")
            if data.get("applies_to") not in ("kernel", "pack", "project"):
                problems.append(f"{rel}: applies_to must be kernel|pack|project")
    return problems
