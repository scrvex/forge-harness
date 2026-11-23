"""Replay today's checks on closed work (K34f, Forge 0.9.2) - read-only, 0 tokens, nothing is changed or reopened.

    replay.py                    # every CLOSED order of this project
    replay.py --orders N010,N012 [--figures] [--json]

For each closed order it re-runs, on the files its answer declared:
  - gate_lessons (order.py lesson_checks: built-in failure classes, lessons with a `check` pattern, the user-language rule);
  - check_html.py on .html files;
  - with --figures: check_figures.py on the declared deliverables (.md) of invest / analytics / marketing orders (--strict:
    invest-only orders). Off by default: in a multi-domain order it also hits evidence tables and meta reports (first
    replay, N012 - a rule applied where the project does not apply it is a false positive, L015);
  - and reports declared files that no longer exist.
Use before a plugin release (forge-audit): a finding is either a true catch the old process missed (write a lesson,
fix the artifact in a new order if it matters) or a false positive of the new check (fix the check before releasing).
Deck review 2026-10-08: "the pipeline is good only if it finds what the previous process missed".
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gates"))
from forge_common import find_root, load_cfg, read_index  # noqa: E402

DOMAIN_LINE = re.compile(r"^- \*\*Domain \(K32\):\*\* (\S+)", re.M)


def replay(root: Path, cfg: dict, ids: list[str] | None = None, figures: bool = False) -> list[dict]:
    import check_figures
    import check_html
    import order as order_mod  # lesson_checks: the same function `order.py close` runs
    circle = cfg["_layout"]["circle"]
    out = []
    for r in read_index(root):
        if r["state"] != "CLOSED" or (ids and r["id"] not in ids):
            continue
        ans = circle / "answers" / f"{r['id']}.json"
        files = json.loads(ans.read_text(encoding="utf-8")).get("files_changed", []) if ans.is_file() else []
        order_md = (circle / "orders" / r["file"]).read_text(encoding="utf-8") if (circle / "orders" / r["file"]).is_file() else ""
        m = DOMAIN_LINE.search(order_md)
        doms = set(m.group(1).split("+")) if m else set()
        declared = order_mod.declared_deliverables(order_md)
        found: list[str] = []
        found += [f"missing now: {f}" for f in files if not (root / f).exists()]
        try:
            found += [f"gate_lessons: {p}" for p in order_mod.lesson_checks(r["id"])]
        except Exception as e:  # an old answer shape must not stop the replay
            found.append(f"gate_lessons could not run: {e}")
        for f in files:
            p = root / f
            if not p.is_file():
                continue
            if p.suffix.lower() == ".html":
                probs, _ = check_html.check(p)
                found += [f"check_html: {f}: {x}" for x in probs]
            # figures discipline only where the order applied it: declared deliverables of invest/analytics/marketing orders;
            # --strict only for an invest-only order (first replay: notes and READMEs of a 9-domain order were false positives)
            if figures and p.suffix.lower() == ".md" and doms & {"invest", "analytics", "marketing"} and f in declared:
                strict = doms == {"invest"}
                probs = check_figures.check(p.read_text(encoding="utf-8", errors="replace"), strict=strict)
                found += [f"check_figures{' --strict' if strict else ''}: {f}: {x}" for x in probs]
        out.append({"order": r["id"], "files": len(files), "domains": sorted(doms), "findings": found})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orders", help="comma-separated ids (default: every CLOSED order)"); ap.add_argument("--json", action="store_true")
    ap.add_argument("--figures", action="store_true", help="also check_figures on declared deliverables of invest/analytics/marketing orders")
    a = ap.parse_args()
    root = find_root(); cfg = load_cfg(root)
    res = replay(root, cfg, [x.strip() for x in a.orders.split(",")] if a.orders else None, a.figures)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1)); return 0
    hit = [x for x in res if x["findings"]]
    print(f"replay of {len(res)} closed order(s): {len(hit)} with findings, {sum(len(x['findings']) for x in res)} finding(s)")
    for x in hit:
        print(f"{x['order']} ({x['files']} file(s){', ' + '+'.join(x['domains']) if x['domains'] else ''}):")
        for f in x["findings"][:12]:
            print(f"  - {f}")
        if len(x["findings"]) > 12:
            print(f"  ... {len(x['findings']) - 12} more (--json)")
    print("triage: true catch -> lesson (+ a fixing order if it matters); false positive -> fix the check before the release")
    return 0


if __name__ == "__main__":
    sys.exit(main())
