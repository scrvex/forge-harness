"""The runner: where are we and what is the next action? (K6, K9, K11)

    step.py                    # STATE, current order, open orders, gates, suggested next command
    step.py --brief [--hook]   # 15-line brief (SessionStart hook; after a compaction it adds the order's request and handoff.md)
    step.py --stop-check [--hook]   # Stop hook: runbook as a JSON systemMessage, once per new situation; blocks a stop only during
                               # an autonomous run with takeable orders, once per state of the work (autorun.py); --plain = text
    step.py rule K8            # full text of one constitution rule
    step.py mode cheap|expensive   # K25: record the owner's mode in forge.json
    step.py end --did "..." [--tokens 150k [--order N007]] [--blockers -] [--commit "msg"]   # session end: gates, session_log row, CONTEXT date, commit
    step.py --json | --no-gates

Deterministic, 0 tokens. The Lead runs it at the start of every session and after every order transition.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import PLUGIN_DIR, find_root, load_cfg, read_index, read_state, today  # noqa: E402

RULE_RE = re.compile(r"^- \*\*(K\d+)[^*]*\*\*[:.]?\s*(.*)$")


def gates(root: Path, cfg: dict) -> dict | None:
    res = subprocess.run([sys.executable, str(cfg["_kernel_dir"] / "gates" / "run_gates.py"), "--json"],
                         capture_output=True, text=True, cwd=root)
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "gates": {}, "error": (res.stdout + res.stderr)[-500:]}


KT = (PLUGIN_DIR / "tools").as_posix()  # absolute: the plugin lives outside the project
LOAD_CACHE_S = 90    # a session start and a compaction right after it must not probe the machine twice
LOAD_DEADLINE_S = 4  # the SessionStart hook has 20 s in all; a killed hook loses the whole brief, not one line


def live_load() -> dict | None:
    """machine.load() for the brief: cached for LOAD_CACHE_S in the temp dir and run under a LOAD_DEADLINE_S deadline
    (on Windows it starts PowerShell several times). None = no answer in time: the brief simply has no machine line."""
    f = Path(tempfile.gettempdir()) / "forge_machine_load.json"
    try:
        c = json.loads(f.read_text(encoding="utf-8"))
        if time.time() - float(c["t"]) < LOAD_CACHE_S and isinstance(c["v"], dict) and "verdict" in c["v"]:
            return c["v"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    from machine import load
    box: dict = {}

    def work() -> None:
        try:
            box["v"] = load()
        except Exception:  # noqa: BLE001 - a visibility aid
            pass
    th = threading.Thread(target=work, daemon=True)
    th.start(); th.join(LOAD_DEADLINE_S)
    v = box.get("v")
    if v:
        try:
            f.write_text(json.dumps({"t": time.time(), "v": v}), encoding="utf-8", newline="\n")
        except OSError:
            pass
    return v


def suggest(state: str, rows: list[dict], gates_ok: bool | None, mode: str | None = "cheap") -> str:
    taken = [r for r in rows if r["state"] == "TAKEN"]
    open_ = [r for r in rows if r["state"] == "OPEN"]
    if mode is None:
        return f"K25: ask the owner which mode to use — cheap (Ollama drafts) or expensive (Claude only) — then: python {KT}/step.py mode <cheap|expensive>"
    if gates_ok is False:
        return f"fix red gates first: python {KT.replace('/tools', '/gates')}/run_gates.py"
    if state == "AWAITING_OWNER":
        return "waiting for the owner — executors blocked (K9); prepare the RU question, do not start orders"
    if state in ("STOPPED_BY_OWNER", "ENVIRONMENT_BROKEN"):
        return f"{state}: only the owner (or an environment fix) can move STATE back to READY"
    if taken:
        r = taken[0]
        return (f"{r['id']} is TAKEN ({r['tier']}): execute in scope, then "
                f"python {KT}/order.py done {r['id']} --text \"verdict\" [--executor ... --summary ...] --commit")
    if open_:
        r = open_[0]
        return f"take the next OPEN order: python {KT}/order.py take {r['id']}   ({r['changes'][:60]})"
    return f'no open orders — plan the next unit: python {KT}/order.py new "title" --tier LEAD --changes "..."'


def rule(cfg: dict, kid: str) -> str:
    """`step.py rule K8` — full text of one constitution rule (its bullet and continuation lines)."""
    kid = kid.upper(); out, on = [], False
    for line in (PLUGIN_DIR / "CONSTITUTION.md").read_text(encoding="utf-8").splitlines():
        m = RULE_RE.match(line)
        if m:
            if on:
                break
            on = m[1] == kid
        elif on and (line.startswith("- ") or line.startswith("#")):
            break
        if on:
            out.append(line)
    return "\n".join(out) or f"{kid}: not found"


def set_mode(root: Path, value: str) -> None:
    """`step.py mode cheap|expensive` — the owner's answer to the K25 question, recorded in forge.json."""
    f = root / "forge.json"; cfg = json.loads(f.read_text(encoding="utf-8"))
    cfg["mode"] = value
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"mode = {value} ({'Ollama drafts by DoD, Claude judges' if value == 'cheap' else 'Claude only, LOCAL orders refused'})")


def lead_tokens(root: Path, cfg: dict, raw: str, order: str = "") -> Path | None:
    """R-37 (0.9.3): `end --tokens 150k [--order N007]` books the Lead's own share (an estimate - the session's context size)
    to an order: --order, else the only candidate among the orders TAKEN in the last 24 h and the orders closed today. Several
    candidates (another session may share the folder) -> nothing is guessed, the figure stays in session_log only."""
    from usage import journal, ledger_path, parse_tokens, record, taken_recently
    n = parse_tokens(raw) if raw not in ("-", "") else None
    if n is None:
        if raw not in ("-", ""):
            print(f"tokens: {raw!r} is not a number - kept in session_log only")
        return None
    rows = {r["id"]: r for r in read_index(root)}
    if order:
        if order not in rows:
            raise SystemExit(f"--order {order}: not in the orders index - nothing written")
        cands = [order]
    else:
        cands = [x for x, r in rows.items() if r["state"] == "TAKEN" and taken_recently(cfg, x)]  # stale = another session's
        jf = cfg["_layout"]["circle"] / "journal" / f"{today()}.md"
        for ln in (jf.read_text(encoding="utf-8", errors="replace").splitlines() if jf.is_file() else []):
            m = re.match(r"- \d\d:\d\d \[(N\d{3})\] closed\b", ln)
            if m and m.group(1) in rows and m.group(1) not in cands:
                cands.append(m.group(1))
    if len(cands) != 1:
        stale = [x for x, r in rows.items() if r["state"] == "TAKEN" and x not in cands]
        print(f"tokens {raw}: " + (f"several orders could own them ({', '.join(cands)})" if cands else
                                   (f"TAKEN order(s) {', '.join(stale)} taken over 24 h ago (another session?)" if stale else "no TAKEN order")
                                   + " and none closed today")
              + " - kept in session_log only; book them with --order Nnnn")
        return None
    msg = record(cfg, cands[0], "lead", None, n, estimate=True, src="session-end")
    print(msg)
    if "not added twice" not in msg:  # like order.py usage: the journal says the total changed (r3 #4)
        journal(cfg, cands[0], f"usage: Lead estimate {raw} booked at session end" + (msg[msg.index(" (after the close"):] if "(after the close" in msg else ""))
    return ledger_path(cfg, cands[0])


def session_end(root: Path, cfg: dict, argv: list[str]) -> int:
    """`step.py end --did "..." [--tokens -] [--blockers -] [--commit "msg"]` — gates, session_log row, CONTEXT date, commit."""
    def opt(name: str, default: str = "-") -> str:
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default
    g = gates(root, cfg)
    if not g or not g["ok"]:
        print("gates RED — fix before ending the session"); return 1
    lay = cfg["_layout"]; log = lay["state"] / "session_log.md"
    booked = lead_tokens(root, cfg, opt("--tokens"), opt("--order", ""))
    if log.is_file():
        with log.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"| {today()} | {opt('--did', '(no summary)')} | {opt('--tokens')} | {opt('--blockers')} |\n")
    ctx = lay["docs"] / "CONTEXT.md"
    if ctx.is_file():
        ctx.write_text(re.sub(r"^Updated: .*$", f"Updated: {today()}", ctx.read_text(encoding="utf-8"), count=1, flags=re.M),
                       encoding="utf-8", newline="\n")
    if "--commit" in argv:  # 0.8: only the session files + --files a,b - never `git add -A` (other agents' files stay out)
        jf = lay["circle"] / "journal" / f"{today()}.md" if booked else None  # the booking's journal line goes with it
        paths = [p for p in (log, ctx, booked, jf) if p and p.is_file()] + [root / f.strip() for f in opt("--files", "").split(",") if f.strip()]
        paths = [str(p) for p in paths if p.exists()]
        subprocess.run(["git", "add", "--", *paths], cwd=root, capture_output=True)
        c = subprocess.run(["git", "commit", "-q", "-m", opt("--commit", "session end") + "\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
                            "--", *paths], cwd=root, capture_output=True, text=True)  # pathspec: nothing staged by others rides along
        print(c.stdout.strip() or c.stderr.strip() or "committed")
        left = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip().splitlines()
        if left:
            print(f"not committed: {len(left)} path(s) — commit them inside their order (order.py done --commit) or pass --files")
    print("session closed"); return 0


def stop_runbook(root: Path, cfg: dict, rows: list[dict]) -> str:
    """What must happen before the session may end (deck review 2026-10-08: "a block carries its runbook").
    Facts only: orders closed after CONTEXT.md was written, TAKEN orders with uncommitted work, uncommitted changes."""
    from datetime import datetime
    try:  # yes/no only, after EVERY turn: a light status, not git_tree's hashing (N013 r3 #2); fail-open, project-scoped
        g = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=root, capture_output=True, text=True, timeout=8)
        dirty = g.returncode == 0 and bool(g.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        dirty = False
    taken = [r["id"] for r in rows if r["state"] == "TAKEN"]
    ctx = cfg["_layout"]["docs"] / "CONTEXT.md"
    ctx_t = ctx.stat().st_mtime if ctx.is_file() else 0.0
    closed = []
    for f in sorted((cfg["_layout"]["circle"] / "journal").glob("*.md")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"- (\d\d:\d\d) \[(N\d{3})\] closed\b", line)
            if m:
                try:
                    # the journal keeps minutes: a close in CONTEXT's own minute counts (rung 3 #5 - better an extra reminder)
                    if datetime.strptime(f"{f.stem} {m.group(1)}", "%Y-%m-%d %H:%M").timestamp() >= ctx_t - ctx_t % 60:
                        closed.append(m.group(2))
                except ValueError:
                    pass
    steps = []
    if closed:
        steps.append(f"CONTEXT.md is older than the close of {', '.join(dict.fromkeys(closed))}: rewrite it (<= 60 lines: where we are, "
                     f"what is next, open questions) - the next session starts from it")
    if taken:
        steps.append(f"order {', '.join(taken)} is TAKEN" + (" with uncommitted work: drafts in the session scratchpad die with the session - "
                     f"keep them in the deliverable or {cfg['_layout']['state'].relative_to(root).as_posix()}/staging/" if dirty else
                     ": finish it (order.py done) or hold it with a reason"))
    if dirty or closed:
        steps.append(f'end: python {KT}/step.py end --did "..." --commit "..."')
    return ("[Forge] before this session ends:\n" + "\n".join(f"  {i}. {s}" for i, s in enumerate(steps, 1))) if steps else ""


def hook_input() -> dict:
    """The hook's JSON on stdin - read only with --hook (a manual run never waits on stdin)."""
    if "--hook" not in sys.argv:
        return {}
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")  # 1.1.1: UTF-8, not the Windows locale
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def stuck_codex(cfg: dict) -> list[str]:
    """D4 (1.1.0): Codex tasks still marked `running` whose run is gone or past its deadline - `codex_exec.py status` resets them."""
    from datetime import datetime
    out = []
    for p in sorted((cfg["_layout"]["circle"] / "answers").glob("N*_plan.json")):
        try:
            tasks = json.loads(p.read_text(encoding="utf-8")).get("tasks", [])
        except (OSError, ValueError):
            continue
        cut = []
        for t in tasks:
            if t.get("status") != "running":
                continue
            try:
                late = datetime.now() > datetime.fromisoformat(t.get("deadline", ""))
            except ValueError:
                late = True
            try:
                from codex_exec import pid_alive
                gone = not pid_alive(t.get("pid", 0))
            except Exception:
                gone = False
            if late or gone:
                cut.append(t.get("id", "?"))
        if cut:
            oid = p.name.split("_")[0]
            out.append(f"  ! {oid} Codex task(s) {', '.join(cut)} were cut off - python {KT}/codex_exec.py status {oid}")
    return out


def after_compaction(cfg: dict, st: dict, rows: list[dict]) -> list[str]:
    """B4 (1.1.0): what a compacted conversation needs back - the owner's words of the current order and the handoff file."""
    out = ["  [after compaction] the summary is lossy: the facts below are the files' own"]
    cur = st.get("CURRENT_ORDER", "-")
    r = next((x for x in rows if x["id"] == cur), None)
    if r:
        md = (cfg["_layout"]["circle"] / "orders" / r["file"]).read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^## Request[^\n]*\n(.*?)(?=^## |\Z)", md, re.M | re.S)
        if m and m.group(1).strip():
            out.append(f"  {cur} request (owner's words): " + " ".join(m.group(1).split())[:600])
        out.append(f"  order file: {(cfg['_layout']['circle'] / 'orders' / r['file']).as_posix()}")
    h = cfg["_layout"]["state"] / "handoff.md"
    if h.is_file():
        text = h.read_text(encoding="utf-8", errors="replace").strip()
        out.append(f"  handoff ({h.as_posix()}, written before the compaction):")
        out += ["    " + line for line in text.splitlines()[:60]]
    out.append("  load again the skill the next step needs (forge-route before an order, forge-code for code, forge-done to close)")
    return out


def brief(root: Path, cfg: dict, st: dict, rows: list[dict], source: str = "") -> str:
    """`step.py --brief` — 15 lines for the SessionStart hook: mode, state, current order DoD, reviews, next command."""
    kv = cfg.get("kernel_version"); pkv = cfg.get("project_kernel_version")
    lines = [f"[Forge] {cfg.get('project')} · plugin {kv}{'' if not pkv or pkv == kv else f' (project made with {pkv})'} · mode {cfg.get('mode') or 'NOT SET'} · "
             f"Codex/ChatGPT {'executor + reviewer on' if str(cfg.get('gpt', '')).lower() == 'allowed' else 'off (K15)'} · "
             f"STATE {st.get('STATE')} · current {st.get('CURRENT_ORDER')} · orders " +
             " ".join(f"{s}:{n}" for s, n in ((s, sum(r['state'] == s for r in rows)) for s in cfg["order_states"]) if n)]
    cur = st.get("CURRENT_ORDER", "-")
    for r in rows:
        if r["id"] == cur or r["state"] == "OPEN":
            lines.append(f"  {r['id']} {r['state']} {r['tier']} — {r['changes'][:70]}")
            if r["id"] == cur:
                md = (cfg["_layout"]["circle"] / "orders" / r["file"]).read_text(encoding="utf-8")
                dod = re.findall(r"^- \[ \] (.+)$", md, re.M)[:4]
                lines += [f"    DoD: {d[:80]}" for d in dod]
                rev = cfg["_layout"]["circle"] / "answers" / f"{r['id']}_review.json"
                if rev.is_file():
                    v = json.loads(rev.read_text(encoding="utf-8"))
                    lines.append(f"    V1 {v.get('verdict')}: DoD {v.get('dod_met')}, lang_ok={v.get('language_ok')}; {'; '.join(v.get('findings', [])[:2])}")
    tail = []  # always shown: a long list of open orders is cut instead (0.9.3 - it used to push `next:` out of the 16 lines)
    try:  # K28 (0.7.0): Forge checks the PC it runs on and tunes itself to it — cached 6 h, never blocks the brief
        from machine import ensure as _mens, line as _mline
        tail.append("  " + _mline(_mens(root)))
    except Exception:
        pass
    try:  # K28: live load - one line, only when the machine is not ok; never fails the brief
        from machine import load_line as _mlline
        _lv = live_load()
        if _lv and _lv["verdict"] != "ok":
            tail.append("  " + _mlline(_lv))
    except Exception:
        pass
    try:  # R-37 (0.9.3): Claude tokens per order - shown only once the project has a ledger
        from usage import brief_line
        tl = brief_line(cfg, cur)
        if tl:
            tail.append(tl)
    except Exception:
        pass
    try:  # K35 (1.0): code projects - the stack to use and the Codex plan's progress
        try:
            from limits import brief_line as _limits_line
            tail.append(_limits_line(root))
        except Exception:
            pass
        if cfg.get("code"):
            plan = cfg["_layout"]["circle"] / "answers" / f"{cur}_plan.json"
            tasks = json.loads(plan.read_text(encoding="utf-8")).get("tasks", []) if plan.is_file() else []
            waiting = sum(t.get('status') == 'proposed' for t in tasks)
            prog = (f" · {cur} tasks {sum(t.get('status') == 'applied' for t in tasks)}/{len(tasks)} applied"
                    + (f", {waiting} to read" if waiting else "")) if tasks else ""
            tail.append(f"  code: read with Serena, docs from Context7 · tasks -> codex_exec.py wave, read the .patch, apply · verify: check_code.py{prog}")
        if os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"):
            tail.append("  ! CLAUDE_CODE_EFFORT_LEVEL is set - it silently overrides agents' effort (setup.py doctor)")
    except Exception:
        pass
    try:  # 1.1.0: the autonomous run and the owner's open questions; Codex runs that were cut off (D4)
        from autorun import brief_line as _run_line
        rl = _run_line(cfg, rows)
        if rl:
            tail.append(rl)
        tail += stuck_codex(cfg)
    except Exception:
        pass
    try:  # N031: the declared state against git facts after an interruption - one line, only when something does not match
        from reconcile import reconcile as _reconcile
        mism = _reconcile(root, cfg, rows=rows)
        if mism:  # the first mismatch in words and how many more: a bare count hides a new one behind the old ones
            first = mism[0] if len(mism) == 1 or len(mism[0]) <= 110 else mism[0][:107] + "..."
            tail.append(f"  reconcile: {first}{f' (+{len(mism) - 1} more)' if len(mism) > 1 else ''} - all: python {KT}/reconcile.py")
    except Exception:
        pass
    tail.append("  next: " + suggest(st.get("STATE", ""), rows, None, cfg.get("mode")))
    tail.append(f"  plugin: {PLUGIN_DIR.as_posix()} · rules: /forge-rules, python {KT}/step.py rule Kn · close: /forge-done")
    out = "\n".join(lines[:16 - len(tail)] + tail)
    if source == "compact":
        try:
            out += "\n" + "\n".join(after_compaction(cfg, st, rows))
        except Exception:
            pass
    return out[:9500]  # SessionStart context is capped at 10,000 characters


def in_project(start: Path) -> bool:
    """A Forge project above `start` (cwd only - never the plugin's own folder)."""
    return any((d / "forge.json").is_file() for d in (start, *start.parents))


def main() -> int:
    if any(x in sys.argv for x in ("--brief", "--stop-check")) and not in_project(Path.cwd()):
        return 0  # 1.1.0: the plugin is installed for every folder - its hooks stay silent outside a Forge project
    root = find_root(); cfg = load_cfg(root)
    st = read_state(root); rows = read_index(root)
    if len(sys.argv) > 2 and sys.argv[1] == "rule":
        print(rule(cfg, sys.argv[2])); return 0
    if len(sys.argv) > 1 and sys.argv[1] in ("mode", "end") and not any(x in ("-h", "--help") for x in sys.argv[2:]):
        from forge_common import refuse_in_codex
        refuse_in_codex(f"step.py {sys.argv[1]}")
    if len(sys.argv) > 2 and sys.argv[1] == "mode" and sys.argv[2] in ("cheap", "expensive"):
        set_mode(root, sys.argv[2]); return 0
    if len(sys.argv) > 1 and sys.argv[1] == "end":
        if any(x in ("-h", "--help") for x in sys.argv[2:]):  # R-42: `end --help` used to close the session
            print('usage: step.py end --did "..." [--tokens N [--order Nnnn]] [--blockers "..."] [--commit "message"] [--files a,b]'); return 0
        return session_end(root, cfg, sys.argv[2:])
    hook = hook_input()
    if "--brief" in sys.argv:
        print(brief(root, cfg, st, rows, str(hook.get("source", "")))); return 0
    if "--stop-check" in sys.argv:  # Stop hook: a runbook (K34, 0.9.2) - Stop fires after EVERY turn, so it blocks only an
        try:  # autonomous run with work left, once per state of the work (1.1.0, K20) - never the owner's dialogue
            from autorun import stop_decision
            block, run_msg = stop_decision(cfg, rows, st, hook)
        except Exception:
            block, run_msg = "", ""
        if block and "--plain" not in sys.argv:
            print(json.dumps({"decision": "block", "reason": block}, ensure_ascii=False))
            return 0
        msg = stop_runbook(root, cfg, rows)  # the reminder names exact steps
        if run_msg:
            msg = run_msg + ("\n" + msg if msg else "")
        if "--plain" in sys.argv:
            if msg:
                print(msg)
            return 0
        # plain stdout of a Stop hook goes only to the debug log (hooks reference, checked 2026-10-08 in N013: the 0.6-0.9.1
        # reminder was never seen); a JSON systemMessage is shown to the user as a warning. Shown once per new situation.
        seen = cfg["_layout"]["state"] / "cache" / "stop_runbook.txt"
        try:
            last = seen.read_text(encoding="utf-8") if seen.is_file() else ""
        except OSError:
            last = ""
        stamp = f"{today()}\n{msg}" if msg else ""  # the same unresolved situation is shown again the next day (N013 r2 #2)
        if stamp != last:
            try:
                seen.parent.mkdir(parents=True, exist_ok=True); seen.write_text(stamp, encoding="utf-8", newline="\n")
            except OSError:
                pass
            if msg:
                print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
        return 0
    g = None if "--no-gates" in sys.argv else gates(root, cfg)
    out = {"project": cfg.get("project"), "kernel": cfg.get("kernel_version"), "state": st, "orders": rows,
           "mode": cfg.get("mode"), "gates_ok": None if g is None else g["ok"], "next": suggest(st.get("STATE", ""), rows, None if g is None else g["ok"], cfg.get("mode"))}
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False)); return 0
    print(f"project {out['project']}  plugin {out['kernel']}  mode {cfg.get('mode') or 'NOT SET'}  STATE {st.get('STATE')}  current {st.get('CURRENT_ORDER')}")
    counts = {s: sum(r["state"] == s for r in rows) for s in cfg["order_states"]}
    print("orders: " + "  ".join(f"{k} {v}" for k, v in counts.items() if v))
    for r in rows:
        if r["state"] in ("OPEN", "TAKEN", "HOLD", "BLOCKED"):
            print(f"  {r['id']} {r['state']:<7} {r['tier']:<5} {r['changes'][:70]}")
    if g is not None:
        red = [k for k, v in g["gates"].items() if not v["ok"]]
        print("gates: " + ("ALL GREEN" if g["ok"] else "RED " + ", ".join(red)))
    print("next:  " + out["next"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
