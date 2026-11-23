"""The autonomous run - Forge 1.1.0 (K20; from ambatupipe's autonomous-run and continuation mod, rewritten for Forge).

The owner's "работай без меня / решай сам" is a mode of the project, not a mood of one reply: it is recorded here, the Stop
hook keeps the Lead working while takeable orders remain, and the owner's open questions wait in one list instead of
stopping the run.

- `start` only after the owner answered the start question (skill forge-autonomous: the DoD and the orders in scope in the
  question, the answer «Запустить»); the owner's words are kept verbatim;
- while the run is `running`, the Stop hook blocks a stop ONCE per state of the work (the fingerprint of the orders' states,
  STATE.md and the Codex plans): a stop on a state that did not move since the last nudge goes through, and the owner is told
  the run is waiting. It never blocks while background work is in flight, while STATE is AWAITING_OWNER / STOPPED_BY_OWNER /
  ENVIRONMENT_BROKEN, or once no order in scope is OPEN or TAKEN (the run is then `done`);
- `ask` records a question for the owner and the run goes on with the best guess written down (K34e); the brief lists the
  questions until `answer`;
- `stop` ends the run (the owner's «стоп», a blocker, or K21: two Verify rounds without progress).

Usage:
  python autorun.py start --words "<owner's words>" [--orders N004,N005]   # after «Запустить»
  python autorun.py status | stop [--why "..."] | ask "<question>" [--guess "..."] | answer <n> "<owner's answer>"
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import find_root, load_cfg, read_index, read_state  # noqa: E402

KT = Path(__file__).resolve().parent.as_posix()
STOPPED_STATES = ("AWAITING_OWNER", "STOPPED_BY_OWNER", "ENVIRONMENT_BROKEN")


def run_file(cfg: dict) -> Path:
    return cfg["_layout"]["state"] / "run.json"


def load(cfg: dict) -> dict:
    try:
        return json.loads(run_file(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(cfg: dict, run: dict) -> None:
    f = run_file(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def in_scope(run: dict, rows: list[dict]) -> list[dict]:
    scope = run.get("orders") or []
    return [r for r in rows if not scope or r["id"] in scope]


def takeable(run: dict, rows: list[dict]) -> list[dict]:
    return [r for r in in_scope(run, rows) if r["state"] in ("OPEN", "TAKEN")]


def fingerprint(cfg: dict, rows: list[dict], st: dict) -> str:
    """What 'the work moved' means: an order changed state, STATE.md changed, or a Codex task changed status."""
    parts = [f"{r['id']}:{r['state']}" for r in rows] + [f"{k}={v}" for k, v in sorted(st.items()) if k != "UPDATED"]
    for p in sorted((cfg["_layout"]["circle"] / "answers").glob("*_plan.json")):
        try:
            tasks = json.loads(p.read_text(encoding="utf-8")).get("tasks", [])
        except (OSError, ValueError):
            continue
        parts += [f"{p.stem}/{t.get('id')}:{t.get('status', 'pending')}" for t in tasks]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def stop_decision(cfg: dict, rows: list[dict], st: dict, hook: dict) -> tuple[str, str]:
    """(block_reason, owner_message) for the Stop hook; ('', '') when the run has nothing to say."""
    run = load(cfg)
    if run.get("status") != "running":
        return "", ""
    if hook.get("background_tasks"):
        return "", ""  # background work wakes the session itself
    state = st.get("STATE", "")
    if state in STOPPED_STATES:
        return "", f"[Forge] autonomous run waits: STATE {state} - only the owner moves it on"
    left = takeable(run, rows)
    if not left:
        run.update(status="done", ended=now(), why="no OPEN or TAKEN order left in scope")
        save(cfg, run)
        closed = [r["id"] for r in in_scope(run, rows) if r["state"] == "CLOSED"]
        held = [r["id"] for r in in_scope(run, rows) if r["state"] in ("HOLD", "BLOCKED")]
        q = [x for x in run.get("questions", []) if not x.get("answer")]
        return "", (f"[Forge] autonomous run done: closed {', '.join(closed) or '-'}; on hold {', '.join(held) or '-'}; "
                    f"questions for you {len(q)}" + (f" (python {KT}/autorun.py status)" if q else ""))
    fp = fingerprint(cfg, rows, st)
    if run.get("nudged") == fp:
        return "", ("[Forge] autonomous run paused: nothing moved since the last nudge - "
                    f"{', '.join(r['id'] for r in left)} still open; tell the Lead how to go on, or «стоп»")
    run["nudged"] = fp
    run["nudges"] = int(run.get("nudges", 0)) + 1
    save(cfg, run)
    r = next((x for x in left if x["state"] == "TAKEN"), left[0])
    nxt = (f"python {KT}/order.py done {r['id']} ..." if r["state"] == "TAKEN" else f"python {KT}/order.py take {r['id']}")
    return (f"[Forge] autonomous run (owner: «{run.get('words', '')[:120]}»): {r['id']} is {r['state']} - keep going: {nxt}. "
            f"A question for the owner does not stop the run: `autorun.py ask \"...\" --guess \"...\"` and go on with the guess. "
            f"Stop only on a real blocker: `order.py hold {r['id']} --why \"...\"` or `autorun.py stop --why \"...\"`."), ""


def brief_line(cfg: dict, rows: list[dict]) -> str:
    run = load(cfg)
    if not run:
        return ""
    q = [x for x in run.get("questions", []) if not x.get("answer")]
    if run.get("status") == "running":
        left = takeable(run, rows)
        return (f"  autonomous run since {run.get('started', '?')[:16]} (owner: «{run.get('words', '')[:60]}»): "
                f"{len(left)} order(s) left" + (f" · questions for the owner: {len(q)}" if q else ""))
    if q:
        return f"  questions for the owner from the last autonomous run: {len(q)} - python {KT}/autorun.py status"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--words", required=True); s.add_argument("--orders", default="")
    sub.add_parser("status")
    p = sub.add_parser("stop"); p.add_argument("--why", default="the owner's stop")
    q = sub.add_parser("ask"); q.add_argument("question"); q.add_argument("--guess", default="")
    a = sub.add_parser("answer"); a.add_argument("n", type=int); a.add_argument("text")
    args = ap.parse_args()
    root = find_root(); cfg = load_cfg(root)
    rows = read_index(root)
    run = load(cfg)
    if args.cmd == "start":
        from forge_common import refuse_in_codex
        refuse_in_codex("autorun.py start")
        orders = [x.strip() for x in args.orders.split(",") if x.strip()]
        unknown = [x for x in orders if x not in {r["id"] for r in rows}]
        if unknown:
            raise SystemExit(f"no such order(s): {', '.join(unknown)}")
        open_q = [x for x in run.get("questions", []) if not x.get("answer")]  # unanswered questions carry over
        run = {"status": "running", "started": now(), "words": args.words, "orders": orders, "questions": open_q}
        save(cfg, run)
        left = takeable(run, rows)
        print(f"autonomous run started: {len(left)} order(s) in scope ({', '.join(r['id'] for r in left) or 'none open'}); "
              f"the Stop hook keeps the work going while they are open")
        return 0
    if not run:
        print("no autonomous run in this project")
        return 0
    if args.cmd == "stop":
        if run.get("status") == "running":
            run.update(status="stopped", ended=now(), why=args.why)
            save(cfg, run)
        print(f"autonomous run {run.get('status')}: {run.get('why', '')}")
        return 0
    if args.cmd == "ask":
        run.setdefault("questions", []).append({"q": args.question, "guess": args.guess, "when": now(), "answer": ""})
        save(cfg, run)
        print(f"question {len(run['questions'])} recorded - go on with the guess" + (f": {args.guess}" if args.guess else ""))
        return 0
    if args.cmd == "answer":
        qs = run.get("questions", [])
        if not 1 <= args.n <= len(qs):
            raise SystemExit(f"no question {args.n} (there are {len(qs)})")
        qs[args.n - 1].update(answer=args.text, answered=now())
        save(cfg, run)
        print(f"question {args.n} answered")
        return 0
    st = read_state(root)
    print(f"run: {run.get('status')} since {run.get('started', '?')} - owner: «{run.get('words', '')}»"
          + (f" - ended {run.get('ended')}: {run.get('why', '')}" if run.get("ended") else ""))
    print(f"orders in scope: " + ", ".join(f"{r['id']} {r['state']}" for r in in_scope(run, rows)) + f" · STATE {st.get('STATE')}")
    for i, x in enumerate(run.get("questions", []), 1):
        print(f"  Q{i} {'answered' if x.get('answer') else 'OPEN'}: {x['q']}" + (f" | guess: {x['guess']}" if x.get("guess") else "")
              + (f" | answer: {x['answer']}" if x.get("answer") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
