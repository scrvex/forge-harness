"""Forge status line - 1.1.0 (F1): Claude's own rate-limit numbers for K37, without the band mod.

Claude Code hands a status-line command its session JSON on stdin; `rate_limits.five_hour` / `seven_day` carry
`used_percentage` and `resets_at` (epoch seconds) for Pro and Max plans. This script writes them to
`~/.forge/claude_limits.json` (the file limits.py reads; source "statusline") and prints one line: the Forge project and
order, then both plans' limits. It reads only cached files (never Codex's logs) and never fails the line.

settings.json: {"statusLine": {"type": "command", "command": "python \"<plugin>/tools/statusline.py\""}}
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def forge_home() -> Path:
    return Path(os.environ.get("FORGE_HOME") or Path.home() / ".forge")


def windows(data: dict) -> list[dict]:
    """The status line's rate_limits as the band writes them: [{kind, percentUsed, resetsAt ISO}]."""
    out = []
    rl = data.get("rate_limits") or {}
    for kind in ("five_hour", "seven_day"):
        w = rl.get(kind) if isinstance(rl, dict) else None
        if not isinstance(w, dict) or not isinstance(w.get("used_percentage"), (int, float)):
            continue
        entry = {"kind": kind, "percentUsed": float(w["used_percentage"])}
        if isinstance(w.get("resets_at"), (int, float)):
            entry["resetsAt"] = datetime.fromtimestamp(w["resets_at"], tz=timezone.utc).isoformat().replace("+00:00", "Z")
        out.append(entry)
    return out


def record(wins: list[dict]) -> None:
    if not wins:
        return
    f = forge_home() / "claude_limits.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"rateLimits": wins, "source": "statusline",
                               "updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}) + "\n", encoding="utf-8")
    os.replace(tmp, f)


def project_part(cwd: Path) -> str:
    root = next((d for d in (cwd, *cwd.parents) if (d / "forge.json").is_file()), None)
    if root is None:
        return ""
    try:
        cfg = json.loads((root / "forge.json").read_text(encoding="utf-8"))
        circle = root / (cfg.get("layout") or {}).get("circle", "01_PROJECT_CORE/circle")
        st = dict(re.findall(r"^([A-Z_]+):\s*(.*)$", (circle / "STATE.md").read_text(encoding="utf-8"), re.M))
    except (OSError, ValueError):
        return f"Forge {root.name}"
    cur = st.get("CURRENT_ORDER", "-")
    return f"Forge {cfg.get('project', root.name)} · " + (f"{cur} · {st.get('STATE', '?')}" if re.fullmatch(r"N\d{3}", cur) else st.get("STATE", "?"))


def limits_part(wins: list[dict]) -> str:
    claude = " · ".join(f"{'5h' if w['kind'] == 'five_hour' else '7d'} {round(w['percentUsed'])}%" for w in wins)
    codex = ""
    try:
        cx = json.loads((forge_home() / "codex_limits.json").read_text(encoding="utf-8"))
        codex = " · ".join(f"{label} {round(cx[k]['used'])}%" for k, label in (("five_hour", "5h"), ("seven_day", "7d"))
                           if isinstance(cx.get(k), dict) and isinstance(cx[k].get("used"), (int, float)))
    except (OSError, ValueError, TypeError):
        pass
    return " | ".join(x for x in (f"Claude {claude}" if claude else "", f"Codex {codex}" if codex else "") if x)


def main() -> int:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")  # 1.1.1: UTF-8, not the Windows locale
    except ValueError:
        data = {}
    wins = windows(data if isinstance(data, dict) else {})
    try:
        record(wins)
    except OSError:
        pass
    cwd = Path(((data.get("workspace") or {}).get("current_dir")) or data.get("cwd") or os.getcwd()) if isinstance(data, dict) else Path.cwd()
    print(" · ".join(x for x in (project_part(cwd), limits_part(wins)) if x) or "Forge")
    return 0


if __name__ == "__main__":
    sys.exit(main())
