"""Gate: circle/orders/00_INDEX.md is the single source of truth for orders (K6, K7).

Checks: every row parses; ids unique; tier and state tokens valid; every listed file exists; every
`Nnnn_*.md` in orders/ is listed; a CLOSED order has an answer (`answers/Nnnn*.json`) and a report
(`reports/Nnnn*.md`) — the two-actor loop cannot be skipped.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from forge_common import read_index  # noqa: E402


def check(cfg: dict) -> list[str]:
    root: Path = cfg["_root"]
    circle = cfg["_layout"]["circle"]
    orders = circle / "orders"
    idx = orders / "00_INDEX.md"
    if not idx.is_file():
        return [f"{circle.relative_to(root).as_posix()}/orders/00_INDEX.md missing"]
    problems, seen = [], set()
    rows = read_index(root)
    table_lines = [l for l in idx.read_text(encoding="utf-8").splitlines() if re.match(r"^\| N\d", l)]
    if len(table_lines) != len(rows):
        problems.append(f"{len(table_lines) - len(rows)} index row(s) do not parse (format: | Nnnn | `file` | TIER | changes | STATE |)")
    for r in rows:
        if r["id"] in seen:
            problems.append(f"{r['id']}: duplicate id")
        seen.add(r["id"])
        if r["tier"] not in cfg["tiers"]:
            problems.append(f"{r['id']}: tier {r['tier']} not in {cfg['tiers']}")
        if r["state"] not in cfg["order_states"]:
            problems.append(f"{r['id']}: state {r['state']} not in {cfg['order_states']}")
        if not (orders / r["file"]).is_file():
            problems.append(f"{r['id']}: file orders/{r['file']} missing")
        elif not r["file"].startswith(r["id"] + "_"):
            problems.append(f"{r['id']}: file name must start with {r['id']}_")
        if r["state"] == "CLOSED":
            if not list((circle / "answers").glob(r["id"] + "*.json")):
                problems.append(f"{r['id']}: CLOSED without answers/{r['id']}.json")
            if not list((circle / "reports").glob(r["id"] + "*.md")):
                problems.append(f"{r['id']}: CLOSED without reports/{r['id']}.md")
    for f in orders.glob("N[0-9][0-9][0-9]_*.md"):
        if f.name not in {r["file"] for r in rows}:
            problems.append(f"orders/{f.name} not listed in 00_INDEX.md")
    return problems
