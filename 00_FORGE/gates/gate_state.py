"""Gate: circle/STATE.md agrees with the order index (K9).

STATE token valid; CURRENT_ORDER is `-` or an existing order id; RUNNING <=> exactly one TAKEN order and it is
CURRENT_ORDER; READY => no TAKEN orders.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from forge_common import read_index, read_state  # noqa: E402


def check(cfg: dict) -> list[str]:
    root: Path = cfg["_root"]
    st = read_state(root)
    if not st:
        return ["circle/STATE.md missing or empty (needs STATE: / UPDATED: / CURRENT_ORDER: lines)"]
    problems = []
    state = st.get("STATE", "")
    if state not in cfg["states"]:
        problems.append(f"STATE '{state}' not in {cfg['states']}")
    rows = {r["id"]: r for r in read_index(root)}
    taken = [i for i, r in rows.items() if r["state"] == "TAKEN"]
    cur = st.get("CURRENT_ORDER", "-")
    if cur != "-" and cur not in rows:
        problems.append(f"CURRENT_ORDER {cur} is not in the index")
    if state == "RUNNING":
        heavy = [i for i in taken if rows[i]["tier"] not in ("LOCAL", "GPT", "CODEX")]  # external executors run alongside (R26, K29)
        if not taken:
            problems.append("RUNNING but no order is TAKEN")
        elif len(heavy) > 1:
            problems.append(f"RUNNING: more than one non-LOCAL order TAKEN {heavy} (R26: only LOCAL/GPT/CODEX orders run in parallel)")
        elif cur not in taken:
            problems.append(f"RUNNING: CURRENT_ORDER {cur} is not among TAKEN {taken}")
        elif heavy and cur != heavy[0]:
            problems.append(f"RUNNING: CURRENT_ORDER {cur} should be the non-LOCAL order {heavy[0]}")
    elif state == "READY" and taken:
        problems.append(f"READY but orders TAKEN: {taken}")
    return problems
