"""Verifier 0 — run every gate_*.py in this folder against the project root; exit 1 if any fails.

    python <plugin>/gates/run_gates.py             # red gates with their problems, then one summary line (green is silent)
    python <plugin>/gates/run_gates.py --verbose   # every gate with its time
    python <plugin>/gates/run_gates.py --json      # machine output (used by step.py)

Each gate module exposes `check(cfg) -> list[str]` (empty list = green). Results are also written to
`<state>/gates_last.json` so other tools can read them without re-running.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from forge_common import find_root, load_cfg, today  # noqa: E402


def run(cfg: dict) -> dict:
    results = {}
    for f in sorted(HERE.glob("gate_*.py")):
        spec = importlib.util.spec_from_file_location(f.stem, f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        t0 = time.time()
        try:
            problems = mod.check(cfg)
        except Exception as e:  # a crashing gate is a red gate, never a silent pass
            problems = [f"gate crashed: {e!r}"]
        results[f.stem] = {"ok": not problems, "problems": problems, "ms": int((time.time() - t0) * 1000)}
    return results


def main() -> int:
    root = find_root()
    cfg = load_cfg(root)
    res = run(cfg)
    out = {"date": today(), "ok": all(r["ok"] for r in res.values()), "gates": res}
    state_dir = cfg["_layout"]["state"]
    state_dir.mkdir(exist_ok=True)
    (state_dir / "gates_last.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False))
    else:
        verbose = "--verbose" in sys.argv  # 1.1.0: success is silent, failures are verbose - every line reaches the Lead's context
        for name, r in res.items():
            if verbose or not r["ok"]:
                print(f"{'PASS' if r['ok'] else 'FAIL'}  {name:<16} {r['ms']:>5} ms" + ("" if r["ok"] else f"  ({len(r['problems'])})"))
            for p in r["problems"][:20]:
                print(f"      - {p}")
        n, ms = len(res), sum(r["ms"] for r in res.values())
        print(f"ALL GREEN ({n} gates, {ms} ms)" if out["ok"] else "RED - fix before closing an order or committing")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
