"""Limit-aware delegation - Forge 1.0.1, status line source 1.1.0 (K37; owner 2026-10-12: «ты должен сам делегировать это по-разному, опираясь на лимит»).

Neither Claude nor Codex is the default executor. Before the Lead hands out a task it asks this script, which compares
how much of each plan's rate limits is used and routes the task to the side with more room (ties go by the kind of task),
inside the hard rules: K15 (Codex only where the owner opened the project), K8 (Codex never reviews Codex), K29 (the plan,
the DoD, architecture, owner-facing text and every close stay with the Lead), K30 (important orders get verifier2).

Where the numbers come from (never invented; a missing reading is "unknown" and said so):
- Codex: the newest `rate_limits` event in Codex's own session logs (`$CODEX_HOME/sessions/**/rollout-*.jsonl`):
  primary = the 5-hour window, secondary = the weekly window, each with used_percent and resets_at;
- Claude: `~/.forge/claude_limits.json`, written by the Forge status line (statusline.py, 1.1.0) or by the forge-band mod from Claude Code's `session.measure` event
  (five_hour / seven_day, percentUsed, resetsAt) - or by hand: `limits.py set-claude --five-hour 42 --week 18` from /usage.
A window whose reset time has passed counts as 0%. Levels: ok < 70% <= tight < 90% <= critical < 99% <= exhausted (the worse window counts). A critical side gets
no delegated work while the other side has room; the Lead's own work goes on in economy mode; only exhausted waits.

Usage:
  python limits.py show [--json]
  python limits.py route --kind code|mechanical|review|research|text|plan [--made-by claude|codex] [--important] [--order N] [--json]
  python limits.py set-claude --five-hour 42 [--week 18] [--five-hour-resets ISO] [--week-resets ISO]
Kinds: code = implementation of a planned task; mechanical = edits/conversions by exact spec; review = second actor on a
result; research = questions about code, sources, facts; text = owner-facing text; plan = DoD, plan, architecture, decisions.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import glob
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TIGHT, CRITICAL, EXHAUSTED = 70.0, 90.0, 99.0
TIE = 10.0  # points of difference below which the kind of task decides


def forge_home() -> Path:
    return Path(os.environ.get("FORGE_HOME") or Path.home() / ".forge")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _iso(ts) -> str:
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="minutes")
        return str(ts or "")
    except (OverflowError, OSError, TypeError, ValueError):
        return ""


def _time(ts) -> datetime | None:
    """Parse an ISO time or Unix timestamp; naive times are UTC."""
    try:
        when = (datetime.fromtimestamp(ts, tz=timezone.utc) if isinstance(ts, (int, float))
                else datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else None)
        return when.replace(tzinfo=timezone.utc) if when is not None and when.tzinfo is None else when
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _window(used, resets) -> dict | None:
    """{used, resets_at}; unknown usage stays unknown, even after a reset."""
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    try:
        used = float(used)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(used):
        return None
    when = _iso(resets)
    reset = _time(resets)
    past = reset is not None and reset <= datetime.now(timezone.utc)
    return {"used": 0.0 if past else used, "resets_at": when, "reset_since_reading": past}


def codex_limits(max_files: int = 40) -> dict | None:
    """The newest rate_limits event across the newest session files, or None."""
    files = glob.glob(str(codex_home() / "sessions" / "**" / "rollout-*.jsonl"), recursive=True)
    latest, latest_at = None, None
    for f in sorted(files, key=os.path.getmtime, reverse=True)[:max_files]:
        try:
            mtime = os.path.getmtime(f)
            lines = Path(f).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            payload = ev.get("payload")
            rl = (payload.get("rate_limits") if isinstance(payload, dict) else None)
            if not isinstance(rl, dict):
                rl = ev.get("rate_limits")
            if not isinstance(rl, dict):
                continue
            p, s = rl.get("primary"), rl.get("secondary")
            p = p if isinstance(p, dict) else {}
            s = s if isinstance(s, dict) else {}
            event_at = _time(ev.get("timestamp")) or datetime.fromtimestamp(mtime, tz=timezone.utc)
            if latest_at is None or event_at > latest_at:
                latest_at = event_at
                latest = {"provider": "codex", "plan": rl.get("plan_type") or "",
                          "seen_at": ev.get("timestamp") or _iso(mtime),
                          "five_hour": _window(p.get("used_percent"), p.get("resets_at")),
                          "seven_day": _window(s.get("used_percent"), s.get("resets_at")),
                          "limit_reached": rl.get("rate_limit_reached_type") or "", "source": Path(f).name}
            break
    return latest


def claude_limits() -> dict | None:
    """The last reading the forge-band mod (or `set-claude`) left in ~/.forge/claude_limits.json, or None."""
    f = forge_home() / "claude_limits.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("rateLimits"), list):
        return None
    wins = {w.get("kind"): w for w in d["rateLimits"] if isinstance(w, dict) and isinstance(w.get("kind"), str)}
    if "five_hour" not in wins and "seven_day" not in wins:
        return None
    source = d.get("source", "forge-band")
    updated = _time(d.get("updated"))
    windows = {}
    for kind, duration in (("five_hour", timedelta(hours=5)), ("seven_day", timedelta(days=7))):
        w = wins.get(kind) or {}
        window = _window(w.get("percentUsed"), w.get("resetsAt"))
        if isinstance(source, str) and source.startswith("set-claude") and not w.get("resetsAt"):
            if updated is None or datetime.now(timezone.utc) - updated > duration:
                window = None
        windows[kind] = window
    return {"provider": "claude", "seen_at": d.get("updated", ""), "source": source, **windows}


def pressure(lim: dict | None) -> float | None:
    if not lim:
        return None
    return max((w["used"] for w in (lim.get("five_hour"), lim.get("seven_day")) if w is not None), default=None)


def level(p: float | None) -> str:
    return ("unknown" if p is None else "exhausted" if p >= EXHAUSTED else "critical" if p >= CRITICAL
            else "tight" if p >= TIGHT else "ok")


def earliest_reset(lim: dict | None) -> str:
    if not lim:
        return ""
    windows = [w for w in (lim.get("five_hour"), lim.get("seven_day"))
               if w is not None and _time(w["resets_at"]) is not None]
    critical = [w for w in windows if w["used"] >= CRITICAL]
    return min(critical or windows, key=lambda w: _time(w["resets_at"]))["resets_at"] if windows else ""


def fmt(lim: dict | None, name: str) -> str:
    if not lim:
        return f"{name} unknown"
    parts = [f"{label} {w['used']:.0f}%" for label, w in (("5h", lim.get("five_hour")), ("7d", lim.get("seven_day")))
             if w is not None]
    return f"{name} " + (" · ".join(parts) if parts else "unknown")


def gpt_open(root: Path | None) -> tuple[bool, str]:
    """K15: may Codex see this project? (forge.json "gpt": "allowed" and not under the owner's deny list)"""
    if root is None:
        return False, "not inside a Forge project"
    try:
        import gpt_bridge
        gpt_bridge.check_paths([root])
        return True, ""
    except Exception as e:  # PolicyError or a broken bridge: Codex is off for this decision
        return False, str(e).split(" - ")[0][:160]


EXECUTOR = {  # what each side runs for a kind
    "code": {"codex": ("CODEX", "codex_exec.py run/wave (patch mode)"), "claude": ("HEAVY", "agent `executor`")},
    "mechanical": {"codex": ("CODEX", "codex_exec.py run (patch mode)"), "claude": ("FAST", "agent `executor-fast`")},
    "review": {"codex": ("GPT-REVIEW", "verify_gpt.py N"), "claude": ("VERIFIER2", "agent `verifier2`")},
    "research": {"codex": ("GPT-ADVISE", "verify_gpt.py advise / factcheck"), "claude": ("INVESTIGATOR", "agent `investigator` (code) or `researcher` (web)")},
}
TIE_PREFERS = {"code": "codex", "mechanical": "codex", "review": "codex", "research": "claude"}


def route(kind: str, *, root: Path | None, made_by: str = "claude", important: bool = False,
          claude: dict | None = None, codex: dict | None = None) -> dict:
    """The decision for one task: {decision, executor, how, reason, ...} - pure, so it is testable."""
    pc, px = pressure(claude), pressure(codex)
    lc, lx = level(pc), level(px)
    nums = f"{fmt(claude, 'Claude')} | {fmt(codex, 'Codex')}"
    out = {"kind": kind, "claude_level": lc, "codex_level": lx, "numbers": nums}
    if kind in ("plan", "text"):  # K29: the Lead's own work, never delegated by limits
        if lc == "exhausted":
            return {**out, "decision": "WAIT", "executor": "LEAD", "how": "the Lead",
                    "reason": f"{kind} stays with the Lead (K29), but Claude's limit is exhausted - wait for the reset "
                              f"({earliest_reset(claude)}) or ask the owner"}
        return {**out, "decision": "LEAD", "executor": "LEAD", "how": "the Lead", "reason": f"{kind} stays with the Lead (K29)"
                + (f" - ECONOMY: Claude {lc} ({fmt(claude, 'Claude')}): short, delegate everything else" if lc == "critical" else "")}
    open_, why_closed = gpt_open(root)
    codex_can = open_ and lx not in ("critical", "exhausted")
    claude_can = lc not in ("critical", "exhausted")
    if kind == "review":
        if made_by == "codex":  # K8: Codex never reviews Codex
            ex = "VERIFIER2" if important else "LEAD"
            return {**out, "decision": ex, "executor": ex, "how": "agent `verifier2`" if important else "the Lead reads the diff",
                    "reason": "Codex-made work is checked by Claude (K8)" + (" - important: verifier2 (K30)" if important else "")}
        if important:  # K30: verifier2 always; the Codex audit on top when Codex has room
            extra = "+ Codex audit (verify_gpt.py N, triage)" if codex_can else f"Codex audit skipped: {why_closed or 'Codex limit ' + lx} (--rung3-skip)"
            return {**out, "decision": "VERIFIER2", "executor": "VERIFIER2", "how": f"agent `verifier2` {extra}",
                    "reason": "important order: verifier2 is mandatory (K30)"}
    if kind not in EXECUTOR:
        raise SystemExit(f"unknown kind {kind!r} - one of: code, mechanical, review, research, text, plan")
    if not codex_can and not claude_can:
        usable = [x for x, lv, ok in (("codex", lx, open_), ("claude", lc, True)) if ok and lv != "exhausted"]
        if not usable:
            resets = sorted(x for x in (earliest_reset(claude), earliest_reset(codex)) if x)
            return {**out, "decision": "WAIT", "executor": "-", "how": "-",
                    "reason": f"no room left ({nums}) - wait for the first reset ({resets[0] if resets else 'unknown'}); "
                              f"grunt work may go to Ollama in cheap mode"}
        side = min(usable, key=lambda x: (px if x == "codex" else pc) or 0)  # both critical: the less loaded, flagged
        ex, how = EXECUTOR[kind][side]
        if side == "codex":
            how += " - run with --force (both limits critical, Codex is the less loaded)"
        why = (f"Codex is off here ({why_closed}) and Claude is {lc} ({fmt(claude, 'Claude')}) - keep the task small"
               if not open_ else f"both limits critical ({nums}) - the less loaded side, keep the task small")
        return {**out, "decision": ex, "executor": ex, "how": how, "side": side, "reason": why}
    if not codex_can:
        side, why = "claude", (f"Codex is off here ({why_closed})" if not open_ else f"Codex limit critical ({fmt(codex, 'Codex')})")
    elif not claude_can:
        side, why = "codex", f"Claude limit critical ({fmt(claude, 'Claude')})"
    elif pc is None or px is None:
        side = TIE_PREFERS[kind]
        why = f"{'Claude' if pc is None else 'Codex'} limit unknown - the kind decides ({kind} -> {side})"
    elif abs(pc - px) < TIE:
        side = TIE_PREFERS[kind]
        why = f"limits close ({nums}) - the kind decides ({kind} -> {side})"
    else:
        side = "codex" if px < pc else "claude"
        why = f"{'Codex' if side == 'codex' else 'Claude'} has more room ({nums})"
    ex, how = EXECUTOR[kind][side]
    return {**out, "decision": ex, "executor": ex, "how": how, "side": side, "reason": why}


def project_root() -> Path | None:
    for d in [Path.cwd(), *Path.cwd().parents]:
        if (d / "forge.json").is_file():
            return d
    return None


def cache(claude: dict | None, codex: dict | None) -> None:
    """~/.forge/codex_limits.json for the band (it cannot read Codex's logs cheaply); never fails."""
    try:
        if codex:
            forge_home().mkdir(parents=True, exist_ok=True)
            (forge_home() / "codex_limits.json").write_text(json.dumps(codex, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def brief_line(root: Path | None) -> str:
    """One line for the SessionStart brief: both limits and where code would go now."""
    cl, cx = claude_limits(), codex_limits()
    cache(cl, cx)
    r = route("code", root=root, claude=cl, codex=cx)
    return f"  limits: {fmt(cl, 'Claude')} | {fmt(cx, 'Codex')} -> code now: {r['decision']} ({r['how']})"


def _reset_arg(value: str) -> str:
    if _time(value) is None:
        raise argparse.ArgumentTypeError("reset time must be an ISO timestamp")
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show"); s.add_argument("--json", action="store_true")
    r = sub.add_parser("route"); r.add_argument("--kind", required=True)
    r.add_argument("--made-by", default="claude", choices=["claude", "codex"]); r.add_argument("--important", action="store_true")
    r.add_argument("--order"); r.add_argument("--json", action="store_true")
    c = sub.add_parser("set-claude"); c.add_argument("--five-hour", type=float, required=True); c.add_argument("--week", type=float)
    c.add_argument("--five-hour-resets", type=_reset_arg); c.add_argument("--week-resets", type=_reset_arg)
    a = ap.parse_args()
    if a.cmd == "set-claude":
        if a.week_resets is not None and a.week is None:
            ap.error("--week-resets requires --week")
        if not math.isfinite(a.five_hour) or (a.week is not None and not math.isfinite(a.week)):
            ap.error("usage percentages must be finite numbers")
        wins = [{"kind": "five_hour", "percentUsed": a.five_hour}] + ([{"kind": "seven_day", "percentUsed": a.week}] if a.week is not None else [])
        for w, resets in zip(wins, (a.five_hour_resets, a.week_resets)):
            if resets is not None:
                w["resetsAt"] = resets
        forge_home().mkdir(parents=True, exist_ok=True)
        (forge_home() / "claude_limits.json").write_text(json.dumps({"rateLimits": wins, "source": "set-claude (by hand)",
                                                                     "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n", encoding="utf-8")
        print("saved:", fmt(claude_limits(), "Claude"))
        return 0
    cl, cx = claude_limits(), codex_limits()
    cache(cl, cx)
    if a.cmd == "show":
        data = {"claude": cl, "codex": cx, "claude_level": level(pressure(cl)), "codex_level": level(pressure(cx))}
        if a.json:
            print(json.dumps(data, ensure_ascii=False))
        else:
            for name, lim in (("Claude", cl), ("Codex", cx)):
                if not lim:
                    print(f"{name}: unknown" + (" - the forge-band mod records it in Claude Code, or `limits.py set-claude`"
                                                if name == "Claude" else " - no Codex session log with rate limits yet"))
                    continue
                parts = [f"{k} {w['used']:.0f}% (resets {w['resets_at'] or '?'})" for k, w in (("5h", lim["five_hour"]), ("7d", lim.get("seven_day"))) if w]
                print(f"{name}: {level(pressure(lim))} - " + ", ".join(parts) + f" - read {lim['seen_at']}")
        return 0
    root = project_root()
    res = route(a.kind, root=root, made_by=a.made_by, important=a.important, claude=cl, codex=cx)
    if a.order and root is not None:
        from forge_common import load_cfg
        f = load_cfg(root)["_layout"]["circle"] / "answers" / f"{a.order}_route.json"
        log = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {"order": a.order, "decisions": []}
        log["decisions"].append({**res, "when": datetime.now().isoformat(timespec="seconds")})
        f.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(res, ensure_ascii=False) if a.json else f"route {a.kind} -> {res['decision']}: {res['how']} - {res['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
