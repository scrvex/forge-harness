"""Claude Code transcripts as the token meter's source (R-37, Forge 0.9.3) - read-only, stdlib, 0 tokens.

Claude Code writes every session to <claude home>/projects/<slug>/<session>.jsonl and every subagent to
<session>/subagents/agent-<id>.jsonl, next to a .meta.json holding agentType, description and toolUseId. Each assistant
request carries its usage: input, cache writes (5 m / 1 h), cache reads, output and model. One request spans several rows
(one per content block) with the same message id, and only the LAST row has the final output count. The N020 prototype
found 183 vs 36,690 output tokens over one agent's 25 requests, so the reader keeps the last row per id.

Cost units = input-token equivalents. The weights are fixed in this file - change them here only:
- input 1;
- cache write 5 m 1.25, 1 h 2;
- cache read 0.1, or 0.05 for opus-5-5 and sonnet-5-5 and 0.025 for fable (the Lead session's cache analysis,
  2026-10-11);
- output 5 (output costs 5x input).

The Lead's requests belong to an order between its order.py commands in that session:
- `take/answer/report/amend/done/close N`, and `N created` in the result of an `order.py new` call, make N the active order;
- `N CLOSED` in the result of an order.py call, or `hold/block N`, ends it, and the order active before it takes over.
A request is charged to the order active when it starts. The folder an order.py run works in (a `cd` in the same command,
else the row's cwd) decides whether it may switch the order - a run in another folder is another project's; the Lead's
requests in the window stay the order's wherever the session stands.
An agent belongs to the first open order of this project that its description or first prompt names, if it worked inside
the project. Otherwise it belongs to the order active when the Lead spawned it (`toolUseId`). Otherwise each of its
requests belongs to the parent session's order active at that moment.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

READ_MULT = (("fable", 0.025), ("mythos", 0.025), ("opus-5-5", 0.05), ("sonnet-5-5", 0.05))
READ_DEFAULT, WRITE_5M, WRITE_1H, OUTPUT = 0.1, 1.25, 2.0, 5.0
ORDER_RE = re.compile(r"(?<![A-Za-z0-9])N\d{3}(?!\d)")
CREATED = re.compile(r"(?<![A-Za-z0-9])(N\d{3}) created\b")
CLOSED = re.compile(r"(?<![A-Za-z0-9])(N\d{3}) CLOSED\b")


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def slug(path) -> str:
    """Claude Code's folder name for a working directory: every non-alphanumeric character becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _fs(p: Path) -> str:
    """Windows: the \\\\?\\ prefix opens paths over 260 characters (long project slugs)."""
    s = str(Path(p).resolve())
    return "\\\\?\\" + s if os.name == "nt" and not s.startswith("\\\\?\\") else s


def rows(p: Path):
    try:
        with open(_fs(p), encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict):
                    yield r
    except OSError:
        return


def text_of(content) -> str:
    """Plain text of a message or tool_result content (a string, or a list of {type: text, text}). Never json.dumps: its
    escapes put a letter before the id ("\\nN019 created") and the id patterns miss it (N019's own `new` was missed so)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(text_of(c.get("text", c.get("content", ""))) if isinstance(c, dict) else str(c) for c in content)
    return "" if content is None else str(content)


def _ts(s) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def read_mult(model: str) -> float:
    return next((v for k, v in READ_MULT if k in (model or "")), READ_DEFAULT)


def request_of(r: dict) -> dict | None:
    """The usage of one assistant row, or None."""
    m = r.get("message")
    if r.get("type") != "assistant" or not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
        return None
    u = m["usage"]; cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    w = int(u.get("cache_creation_input_tokens") or 0); w1 = min(w, int(cc.get("ephemeral_1h_input_tokens") or 0))
    return {"id": m.get("id") or r.get("uuid"), "t": _ts(r.get("timestamp")), "model": m.get("model") or "",
            "I": int(u.get("input_tokens") or 0), "W5": w - w1, "W1": w1, "R": int(u.get("cache_read_input_tokens") or 0),
            "O": int(u.get("output_tokens") or 0),
            "tools": sum(1 for c in m.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use")}


def requests(rs, start: int = 0) -> list[dict]:
    """Requests of rows[start:], last row per message id (final output), in order of first appearance; tool uses summed
    over the rows (each row holds its own content block)."""
    out: dict[str, dict] = {}
    for i, r in enumerate(rs):
        if i < start:
            continue
        q = request_of(r)
        if not q:
            continue
        prev = out.get(q["id"])
        if prev:
            q["t"], q["tools"] = prev["t"], prev["tools"] + q["tools"]
        out[q["id"]] = q
    return list(out.values())


def cost(reqs: list[dict]) -> dict:
    c = {"requests": len(reqs), "input": 0, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 0, "units": 0.0,
         "tool_uses": 0, "models": sorted({q["model"] for q in reqs if q["model"]})}
    for q in reqs:
        c["input"] += q["I"]; c["cache_write_5m"] += q["W5"]; c["cache_write_1h"] += q["W1"]; c["cache_read"] += q["R"]
        c["output"] += q["O"]; c["tool_uses"] += q.get("tools", 0)
        c["units"] += q["I"] + WRITE_5M * q["W5"] + WRITE_1H * q["W1"] + read_mult(q["model"]) * q["R"] + OUTPUT * q["O"]
    c["units"] = int(round(c["units"]))
    last = reqs[-1] if reqs else None
    c["context_last"] = (last["I"] + last["W5"] + last["W1"] + last["R"] + last["O"]) if last else 0  # = the Agent tool's totalTokens
    c["duration_ms"] = int((reqs[-1]["t"] - reqs[0]["t"]) * 1000) if len(reqs) > 1 else 0
    c["first_t"], c["last_t"] = (reqs[0]["t"], reqs[-1]["t"]) if reqs else (0.0, 0.0)  # the run's own time, not the scan's
    return c


def _inside(cwd, root: Path) -> bool:
    if not cwd:
        return False
    a, b = os.path.normcase(os.path.realpath(str(cwd))), os.path.normcase(os.path.realpath(str(root)))  # realpath: macOS /var -> /private/var
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


CD = re.compile(r"(?:^|[;&|(]\s*)(?:cd|Set-Location|pushd)\s+(?:-LiteralPath\s+|-Path\s+)?(\"[^\"]+\"|'[^']+'|[^\s;&|)]+)", re.I)


HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\2[ \t]*(?=\n|$)", re.S)
HERESTRING = re.compile(r"@(['\"])\r?\n.*?\r?\n\1@", re.S)


def code_of(cmd: str) -> str:
    """The command without heredoc bodies and PowerShell here-strings: they are data (a script's text, a test fixture with
    `python .../order.py take N003` inside a string) - this session's own scratch scripts opened fake windows so."""
    return HERESTRING.sub("@''@", HEREDOC.sub("<<data", cmd))


def cmd_dir(cmd: str, cwd, upto: int | None = None) -> str | None:
    """The folder an order.py run works in: the last `cd X` before it in the same command (`cd /c/ai/agi && python
    .../order.py ...` from a session filed under C:/ai - N019's own `new` ran so), else the row's cwd. `upto` = where that
    run starts (default: the first run). MSYS /c/... paths are read as C:/..."""
    if upto is None:
        m = ORDER_RUN.search(cmd)
        upto = m.start() if m else len(cmd)
    cds = [x for x in CD.finditer(cmd[:upto])]
    if not cds:
        return cwd
    target = cds[-1].group(1).strip("\"'")
    mm = re.match(r"^/([a-zA-Z])(/.*)?$", target)
    if mm:
        target = f"{mm.group(1).upper()}:{mm.group(2) or '/'}"
    target = os.path.expanduser(target)
    return target if os.path.isabs(target) else os.path.join(str(cwd or ""), target)


def _entries(folder: Path):
    """(name, is_dir, mtime) of a folder through the long-path form - pathlib's iterdir/glob/stat would fail past 260 chars."""
    try:
        with os.scandir(_fs(folder)) as it:
            for e in it:
                try:
                    yield e.name, e.is_dir(), e.stat().st_mtime
                except OSError:
                    continue
    except OSError:
        return


def session_files(root: Path, since: float = 0.0) -> list[Path]:
    """Main transcripts of sessions that may have worked in `root`: its folder, its subfolders and its ancestors (a session
    started in C:/ai that works in C:/ai/agi is filed under C--ai). A slug cannot tell `agi/old` from `agi-old`: such a
    folder is read too, and the cwd checks below keep its sessions' commands and agents out."""
    base, s = claude_home() / "projects", slug(Path(root).resolve())
    out = []
    for n, is_dir, _ in _entries(base):
        if is_dir and (n == s or n.startswith(s + "-") or s.startswith(n + "-")):
            out += [base / n / fn for fn, d2, mt in _entries(base / n) if not d2 and fn.endswith(".jsonl") and mt >= since]
    return sorted(out)


ACTIVATE = {"take", "answer", "done", "close"}  # work on the order; report/amend may touch another order in passing (r2 #2)
# what order.py prints when the verb succeeded - a pipe (`order.py take N008 | tail -5`, written so in real sessions) hides
# the exit code, so a refused take arrives without is_error; the window opens only on the success text (r3)
SUCCESS = {"take": "{oid} TAKEN", "answer": "answers/{oid}.json written", "done": "reports/{oid}.md written", "close": "{oid} CLOSED"}
# an order.py RUN in command position (line start or after ; & | ( && ||, optional VAR=x / timeout N prefixes) - not the same
# words inside a quoted string: a commit message, or a test fixture `bash("python .../order.py take N001")` in a heredoc
# (this session's own transcript opened a fake N001 window that way - r2 #A)
CMD_START = r"(?:^|[;&|(\n])\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:timeout\s+\S+\s+)?"
ORDER_RUN = re.compile(CMD_START + r"python[0-9.]*(?:\.exe)?\s+[\"']?[^\s\"';&|]*order\.py\b", re.M)
ORDER_CALL = re.compile(CMD_START + r"python[0-9.]*(?:\.exe)?\s+[\"']?[^\s\"';&|]*order\.py[\"']?\s+"
                        r"(take|answer|report|amend|done|close|hold|block|reopen)\s+[\"']?(N\d{3})(?!\d)", re.M)


def lead_windows(path: Path, root: Path) -> dict:
    """Walk one main transcript: which order owns each Lead request and each Agent spawn, and the active-order intervals.
    A verb counts only when its result carries order.py's success text (a refused `take`, even piped so the exit code is
    lost, opens nothing - r2 #A, r3; an error later in the same command does not undo a close that happened), and only the
    results of the session's own order.py runs count (`N created`, `N CLOSED`): the same words inside a file the Lead reads,
    or in test output, change nothing (r1 #2). `hold`/`block` end a window; a closed or held order hands the window back to
    the order that was active before it (a `done N016` in the middle of N020 work)."""
    stack: list[str] = []  # active order = stack[-1]
    owner, spawned, intervals, calls, closed_t, t_prev = {}, {}, [], {}, {}, 0.0
    reqs: dict[str, dict] = {}

    def end(oid: str) -> None:
        while oid in stack:
            stack.remove(oid)

    for r in rows(path):
        if r.get("isSidechain"):
            continue
        t = _ts(r.get("timestamp")) or t_prev
        before = stack[-1] if stack else None
        q = request_of(r)
        if q:
            if q["id"] not in owner:
                owner[q["id"]] = before
            prev = reqs.get(q["id"])
            if prev:
                q["t"], q["tools"] = prev["t"], prev["tools"] + q["tools"]
            reqs[q["id"]] = q
            for c in r["message"].get("content") or []:
                if not isinstance(c, dict) or c.get("type") != "tool_use":
                    continue
                if c.get("name") in ("Agent", "Task"):
                    spawned[c.get("id")] = before
                cmd = code_of(str((c.get("input") or {}).get("command") or ""))
                runs = [m for m in ORDER_RUN.finditer(cmd) if _inside(cmd_dir(cmd, r.get("cwd"), m.start()), root)]
                if runs:  # `cd A; order.py take N001; cd B; order.py take N002` - only the runs inside this project count (r3 #5)
                    calls[c.get("id")] = [(m.group(1), m.group(2)) for m in ORDER_CALL.finditer(cmd)
                                          if _inside(cmd_dir(cmd, r.get("cwd"), m.start()), root)]
        elif r.get("type") == "user":
            content = (r.get("message") or {}).get("content")
            for c in content if isinstance(content, list) else []:
                if not (isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in calls):
                    continue  # not is_error: `order.py done N --commit` may close N and then fail in git - the text decides
                text = text_of(c.get("content"))
                for verb, oid in calls[c["tool_use_id"]]:
                    if verb in ACTIVATE and SUCCESS[verb].format(oid=oid) in text:
                        end(oid); stack.append(oid)
                    elif verb in ("hold", "block") and f"{oid} -> " in text:
                        end(oid)
                for oid in CREATED.findall(text):
                    end(oid); stack.append(oid)
                for oid in CLOSED.findall(text):
                    end(oid); closed_t[oid] = t  # to the second (the journal keeps minutes)
        after = stack[-1] if stack else None
        if after != before:
            intervals.append((intervals[-1][1] if intervals else 0.0, t, before))
        t_prev = t
    intervals.append((intervals[-1][1] if intervals else 0.0, float("inf"), stack[-1] if stack else None))
    by: dict = {}
    for k, q in reqs.items():
        by.setdefault(owner.get(k), []).append(q)
    return {"by_order": by, "spawned": spawned, "intervals": intervals, "closed_t": closed_t}


def _active_at(intervals, t: float):
    for a, b, oid in intervals:
        if a <= t < b:
            return oid
    return None


def round_ends(rs: list[dict]) -> list[int]:
    """Context at the end of every round of an agent: the last request before each new prompt (a SendMessage continuation)
    and the last request overall - what the Agent tool reported for that round."""
    ends, last = [], None
    for r in rs:
        q = request_of(r)
        if q:
            last = q
            continue
        content = (r.get("message") or {}).get("content")
        prompt = r.get("type") == "user" and not (isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content))
        if prompt and last:
            ends.append(last); last = None
    if last:
        ends.append(last)
    return [q["I"] + q["W5"] + q["W1"] + q["R"] + q["O"] for q in ends]


def agents_of(session: Path, win: dict, oid: str, root: Path, known: set | None = None, closed_at: dict | None = None) -> list[dict]:
    """Agents of one session charged to `oid`, one entry per agent with its cost and the reason. The answer must not depend
    on when the scan runs (r2 #3, #4), so every rule looks at the agent's own time:
    (1) the first order of this project its description or first prompt names that was still open when the agent started
        (an order closed before is a reference) - only if the agent worked inside the project (another project may reuse
        the numbers);
    (2) else the order active when the Lead spawned it, whichever that is (an agent outliving its order stays its order's);
    (3) only when the spawn is not in this transcript: each request by the parent session's window."""
    out = []
    folder = session.with_suffix("") / "subagents"
    known = (set(known) if known is not None else {oid}) | {oid}
    closed_at = closed_at or {}
    for fn, is_dir, _ in sorted(_entries(folder)):
        if is_dir or not (fn.startswith("agent-") and fn.endswith(".jsonl")):
            continue
        f = folder / fn
        meta = {}
        try:
            with open(_fs(f.with_suffix(".meta.json")), encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass
        rs = list(rows(f))
        prompt = next((text_of((r.get("message") or {}).get("content")) for r in rs if r.get("type") == "user"), "")
        cwd = next((r.get("cwd") for r in rs if r.get("cwd")), None)
        reqs = requests(rs)
        start = reqs[0]["t"] if reqs else 0.0
        shut = {**closed_at, **win.get("closed_t", {})}  # the transcript's own close is exact to the second
        named = [x for x in dict.fromkeys(ORDER_RE.findall(f"{prompt} {meta.get('description', '')}"))
                 if x in known and not (x in shut and shut[x] <= start)]
        if named and (cwd is None or _inside(cwd, root)):
            mine, why = (reqs, f"named {named[0]}") if named[0] == oid else ([], "")
        elif meta.get("toolUseId") in win["spawned"]:
            mine, why = (reqs, "spawned while the order was active") if win["spawned"][meta["toolUseId"]] == oid else ([], "")
        else:
            mine, why = [q for q in reqs if _active_at(win["intervals"], q["t"]) == oid], "requests in the order's window"
        if mine:
            out.append({"agent_id": fn[len("agent-"):-len(".jsonl")], "who": meta.get("agentType") or "general-purpose",
                        "description": meta.get("description", ""), "why": why, "round_ends": round_ends(rs), **cost(mine)})
    return out


def _mentions(folder: Path, needle: bytes) -> bool:
    for fn, is_dir, _ in _entries(folder):
        if not is_dir and fn.startswith("agent-") and fn.endswith((".jsonl", ".meta.json")):
            try:
                with open(_fs(folder / fn), "rb") as fh:
                    if needle in fh.read():
                        return True
            except OSError:
                continue
    return False


def scan(root: Path, oid: str, since: float = 0.0, known: set | None = None, closed_at: dict | None = None) -> dict:
    """Everything the transcripts say about one order: the Lead's requests per session and the agents charged to it.
    `known` = this project's order ids, `closed_at` = when each closed order closed (journal) - see agents_of."""
    res = {"order": oid, "sessions": [], "lead": [], "agents": []}
    needle = oid.encode()
    for f in session_files(root, since):
        try:
            with open(_fs(f), "rb") as fh:
                blob = fh.read()
        except OSError:
            continue
        if needle not in blob and not _mentions(f.with_suffix("") / "subagents", needle):
            continue  # cheap filter: the id in the session or in one of its agents (a side session that only spawned agents)
        win = lead_windows(f, root)
        lead = win["by_order"].get(oid, [])
        agents = agents_of(f, win, oid, root, known, closed_at)
        if lead or agents:
            res["sessions"].append(f.stem)
            if lead:
                res["lead"].append({"session": f.stem, **cost(lead)})
            res["agents"] += agents
    return res
