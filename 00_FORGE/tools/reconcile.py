"""Reconcile the declared state with git facts after an interruption (ambatupipe: "pick-up after a break", N031).

    reconcile.py [Nnnn]    # one line per mismatch for every open order (or the given one); exit 0 = consistent, 1 = mismatches

What the project DECLARES (circle/STATE.md, the orders index, the order file, circle/answers/Nnnn_plan.json, Nnnn_codex.json,
Nnnn.json `files_changed`) is compared with what git SHOWS (the working tree against the snapshot `order.py take` stored in
Nnnn_scope.json, and the commits made since the take). Read-only: it never edits state, never touches the network, and it
does not decide anything - a mismatch is a question for the Lead, not a fix. Used by `step.py --brief` (one line, only when
there are mismatches).
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
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import find_root, git_tree, load_cfg, read_index, read_state  # noqa: E402

LIST_MAX = 4
TREE_DEADLINE = 8  # seconds for `git status` over the project; the SessionStart hook has 20 in all and a killed hook loses the whole brief
CASEFOLD = os.name == "nt"  # Windows paths are compared without case
_NO_LOCK = {"GIT_OPTIONAL_LOCKS": "0"}  # read-only means no .git/index refresh and no index.lock against a parallel `git add`/`commit`


def _json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _list(x) -> list:
    return x if isinstance(x, list) else []  # `"tasks": null` or a string in a hand-written plan is "no tasks", never a traceback


def _norm(paths) -> list[str]:
    """Posix, relative, no leading "./" and no trailing "/"; a bare string is one path, anything else none. "" = the project root."""
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, (list, tuple)):
        return []
    out = []
    for x in paths:
        s = str(x).replace("\\", "/").strip()
        if not s:
            continue
        while s.startswith("./"):
            s = s[2:]
        s = s.rstrip("/")
        out.append("" if s == "." else s)
    return out


def _key(s: str) -> str:
    return s.casefold() if CASEFOLD else s


def _covers(declared: list[str], path: str) -> bool:
    p = _key(path)
    return any(d == "" or p == _key(d) or p.startswith(_key(d) + "/") for d in declared)


def _under(base: str, paths: list[str]) -> list[str]:
    """Paths of a plan that says "relative to root": prefixed with that root."""
    if not base:
        return paths
    return [f"{base}/{p}" if p else base for p in paths]


def _short(paths: list[str]) -> str:
    return ", ".join(paths[:LIST_MAX]) + (f" (+{len(paths) - LIST_MAX})" if len(paths) > LIST_MAX else "")


def _tree(root: Path) -> dict[str, str] | None:
    """git_tree under a deadline and without taking the index lock; None when git is slow, missing or the folder is no repository."""
    box: dict = {}

    def work() -> None:
        try:
            box["tree"] = git_tree(root)
        except Exception:  # noqa: BLE001 - a visibility aid
            box["tree"] = None
    old = {k: os.environ.get(k) for k in _NO_LOCK}
    os.environ.update(_NO_LOCK)
    try:
        th = threading.Thread(target=work, daemon=True)
        th.start(); th.join(TREE_DEADLINE)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return box.get("tree")


def committed_since(root: Path, taken: str) -> set[str] | None:
    """Paths of the commits made since `taken` (a unix timestamp or "YYYY-MM-DD HH:MM", local time); None when git cannot say."""
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=off", "log", f"--since={taken}", "--relative", "--name-only", "--pretty=format:", "--", "."],
                           cwd=root, capture_output=True, timeout=10, env={**os.environ, **_NO_LOCK})
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return {ln.strip() for ln in r.stdout.decode("utf-8", "replace").splitlines() if ln.strip()}


def takes(circle: Path, oid: str) -> int:
    """How many times the order was taken (journal lines `- HH:MM [Nnnn] taken`). The snapshot belongs to the LAST take only: after
    hold -> reopen -> take the work of the first cycle is in its base, so file-level claims about it cannot be made."""
    pat = re.compile(rf"^- \d\d:\d\d \[{re.escape(oid)}\] taken\b", re.M)
    n = 0
    for f in (circle / "journal").glob("*.md"):
        try:
            n += len(pat.findall(f.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            pass
    return n


def taken_epoch(snap: dict, snap_file: Path) -> int | None:
    """When the take happened, as unix time. NOT the snapshot's mtime alone: `order.py close` rewrites that file at every attempt and
    git clone/pull resets it, so work committed before the rewrite would stop counting. Order of trust: `taken_ts` (an epoch, when
    order.py records one), else the recorded local minute `taken` ("YYYY-MM-DD HH:MM"; a floor, so work is never missed), refined by
    the file's mtime only while the snapshot was never re-checked (no `checked`) and the mtime lies within two minutes of that minute."""
    ts = snap.get("taken_ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        return int(ts)
    try:
        minute = int(time.mktime(time.strptime(str(snap.get("taken", "")), "%Y-%m-%d %H:%M")))
    except (ValueError, OverflowError):
        return None
    if "checked" not in snap:
        try:
            m = int(snap_file.stat().st_mtime)
            if 0 <= m - minute <= 120:
                return m
        except OSError:
            pass
    return minute


def plan_tasks(answers: Path, oid: str) -> list[dict]:
    """Tasks the order declares as {id, status, scope}. The plan wins; a Codex run only adds tasks the plan does not know (an
    ad-hoc `codex_exec.py run`). In answers/Nnnn_codex.json `status` is what CODEX answered (done = a proposal, nothing written yet)
    and `applied` is the time the Lead applied the patch - so a task counts as "applied" only with `applied`; of several runs of one
    task the latest wins, but an applied one stays applied. A plan `root` prefixes the plan's scopes."""
    plan = _json(answers / f"{oid}_plan.json")
    base = next(iter(_norm(plan.get("root"))), "")
    tasks: dict[str, dict] = {}
    for i, t in enumerate(_list(plan.get("tasks"))):
        if isinstance(t, dict):
            tid = str(t.get("id") or f"#{i + 1}")
            tasks[tid] = {"id": tid, "status": t.get("status"), "scope": _under(base, _norm(t.get("scope")))}
    ad_hoc: dict[str, dict] = {}
    for run in _list(_json(answers / f"{oid}_codex.json").get("runs")):
        for t in _list(run.get("tasks")) if isinstance(run, dict) else []:
            if not (isinstance(t, dict) and t.get("id")):
                continue
            tid = str(t["id"])
            if tid in tasks:
                continue
            applied = bool(t.get("applied")) or ad_hoc.get(tid, {}).get("status") == "applied"
            scope = _norm(t.get("scope")) or (_norm(list(t["proposed"])) if isinstance(t.get("proposed"), dict) else [])
            ad_hoc[tid] = {"id": tid, "status": "applied" if applied else t.get("status"), "scope": scope}
    return list(tasks.values()) + list(ad_hoc.values())


def _snapshot(answers: Path, oid: str) -> dict | None:
    """The take snapshot when it can ground file-level claims: present, readable, not skipped (too many dirty paths at take)."""
    snap = _json(answers / f"{oid}_scope.json")
    return snap if snap and not snap.get("skipped") and isinstance(snap.get("base"), dict) else None


def check_order(root: Path, cfg: dict, row: dict, tree: dict[str, str] | None) -> list[str]:
    """Mismatches of one order; each a short line starting with the order id."""
    oid, lay = row["id"], cfg["_layout"]
    circle = lay["circle"]; answers = circle / "answers"
    out: list[str] = []
    if not (circle / "orders" / row["file"]).is_file():
        out.append(f"{oid} is {row['state']} in the index but its order file {row['file']} is missing")
    tasks = plan_tasks(answers, oid)
    claimed = [t for t in tasks if t["status"] == "applied"]
    snap_file = answers / f"{oid}_scope.json"
    if row["state"] == "OPEN" and claimed and not snap_file.is_file() and not takes(circle, oid):
        # an order that was taken once and reopened (blocked executor -> reopen) keeps the applied tasks of the first cycle: not a mismatch
        out.append(f"{oid} is OPEN but {len(claimed)} plan task(s) are marked applied ({_short([t['id'] for t in claimed])}) - taken without `order.py take`?")
    if row["state"] != "TAKEN":
        return out
    snap = _snapshot(answers, oid)
    if snap is None or tree is None:
        return out  # no baseline (taken before 0.9.2, too many dirty paths, not git): no file-level claim is made
    own = [p.relative_to(root).as_posix() for p in (lay["circle"], lay["state"]) if p != root]
    base = snap["base"]
    touched = {p for p, h in tree.items() if base.get(p) != h}
    since = taken_epoch(snap, snap_file)
    committed = committed_since(root, str(since)) if since is not None else None
    complete = committed is not None  # without the commit list, "nothing changed" cannot be claimed
    touched |= committed or set()
    work = sorted(p for p in touched if not _covers(own, p))  # the order's own records do not count as work
    answer = _json(answers / f"{oid}.json")
    declared_files = _norm(answer.get("files_changed"))
    declared = list(declared_files)
    retaken = takes(circle, oid) > 1  # hold -> reopen -> take again: the first cycle's files sit in the new base
    for t in tasks:
        sc = t["scope"]
        declared += sc
        if t["status"] == "applied" and sc and complete and not retaken and not all(_covers(own, s) for s in sc) \
                and not any(_covers(sc, p) for p in work):
            out.append(f"{oid} task {t['id']} is marked applied but its files have no changes since take ({_short(sc)})")
    if complete and not work and not tasks and not answer:
        out.append(f"{oid} is TAKEN since {snap.get('taken', '?')} with no activity (no changed files, no plan, no answer)")
    if declared_files:  # only once the answer exists: before it the Lead's own edits outside the tasks' scope (docs, rules) are normal work
        extra = [p for p in work if not _covers(declared, p)]
        if extra:
            out.append(f"{oid} files changed that the answer and the tasks do not declare: {_short(extra)}")
    return out


def reconcile(root: Path, cfg: dict, only: str = "", rows: list[dict] | None = None) -> list[str]:
    """All mismatches (open orders, or the one named). Read-only; git is asked only for a TAKEN order that has a usable snapshot."""
    rows = read_index(root) if rows is None else rows
    st = read_state(root)
    open_ = [r for r in rows if r["state"] != "CLOSED" and (not only or r["id"] == only)]
    out: list[str] = []
    if only and not open_:
        return [f"{only}: not an open order in the index"]
    taken = [r for r in rows if r["state"] == "TAKEN"]
    if not only:
        if st.get("STATE") == "RUNNING" and not taken:
            out.append("STATE is RUNNING but no order is TAKEN")
        if st.get("STATE") == "READY" and taken:
            out.append(f"STATE is READY but {', '.join(r['id'] for r in taken)} is TAKEN")
        cur = st.get("CURRENT_ORDER", "-")
        if cur not in ("-", "") and not any(r["id"] == cur for r in rows):
            out.append(f"CURRENT_ORDER {cur} is not in the orders index")
    answers = cfg["_layout"]["circle"] / "answers"
    need_tree = any(r["state"] == "TAKEN" and _snapshot(answers, r["id"]) for r in open_)
    tree = _tree(root) if need_tree else None
    for r in open_:
        out += check_order(root, cfg, r, tree)
    return out


def main() -> int:
    only = next((a for a in sys.argv[1:] if not a.startswith("-")), "").upper()
    root = find_root(); cfg = load_cfg(root)
    found = reconcile(root, cfg, only)
    for line in found:
        print(line)
    if not found:
        print("reconcile: consistent")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
