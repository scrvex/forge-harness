"""Write a lesson to circle/lessons/YYYY-MM-DD_Lnnn.json (K10, K12, K17).

    lesson.py --failed "what failed" --cause "root cause" --fix "what changes" --applies kernel --source gate
    lesson.py --worked "what worked" --cause "..." --fix "keep it as ..." --applies project --source review
Options: --phase 1 --confidence high|medium|low --evidence path --order N001
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
from forge_common import find_root, load_cfg, today  # noqa: E402


def next_id(lessons_dir: Path) -> str:
    ids = [int(m[1]) for f in lessons_dir.glob("*_L*.json") if (m := re.search(r"_L(\d+)\.json$", f.name))]
    return f"L{(max(ids) + 1 if ids else 1):03d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--failed"); g.add_argument("--worked")
    ap.add_argument("--cause", required=True); ap.add_argument("--fix", required=True)
    ap.add_argument("--applies", required=True, choices=["kernel", "pack", "project"])
    ap.add_argument("--source", required=True, choices=["gate", "review", "owner", "retro", "overrule"])  # overrule: 0.8 K31
    ap.add_argument("--phase", type=int); ap.add_argument("--confidence", default="medium", choices=["high", "medium", "low"])
    ap.add_argument("--evidence", help="path to the file/journal/log that shows it (mandatory unless --source owner; audit §7.7)"); ap.add_argument("--order")
    a = ap.parse_args()
    from forge_common import refuse_in_codex
    refuse_in_codex("lesson.py")  # K29: the Lead's job, never ChatGPT's
    if not a.evidence and a.source != "owner":
        raise SystemExit("lesson.py: --evidence is required (a path or order id that proves the lesson) — models confabulate memories; no evidence, no lesson")
    root = find_root(); cfg = load_cfg(root)
    d = cfg["_layout"]["circle"] / "lessons"; d.mkdir(parents=True, exist_ok=True)
    lid = next_id(d)
    data = {"id": lid, "when": today(), "project": cfg.get("project", root.name), "phase": a.phase, "source": a.source,
            **({"what_failed": a.failed} if a.failed else {"what_worked": a.worked}),
            "root_cause": a.cause, "fix": a.fix, "applies_to": a.applies, "confidence": a.confidence,
            "evidence": a.evidence, "order": a.order}
    f = d / f"{today()}_{lid}.json"
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if a.applies == "kernel" and not cfg.get("kernel_mutable"):  # K24: proposals to Forge, never edits of Core 1
        prop = cfg["_layout"]["docs"] / "KERNEL_PROPOSALS.md"; prop.parent.mkdir(parents=True, exist_ok=True)
        if not prop.is_file():
            prop.write_text("# Proposals to Forge Core 1 (from this project; the kernel itself is immutable here, K23)\n\n", encoding="utf-8", newline="\n")
        with prop.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"- {today()} {lid}: {a.failed or a.worked} -> {a.fix}\n")
    print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
