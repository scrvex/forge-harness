"""Lessons that became guards - Forge 1.1.0 (D3; the idea of hookify in anthropics/claude-code, rewritten for Forge).

A lesson that keeps coming back is a rule nobody enforces. Here it becomes a guard: a regular expression the hooks check
before a shell command runs (`on: bash`, PreToolUse) or after a file is written (`on: write`, PostToolUse), with the
lesson it came from. `warn` tells the Lead and lets the call go on; `block` refuses a command (a written file cannot be
un-written: a `block` on write is reported like a red gate). Guards live in the project (`<config>/guards.json`), are added
only with the owner's yes (K17: lessons -> audit -> the owner approves), and every one names its lesson.

Usage:
  python guards.py list
  python guards.py add --on bash|write --pattern REGEX --action warn|block --message "what to do instead" --lesson L012 --by "owner, 2026-10-13"
  python guards.py remove G001
  python guards.py propose            # lessons that repeat - candidates for forge-audit, never added on their own
  python guards.py test "<command or text>" [--on bash]   # which guards would fire
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ACTIONS = ("warn", "block")
EVENTS = ("bash", "write")


def guards_file(cfg: dict) -> Path:
    return cfg["_layout"]["config"] / "guards.json"


def load(cfg: dict) -> list[dict]:
    try:
        data = json.loads(guards_file(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [g for g in data.get("guards", []) if isinstance(g, dict)] if isinstance(data, dict) else []


def save(cfg: dict, guards: list[dict]) -> None:
    f = guards_file(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"guards": guards}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def fired(guards: list[dict], on: str, text: str) -> list[dict]:
    """The guards of this event whose pattern matches `text` (a broken pattern never fires and never crashes the hook)."""
    out = []
    for g in guards:
        if g.get("on") != on:
            continue
        try:
            if re.search(str(g.get("pattern", "")), text):
                out.append(g)
        except re.error:
            continue
    return out


# 1.2.0: on `write` the hook (gates/check_file.py) answers `block` with exit 2 + stderr and `warn` with exit 0 + additionalContext.
def verdict(cfg: dict, on: str, text: str) -> tuple[str, str]:
    """('block' | 'warn' | '', message) for the hooks."""
    hits = fired(load(cfg), on, text)
    if not hits:
        return "", ""
    action = "block" if any(g.get("action") == "block" for g in hits) else "warn"
    msg = " | ".join(f"[Forge guard {g.get('id')}, lesson {g.get('lesson', '?')}] {g.get('message', '')}" for g in hits)
    return action, msg


def check_hook(on: str, text: str, cwd: str) -> tuple[str, str]:
    """For the hooks: the project of `cwd` (none -> nothing), its guards against `text`; never raises."""
    try:
        from forge_common import load_cfg
        start = Path(cwd)
        root = next((d for d in (start, *start.parents) if (d / "forge.json").is_file()), None)
        if root is None:
            return "", ""
        return verdict(load_cfg(root), on, text)
    except Exception:
        return "", ""  # a broken guard fails open: K15 and the gates still hold


def lessons(cfg: dict) -> list[dict]:
    out = []
    for f in sorted((cfg["_layout"]["circle"] / "lessons").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            d["_file"] = f.name
            out.append(d)
    return out


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w-]+", text.lower()) if len(w) > 2}


def similar(a: set[str], b: set[str]) -> bool:
    """Same failure class: most of the shorter description's words recur in the other (a rough, explainable test)."""
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.7


def propose(cfg: dict) -> list[dict]:
    """Lessons whose failure repeats (a similar `what_failed` twice or more), with a guard command when the fix names a
    command in backticks. Grouping is a word overlap - the Lead reads the lessons before proposing a guard."""
    groups: dict[str, list[dict]] = defaultdict(list)
    seeds: list[tuple[str, set[str]]] = []
    for les in lessons(cfg):
        w = words(str(les.get("what_failed", "")))
        if not w:
            continue
        k = next((name for name, seed in seeds if similar(w, seed)), "")
        if not k:
            k = " ".join(sorted(w))[:80]
            seeds.append((k, w))
        groups[k].append(les)
    out = []
    for k, items in groups.items():
        if len(items) < 2:
            continue
        ids = [x.get("id", x["_file"]) for x in items]
        cmd = next((m.group(1) for x in items if (m := re.search(r"`([^`]{3,80})`", str(x.get("fix", ""))))), "")
        out.append({"class": k, "lessons": ids, "fix": str(items[-1].get("fix", ""))[:200],
                    "suggest": (f"guards.py add --on bash --pattern {json.dumps(re.escape(cmd))} --action warn "
                                f"--message \"{str(items[-1].get('fix', ''))[:120]}\" --lesson {ids[-1]} --by \"owner, <date>\"")
                    if cmd else ""})
    return out


def main() -> int:
    from forge_common import find_root, load_cfg
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("--on", choices=EVENTS, required=True); a.add_argument("--pattern", required=True)
    a.add_argument("--action", choices=ACTIONS, default="warn"); a.add_argument("--message", required=True)
    a.add_argument("--lesson", required=True); a.add_argument("--by", required=True, help="who approved it and when (K17)")
    r = sub.add_parser("remove"); r.add_argument("id")
    sub.add_parser("propose")
    t = sub.add_parser("test"); t.add_argument("text"); t.add_argument("--on", choices=EVENTS, default="bash")
    args = ap.parse_args()
    cfg = load_cfg(find_root())
    guards = load(cfg)
    if args.cmd == "list":
        for g in guards:
            print(f"{g['id']} {g['on']:<5} {g['action']:<5} /{g['pattern']}/ - {g['message']} (lesson {g.get('lesson')}, {g.get('by', '')})")
        print(f"{len(guards)} guard(s) in {guards_file(cfg).as_posix()}")
        return 0
    if args.cmd == "add":
        from forge_common import refuse_in_codex
        refuse_in_codex("guards.py add")
        try:
            re.compile(args.pattern)
        except re.error as e:
            raise SystemExit(f"not a regular expression: {e}")
        gid = f"G{max([int(g['id'][1:]) for g in guards if re.fullmatch(r'G\d+', str(g.get('id', '')))] or [0]) + 1:03d}"
        guards.append({"id": gid, "on": args.on, "pattern": args.pattern, "action": args.action, "message": args.message,
                       "lesson": args.lesson, "by": args.by, "added": date.today().isoformat()})
        save(cfg, guards)
        print(f"{gid} added: {args.action} on {args.on} /{args.pattern}/")
        return 0
    if args.cmd == "remove":
        left = [g for g in guards if g.get("id") != args.id]
        if len(left) == len(guards):
            raise SystemExit(f"no guard {args.id}")
        save(cfg, left)
        print(f"{args.id} removed")
        return 0
    if args.cmd == "test":
        action, msg = verdict(cfg, args.on, args.text)
        print(f"{action.upper()}: {msg}" if action else "no guard fires")
        return 0
    cands = propose(cfg)
    for c in cands:
        print(f"- {', '.join(c['lessons'])}: {c['class']} -> fix: {c['fix']}")
        if c["suggest"]:
            print(f"    candidate: python {Path(__file__).as_posix()} {c['suggest']}")
    print(f"{len(cands)} repeating lesson class(es) - the owner decides which become guards (K17)" if cands else
          "no lesson repeats yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
