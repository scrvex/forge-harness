"""Claude token accounting per order (R-37, Forge 0.9.3) - 0 tokens, stdlib only.

    usage.py --hook                     # PostToolUse(Agent) / SubagentStop hook: books a finished agent's tokens (stdin JSON)
    usage.py add N019 --who verifier2 --usage "<usage>total_tokens: 40685 tool_uses: 8 duration_ms: 135916</usage>"
    usage.py add N019 --who lead --tokens 150k --estimate      # the Lead's own figure (an estimate, kept apart)
    usage.py show N019                  # runs, total and estimate ratio of one order
    usage.py scan N019 [--apply]        # what the Claude Code transcripts say about N019 (close runs it with --apply)
    usage.py report [--days 30] [--md file.md]                 # per-order table for forge-audit ("Скорость и токены")

Source of truth (N020): the transcripts Claude Code writes (tools/transcripts.py). `close` scans the sessions that
worked on the order, then books two kinds of runs:
- the Lead's requests in the order's window, and every agent charged to it, as `src: transcript` runs;
- each with its cost in units (input-token equivalents, from exact per-request usage).
Hook, manual and estimated runs of the same agent or of the Lead are then marked `superseded`.
Without transcripts (another client, a deleted history), the hook and the manual figures below remain the record.

Ledger: <circle>/answers/N019_usage.json, committed with the order. Tokens of an agent = what Claude Code reports for it
(`subagent_tokens` / `totalTokens`: the agent's context at its end) - the metric of the 2026-10-05 audit. The Lead's own
figure is an estimate (`step.py end --tokens`) and is summed separately. Foreground agents are booked exactly from the
Agent result; background ones from their transcript at SubagentStop (`approx`, ~5% low - a pasted <usage> block replaces
it). Booking (several sessions may share the folder): the first open order the prompt names (an id or an order file
name), else the order this session's agents named last, else the current order only if it is the one TAKEN order and was
taken in the last 24 h, else <state>/cache/usage_unassigned.jsonl (counted in the brief and the audit, never guessed).
The hook never fails and never prints: accounting must not cost the session anything.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import load_cfg, now_hm, read_index, read_state, today  # noqa: E402
import transcripts  # noqa: E402  (N020: the meter's source)

ORDER_RE = re.compile(r"(?<![A-Za-z0-9])N\d{3}(?!\d)")  # also inside a file name: orders/N019_forge_...md
FRESH_TAKE_HOURS = 24  # an unnamed agent falls back to the current order only if it was taken this recently
CONTEXT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
BLOCK = {"tokens": r"(?:subagent_tokens|total_tokens|totaltokens|(?<![a-z_])tokens)",
         "tool_uses": r"(?:tool_uses|totaltoolusecount)", "duration_ms": r"(?:duration_ms|totaldurationms)"}
UNASSIGNED_CAP = 200   # lines kept in the unassigned log (a cache, not a record; stays under gate_size's 64 KB)
PENDING_DAYS = 3       # launched background agents older than this are forgotten


@contextmanager
def _lock(cfg: dict, name: str, wait: float = 4.0):
    """Parallel agents finish together and their hooks run at once: one writer per file (a lock in <state>/cache). After
    `wait` seconds (two nested locks, agents map then ledger, stay under the hook's 10 s timeout) the write goes ahead without it - a lost race is better than a lost run;
    a lock older than 15 s is stale. Windows answers PermissionError while the previous holder's lock file is being deleted:
    that is "busy" too (N020: one run of 12 lost under load)."""
    lk = _cache(cfg) / f"{name}.lock"
    end, fd = time.time() + wait, None
    while True:
        try:
            fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            try:
                if time.time() - lk.stat().st_mtime > 15:
                    lk.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() > end:
                break
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lk.unlink(missing_ok=True)
            except OSError:
                pass


# ---------- numbers ----------
def parse_tokens(value) -> int | None:
    """'150k' / '1.2M' / '1,2M' / '150 000' / '150,000' / '~40k' / 40685 -> int; None for '-', '' or anything else."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    s = str(value).strip().replace(" ", " ").lstrip("~≈").strip()
    m = re.fullmatch(r"(\d[\d.,]*)\s*([kKmMкКмМ]?)", s.replace(" ", "").replace("_", ""))
    if not m:
        return None
    num, suf = m.group(1), m.group(2).lower()
    if suf:
        try:
            return int(round(float(num.replace(",", ".")) * (1_000 if suf in "kк" else 1_000_000)))
        except ValueError:
            return None
    if num.isdigit():
        return int(num)
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", num):  # 150,000 or 150.000 - thousands separators only
        return int(re.sub(r"[.,]", "", num))
    return None  # '1.5' without a unit is not a token count


def parse_block(text: str) -> dict:
    """The <usage> block of an Agent result (or the tool's JSON) -> {tokens, tool_uses, duration_ms} (missing keys absent)."""
    out = {}
    for key, pat in BLOCK.items():
        m = re.search(pat + r"\"?\s*[:=]\s*\"?(\d[\d,_ ]*)", text or "", re.I)
        if m:
            out[key] = int(re.sub(r"[,_ ]", "", m.group(1)))
    return out


def fmt(n: int | None) -> str:
    if n is None:
        return "-"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(n)


# ---------- ledger ----------
def ledger_path(cfg: dict, oid: str) -> Path:
    return cfg["_layout"]["circle"] / "answers" / f"{oid}_usage.json"


def load(cfg: dict, oid: str) -> dict:
    f = ledger_path(cfg, oid)
    if f.is_file():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("runs"), list):
                return d
        except (json.JSONDecodeError, OSError):
            pass
    return {"order": oid, "runs": []}


def save(cfg: dict, oid: str, d: dict) -> None:
    f = ledger_path(cfg, oid)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def make_run(who: str, tokens: int, kind: str = "measured", src: str = "manual", tool_uses=None, duration_ms=None, **extra) -> dict:
    run = {"when": f"{today()} {now_hm()}", "who": who or "agent", "kind": kind, "tokens": int(tokens), "src": src}
    if tool_uses is not None:
        run["tool_uses"] = int(tool_uses)
    if duration_ms is not None:
        run["duration_ms"] = int(duration_ms)
    run.update({k: v for k, v in extra.items() if v not in (None, "")})
    return run


APPROX_MATCH = 0.15  # assumption from one observation (N015 verifier2: transcript 5.3% under the tool's figure) - widen if
                     # a pasted exact block ever fails to replace its approximate run


def add_run(cfg: dict, oid: str, run: dict) -> str:
    """Append a run unless it is already there: the same hook key; a hand-entered measured run with the numbers of a run
    already booked (the Lead pasted the usage block of an agent the hook had booked); the same Lead estimate again (a retried
    `done` or `step.py end`). Two hook runs with different keys are two agents even when their numbers are equal."""
    with _lock(cfg, f"{oid}_usage.json"):
        return _add_run(cfg, oid, run)


def same_agent(hook_who: str, hand_who: str) -> bool:
    """'verifier2' typed by the Lead and 'forge:verifier2' booked by the hook name one agent. The Lead's default --who
    'agent' (no name given) matches any; the hook side always carries the agent's real type (r3 #2)."""
    a, b = (hook_who or "").lower(), (hand_who or "agent").lower()
    return b == "agent" or a == b or a.split(":")[-1] == b.split(":")[-1]


def lead_who(who: str) -> str:
    """'lead', 'lead:claude' - one actor, the Lead (r3 #3: done --tokens and step.py end used different spellings)."""
    return "lead" if (who or "").lower().startswith("lead") else (who or "")


def _add_run(cfg: dict, oid: str, run: dict) -> str:
    d = load(cfg, oid)
    same_tools = lambda r: run.get("tool_uses") is None or r.get("tool_uses") in (None, run.get("tool_uses"))  # noqa: E731
    for r in d["runs"]:
        if run.get("key") and r.get("key") == run["key"]:
            return f"{oid}: already recorded ({run['key']})"
        if run["kind"] == "measured" and r.get("kind") == "measured" and r.get("tokens") == run["tokens"] and same_tools(r):
            # a hand-pasted block of an agent the hook booked (one side has a hook key), or the same block pasted twice (no
            # keys, tool uses given and equal); two bare `--tokens 5k` for two agents stay two runs (rung 3 #3)
            hook_vs_hand = bool(run.get("key")) != bool(r.get("key")) and (
                same_agent(r.get("who"), run["who"]) if r.get("key") else same_agent(run["who"], r.get("who")))
            pasted_twice = not run.get("key") and not r.get("key") and run.get("tool_uses") is not None and r.get("tool_uses") is not None
            if hook_vs_hand or pasted_twice:
                return f"{oid}: already recorded ({fmt(run['tokens'])} by {r.get('who')}, {r.get('src')}) - not added twice"
        if (run["kind"] == "estimate" and r.get("kind") == "estimate" and lead_who(r.get("who")) == lead_who(run["who"]) and r.get("tokens") == run["tokens"]
                and r.get("when", "")[:10] == run.get("when", "")[:10]):  # a retried command the same day; another day = another session
            return f"{oid}: this estimate ({fmt(run['tokens'])} by {run['who']}) is already recorded today - not added twice"
    if not run.get("key"):  # typed after the scan: the transcripts already measured this agent / this Lead day
        meas = [r for r in d["runs"] if r.get("src") == "transcript" and not r.get("superseded")]
        agent = next((r for r in meas if run["kind"] == "measured" and lead_who(r.get("who")) != "lead"
                      and same_agent(r.get("who"), run["who"]) and covers(r, run["tokens"])), None)
        lead = next((r for r in meas if run["kind"] == "estimate" and lead_who(r.get("who")) == "lead"
                     and run.get("when", "")[:10] in (r.get("days") or [])), None)
        if agent or lead:
            run["superseded"] = "transcript"
            d["runs"].append(run)
            save(cfg, oid, d)
            return (f"{oid}: {fmt(run['tokens'])} by {run['who']} is already measured from the transcripts "
                    f"({(agent or lead).get('who')}) - kept as superseded, the total does not change")
    msg = None
    if run["kind"] == "measured" and not run.get("approx") and not run.get("key"):
        # a background agent booked from its transcript reads ~5% low (the last message's output is not fully written there);
        # its exact <usage> block, pasted later, replaces the NEAREST such figure of the same agent instead of counting it twice
        near = [r for r in d["runs"] if r.get("approx") and same_tools(r) and same_agent(r.get("who"), run["who"])
                and abs(r["tokens"] - run["tokens"]) <= APPROX_MATCH * max(run["tokens"], 1)]
        if run["who"].lower() == "agent" and len(near) > 1:  # no name and several candidates: never guess which one (r3 #2)
            near = []
        if near:
            r = min(near, key=lambda x: abs(x["tokens"] - run["tokens"]))
            old = r["tokens"]
            r.update(tokens=run["tokens"], src=f"{r.get('src')}+{run['src']}")
            r.pop("approx", None)
            if run.get("duration_ms") is not None:
                r["duration_ms"] = run["duration_ms"]
            msg = f"{oid}: exact figure {fmt(run['tokens'])} replaces the hook's approximate {fmt(old)} ({r.get('who')})"
    if msg is None:
        d["runs"].append(run)
        msg = f"{oid}: +{fmt(run['tokens'])} tokens ({run['who']}, {run['kind']}) - total {fmt(summary(d)['total'])}"
    if "closed" in d:  # booked after the close (e.g. `step.py end --tokens`): the snapshot follows, marked as updated (rung 3 #6)
        s = summary(d, estimate_of(cfg, oid))
        d["closed"].update({k: s[k] for k in SNAPSHOT}, updated=f"{today()} {now_hm()}")
        msg += f" (after the close: total now {fmt(s['total'])})"
    save(cfg, oid, d)
    return msg


def summary(d: dict, estimate: int | None = None) -> dict:
    """Totals of the runs that count (superseded ones do not). `tokens` = the reported metric (context at the end); `units`
    = spend from the transcripts. The estimate is compared with units once the Lead's own requests are measured (N020),
    else with tokens."""
    runs = [r for r in d.get("runs", []) if isinstance(r.get("tokens"), int) and not r.get("superseded")]
    lead_m = [r for r in runs if r.get("kind") != "estimate" and lead_who(r.get("who")) == "lead"]
    agents = [r for r in runs if r.get("kind") != "estimate" and lead_who(r.get("who")) != "lead"]
    lead_e = [r for r in runs if r.get("kind") == "estimate"]
    by_who: dict[str, int] = {}
    for r in agents:
        by_who[r.get("who", "agent")] = by_who.get(r.get("who", "agent"), 0) + r["tokens"]
    s = {"runs": len(agents), "agents_tokens": sum(r["tokens"] for r in agents), "lead_estimate": sum(r["tokens"] for r in lead_e),
         "lead_measured": sum(r["tokens"] for r in lead_m), "duration_ms": sum(r.get("duration_ms") or 0 for r in agents),
         "by_who": dict(sorted(by_who.items(), key=lambda x: -x[1])),
         "units": sum(int(r.get("units") or 0) for r in runs), "lead_units": sum(int(r.get("units") or 0) for r in lead_m),
         "agents_units": sum(int(r.get("units") or 0) for r in agents), "lead_requests": sum(int(r.get("requests") or 0) for r in lead_m)}
    s["total"] = s["agents_tokens"] + s["lead_measured"] + s["lead_estimate"]  # superseded estimates are already out
    s["units_complete"] = (bool(lead_m) and all(r.get("units") is not None for r in agents)
                           and not s["lead_estimate"])  # an unmeasured Lead share left -> the units are a part, not the order
    s["estimate"] = estimate
    base = s["units"] if s["units_complete"] else s["total"]
    s["ratio"] = round(base / estimate, 2) if estimate and base else None
    s["ratio_of"] = "units" if s["units_complete"] else "tokens"
    return s


SNAPSHOT = ("total", "agents_tokens", "lead_estimate", "lead_measured", "runs", "units", "lead_units", "agents_units", "estimate", "ratio", "ratio_of")


def line(oid: str, s: dict) -> str:
    if s.get("units_complete"):
        out = (f"{oid} spend {fmt(s['units'])} units (Lead {fmt(s['lead_units'])} in {s['lead_requests']} requests + agents "
               f"{fmt(s['agents_units'])} in {s['runs']} run(s); from the transcripts) · reported tokens {fmt(s['total'])}")
    else:
        parts = [f"agents {fmt(s['agents_tokens'])} in {s['runs']} run(s)"] if s["runs"] else []
        if s.get("lead_measured"):
            parts.append(f"Lead {fmt(s['lead_measured'])}")
        if s["lead_estimate"]:  # typed and not covered by the transcripts (another day or machine)
            parts.append(f"Lead est. {fmt(s['lead_estimate'])}")
        out = f"{oid} tokens {fmt(s['total'])}" + (f" ({' + '.join(parts)})" if parts else "")
        if s.get("units"):
            out += f" · units {fmt(s['units'])} (part of the order)"
    if s["estimate"]:
        out += f" vs estimate {fmt(s['estimate'])}" + (f" = x{s['ratio']:.2f} ({s.get('ratio_of', 'tokens')})" if s["ratio"] else "")
        if s["ratio"] and (s["ratio"] > 2 or s["ratio"] < 0.5):
            out += " - estimate off by more than 2x: note why in the audit"
    if s["runs"] and not s["lead_estimate"] and not s.get("lead_measured"):
        out += " - the Lead's own share is not recorded (no transcript found; order.py usage N --who lead --tokens <your context>)"
    return out


def order_md(cfg: dict, oid: str) -> str:
    row = next((r for r in read_index(cfg["_root"]) if r["id"] == oid), None)
    f = cfg["_layout"]["circle"] / "orders" / row["file"] if row else None
    return f.read_text(encoding="utf-8", errors="replace") if f and f.is_file() else ""


def estimate_of(cfg: dict, oid: str, md: str | None = None) -> int | None:
    """The order's token estimate: the last `token estimate := X` amendment, else the header `**Token estimate:** X`,
    else `estimate_tokens` of a swarm manifest <circle>/plans/*.json whose `cycle` is this order."""
    md = order_md(cfg, oid) if md is None else md
    amended = re.findall(r"token estimate := (\S+)", md)
    if amended and parse_tokens(amended[-1].strip("`")) is not None:
        return parse_tokens(amended[-1].strip("`"))
    m = re.search(r"\*\*Token estimate:\*\*\s*([^\s*]+)", md)
    if m and parse_tokens(m.group(1)) is not None:
        return parse_tokens(m.group(1))
    plans = cfg["_layout"]["circle"] / "plans"
    for f in sorted(plans.glob("*.json")) if plans.is_dir() else []:
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(p, dict) and p.get("cycle") == oid and parse_tokens(p.get("estimate_tokens")) is not None:
            return parse_tokens(p.get("estimate_tokens"))
    return None


def journal(cfg: dict, oid: str, text: str) -> Path:
    """One line in the circle journal (the same format order.py writes) - for bookings made outside order.py."""
    f = cfg["_layout"]["circle"] / "journal" / f"{today()}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.is_file():
        f.write_text(f"# Journal {today()}\n\n", encoding="utf-8", newline="\n")
    with f.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"- {now_hm()} [{oid}] {text}\n")
    return f


def closed_times(cfg: dict) -> dict:
    """{order: when it last closed} from the journal - the order being scanned included: at its close the line is already
    written (agents that start later are the next order's), and a re-scan long after must give the same answer."""
    out = {}
    for f in sorted((cfg["_layout"]["circle"] / "journal").glob("*.md")):
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"- (\d\d:\d\d) \[(N\d{3})\] closed\b", ln)
            if m:
                try:
                    out[m.group(2)] = datetime.strptime(f"{f.stem} {m.group(1)}", "%Y-%m-%d %H:%M").timestamp()
                except ValueError:
                    pass
    return out


def created_at(cfg: dict, oid: str) -> float:
    """When the order was created (its `new order` journal line); 0 when unknown."""
    for f in sorted((cfg["_layout"]["circle"] / "journal").glob("*.md")):
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"- (\d\d:\d\d) \[(N\d{3})\] new order\b", ln)
            if m and m.group(2) == oid:
                try:
                    return datetime.strptime(f"{f.stem} {m.group(1)}", "%Y-%m-%d %H:%M").timestamp()
                except ValueError:
                    return 0.0
    return 0.0


def _usage_fields(c: dict) -> dict:
    return {"units": c["units"], "requests": c["requests"], "models": c["models"],
            "usage": {k: c[k] for k in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")}}


def scan_apply(cfg: dict, oid: str, apply: bool = True) -> dict:
    """N020: book what the transcripts say about the order - the Lead's requests in its window (one run per session) and every
    agent charged to it (one run per agent, all its continuations) - and mark the runs they replace as superseded:
    hook runs of the same agent, hand-entered figures of it, the Lead's estimates. Re-running replaces the previous scan."""
    since = created_at(cfg, oid)
    known = {r["id"] for r in read_index(cfg["_root"])} | {oid}
    res = transcripts.scan(cfg["_root"], oid, since=since - 3600 if since else 0.0, known=known, closed_at=closed_times(cfg))
    if apply and (res["lead"] or res["agents"]):
        with _lock(cfg, f"{oid}_usage.json"):
            save(cfg, oid, merge_scan(load(cfg, oid), res))
    return res


def covers(agent: dict, tokens: int) -> bool:
    """A hand-entered figure is one ROUND of this transcript agent: the context at the end of a round (what the Agent tool
    reported for it) is that figure, or up to APPROX_MATCH below it (the round's last output is not fully in the transcript),
    never above it by more than 2%. Mid-round contexts do not count - a long agent spans almost any figure (N020 r1 #4)."""
    return any(tokens * (1 - APPROX_MATCH) <= c <= tokens * 1.02 for c in agent.get("round_ends") or [agent.get("context_last", 0)])


def _at(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else f"{today()} {now_hm()}"


def merge_scan(d: dict, res: dict) -> dict:
    """The ledger after a scan (pure): previous transcript runs replaced, the runs they cover marked superseded."""
    new = [make_run("lead", c["context_last"], src="transcript", key=f"lead:{c['session']}", tool_uses=c["tool_uses"],
                    duration_ms=c["duration_ms"], session=c["session"], **_usage_fields(c)) for c in res["lead"]]
    new += [make_run(a["who"], a["context_last"], src="transcript", key=f"agent:{a['agent_id']}", tool_uses=a["tool_uses"],
                     duration_ms=a["duration_ms"], agent_id=a["agent_id"], why=a["why"], **_usage_fields(a)) for a in res["agents"]]
    for run, c in zip(new, res["lead"] + res["agents"]):
        run["when"] = _at(c.get("last_t"))  # the run's own time: a later re-scan never moves old spend into this week
        if c.get("round_ends"):
            run["round_ends"] = c["round_ends"]  # a figure typed later is matched against them (rung 3 #2)
    ids = {a["agent_id"] for a in res["agents"]}
    lead_days = set()  # the days of the measured Lead requests, and the day after (a session that ends past midnight)
    for run, c in zip(new, res["lead"]):
        if c.get("first_t") and c.get("last_t"):
            day, days = datetime.fromtimestamp(c["first_t"]).date(), []
            while day <= datetime.fromtimestamp(c["last_t"]).date() + timedelta(days=1):
                days.append(day.isoformat()); day += timedelta(days=1)
            run["days"] = days; lead_days.update(days)
    d = {**d, "runs": [dict(r) for r in d["runs"] if r.get("src") != "transcript"]}
    for r in d["runs"]:
        r.pop("superseded", None)
        cands = [a for a in res["agents"] if same_agent(a["who"], r.get("who")) and covers(a, r["tokens"])]
        unnamed = (r.get("who") or "agent").lower() == "agent"
        hand_copy = r.get("kind") == "measured" and not r.get("key") and (len(cands) == 1 if unnamed else bool(cands))
        # a typed Lead estimate gives way to Lead requests measured that day or the day before (a session ending past midnight);
        # an estimate of another machine's session typed in that range is dropped too - a small undercount, accepted
        lead_copy = r.get("kind") == "estimate" and lead_who(r.get("who")) == "lead" and r.get("when", "")[:10] in lead_days
        if r.get("agent_id") in ids or hand_copy or lead_copy:
            r["superseded"] = "transcript"
    d["runs"] += new
    d["scanned"] = {"when": f"{today()} {now_hm()}", "sessions": res["sessions"]}
    return d


def close_summary(cfg: dict, oid: str) -> str | None:
    """Called by `order.py close`: scan the transcripts, snapshot into the ledger, one line for the journal; None when
    nothing to say."""
    est = estimate_of(cfg, oid)
    note = ""
    try:
        scan_apply(cfg, oid)
    except Exception as e:  # noqa: BLE001 - the transcripts are an aid; their absence or a format change never blocks a close
        note = f" (transcript scan skipped: {type(e).__name__}: {e})"
    with _lock(cfg, f"{oid}_usage.json"):  # a hook may be booking an agent at this very moment
        d = load(cfg, oid)
        s = summary(d, est)
        if d["runs"]:
            d["closed"] = {"when": f"{today()} {now_hm()}", **{k: s[k] for k in SNAPSHOT}}
            save(cfg, oid, d)
    pend = [a for a, m in pending(cfg).items() if m.get("order") == oid and m.get("pending")]
    if not d["runs"] and not s["estimate"]:
        return None
    msg = (line(oid, s) if d["runs"] else f"{oid} tokens: none recorded (estimate {fmt(s['estimate'])})") + note
    if pend:
        msg += f"; {len(pend)} background agent(s) launched for {oid} never reported - paste their usage: order.py usage {oid} --who <agent> --usage \"...\""
    return msg


# ---------- hook ----------
def project_root(start: Path) -> Path | None:
    """The Forge project of the session - from the hook's cwd only (never from the plugin's own location)."""
    for d in [start, *start.parents]:
        if (d / "forge.json").is_file():
            return d
    return None


def taken_recently(cfg: dict, oid: str, hours: float = FRESH_TAKE_HOURS) -> bool:
    """The order's last `taken` journal line is younger than `hours` - an order TAKEN days ago is likely another session's."""
    best = 0.0
    for f in (cfg["_layout"]["circle"] / "journal").glob("*.md"):
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"- (\d\d:\d\d) \[(N\d{3})\] taken\b", ln)
            if m and m.group(2) == oid:
                try:
                    best = max(best, datetime.strptime(f"{f.stem} {m.group(1)}", "%Y-%m-%d %H:%M").timestamp())
                except ValueError:
                    pass
    return datetime.now().timestamp() - best <= hours * 3600


def resolve_order(cfg: dict, text: str, last_named: str | None = None) -> tuple[str | None, str]:
    """Which order pays for an agent. Several sessions may share the folder, so the global current order is the last
    resort: (1) the first open order the prompt names (an id or an order file name); (2) the order this session's agents
    named last (`last_named`); (3) the current order, only when it is the one TAKEN order and was taken in the last
    FRESH_TAKE_HOURS; else none - the run goes to the unassigned log, never to a guessed order."""
    rows = {r["id"]: r for r in read_index(cfg["_root"])}
    named = [x for x in dict.fromkeys(ORDER_RE.findall(text or "")) if x in rows]
    live = [x for x in named if rows[x]["state"] != "CLOSED"]
    if live:
        return live[0], "named in the prompt"
    if named:  # an audit or a replay reading closed orders is not those orders' cost
        return None, f"prompt names only closed order(s) {', '.join(named)}"
    if last_named in rows and rows[last_named]["state"] != "CLOSED":
        return last_named, "named earlier by this session's agents"
    cur = read_state(cfg["_root"]).get("CURRENT_ORDER", "-")
    taken = [r["id"] for r in rows.values() if r["state"] == "TAKEN"]
    if taken == [cur] and taken_recently(cfg, cur):
        return cur, "current order"
    return None, ("no order named; " + (f"several orders TAKEN ({', '.join(taken)})" if len(taken) > 1 else
                                         f"current order {cur} was taken over {FRESH_TAKE_HOURS:g} h ago" if taken == [cur] else
                                         f"TAKEN order {taken[0]} is not the current one ({cur})" if taken else "none TAKEN"))


def _cache(cfg: dict) -> Path:
    c = cfg["_layout"]["state"] / "cache"
    c.mkdir(parents=True, exist_ok=True)
    return c


def pending(cfg: dict) -> dict:
    f = cfg["_layout"]["state"] / "cache" / "usage_agents.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_pending(cfg: dict, data: dict) -> None:
    cutoff = datetime.now().timestamp() - PENDING_DAYS * 86400
    data = {k: v for k, v in data.items() if v.get("at", 0) >= cutoff}
    (_cache(cfg) / "usage_agents.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def book(cfg: dict, oid: str | None, run: dict, why: str) -> None:
    if oid and next((r for r in read_index(cfg["_root"]) if r["id"] == oid and r["state"] != "CLOSED"), None):
        add_run(cfg, oid, run)
        return
    if oid:
        why = f"order {oid} was closed before the agent finished"
    f = _cache(cfg) / "usage_unassigned.jsonl"
    with _lock(cfg, f.name):
        old = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
        if run.get("key") and any(f'"key": "{run["key"]}"' in x for x in old):
            return  # the same agent result delivered twice
        old.append(json.dumps({**run, "why": why}, ensure_ascii=False))
        f.write_text("\n".join(old[-UNASSIGNED_CAP:]) + "\n", encoding="utf-8", newline="\n")


def transcript_totals(path: Path, start: int = 0) -> dict | None:
    """One run of a subagent from its transcript, lines [start:]: tokens = context of the last assistant message (the
    Agent tool's totalTokens, ~5% low: that message's output count is not fully written - checked on N015's verifier2 run,
    38.5k vs 40.7k, tool uses 8 = 8), tool uses, wall time. None when the segment has no assistant message."""
    rs = list(transcripts.rows(path))
    reqs = transcripts.requests(rs, start)
    if not reqs:
        return None
    c = transcripts.cost(reqs)
    return {"tokens": c["context_last"], "tool_uses": c["tool_uses"], "duration_ms": c["duration_ms"], "lines": len(rs), **_usage_fields(c)}


def _lines(p: Path | None) -> int:
    try:
        return sum(1 for _ in transcripts.rows(p)) if p and p.is_file() else -1
    except OSError:
        return -1


def on_agent(cfg: dict, data: dict) -> None:
    """PostToolUse on the Agent tool. Foreground: the result carries the totals. Background (`async_launched`): only the
    agent id - remember it with its order, SubagentStop books it when the agent finishes."""
    tr, ti = data.get("tool_response"), data.get("tool_input") or {}
    if isinstance(tr, str):
        try:
            tr = json.loads(tr)
        except json.JSONDecodeError:
            return
    if not isinstance(tr, dict):
        return
    aid = str(tr.get("agentId") or "")
    who = str(tr.get("agentType") or ti.get("subagent_type") or "")  # empty: SubagentStop's agent_type fills it (r3 #2)
    agents = pending(cfg)
    skey = f"session:{data.get('session_id')}" if data.get("session_id") else ""
    text = " ".join(str(x) for x in (ti.get("prompt"), tr.get("prompt"), ti.get("description")) if x)  # the brief first (rung 3 #4)
    oid, why = resolve_order(cfg, text, (agents.get(skey) or {}).get("order") if skey else None)
    rows = {r["id"]: r["state"] for r in read_index(cfg["_root"])}
    live = {x for x in ORDER_RE.findall(text) if rows.get(x, "CLOSED") != "CLOSED"}
    if skey and why == "named in the prompt" and len(live) == 1:  # one open order named = this session's subject; a prompt
        agents[skey] = {"order": oid, "at": datetime.now().timestamp()}  # naming several may only mention them (r2 low #1)
        save_pending(cfg, agents)
    if tr.get("status") == "completed" and tr.get("totalTokens") is not None:
        book(cfg, oid, make_run(who or "general-purpose", tr["totalTokens"], src="hook:agent", tool_uses=tr.get("totalToolUseCount"),
                                duration_ms=tr.get("totalDurationMs"), agent_id=aid, key=f"{aid}#0" if aid else None), why)
        if aid:  # a later continuation of this agent (SendMessage) is booked from the lines after this point
            tp = data.get("transcript_path")
            sub = Path(os.path.expanduser(tp)).with_suffix("") / "subagents" / f"agent-{aid}.jsonl" if tp else None
            agents[aid] = {"order": oid, "why": why, "who": who, "lines": _lines(sub), "pending": False, "at": datetime.now().timestamp()}
            save_pending(cfg, agents)
    elif tr.get("status") == "async_launched" and aid:
        agents[aid] = {"order": oid, "why": why, "who": who, "lines": 0, "pending": True, "at": datetime.now().timestamp()}
        save_pending(cfg, agents)


def on_stop(cfg: dict, data: dict) -> None:
    """SubagentStop: books a background agent (or a continuation) from its own transcript. Agents this hook did not see
    launched are ignored - a foreground agent's stop fires before its PostToolUse, which books it with exact numbers."""
    aid = str(data.get("agent_id") or "")
    agents = pending(cfg)
    m = agents.get(aid)
    if not m or m.get("lines", -1) < 0 or not data.get("agent_transcript_path"):
        return
    p = Path(os.path.expanduser(str(data["agent_transcript_path"])))
    tot = transcript_totals(p, m["lines"]) if p.is_file() else None
    if not tot:
        return
    book(cfg, m.get("order"), make_run(str(data.get("agent_type") or m.get("who") or "general-purpose"), tot["tokens"], src="hook:subagent-stop",
                                       tool_uses=tot["tool_uses"], duration_ms=tot["duration_ms"], agent_id=aid,
                                       key=f"{aid}#{m['lines']}", approx=True,
                                       **{k: tot[k] for k in ("units", "requests", "models", "usage")}), m.get("why", ""))
    m.update(lines=tot["lines"], pending=False, at=datetime.now().timestamp())
    save_pending(cfg, agents)


def hook(stdin: bytes | str) -> int:
    cfg = None
    try:  # bytes, decoded here: the console code page (cp1251/cp1252) cannot read UTF-8 Cyrillic in a prompt (N019 r1 #3)
        data = json.loads((stdin.decode("utf-8", errors="replace") if isinstance(stdin, bytes) else stdin) or "{}")
        root = project_root(Path(data.get("cwd") or os.getcwd()).resolve())
        if root is None:
            return 0
        cfg = load_cfg(root)
        if data.get("hook_event_name") == "PostToolUse" and data.get("tool_name") in ("Agent", "Task"):
            with _lock(cfg, "usage_agents.json"):  # the agents map, then (inside) the order's ledger - always in this order
                on_agent(cfg, data)
        elif data.get("hook_event_name") == "SubagentStop":
            with _lock(cfg, "usage_agents.json"):
                on_stop(cfg, data)
    except Exception as e:  # noqa: BLE001 - accounting never breaks a session; the last error is kept for diagnosis
        try:
            if cfg:
                (_cache(cfg) / "usage_hook_error.txt").write_text(f"{today()} {now_hm()} {type(e).__name__}: {e}\n", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return 0


# ---------- reports ----------
def unassigned(cfg: dict, days: int | None = None) -> list[dict]:
    f = cfg["_layout"]["state"] / "cache" / "usage_unassigned.jsonl"
    out = []
    for ln in (f.read_text(encoding="utf-8", errors="replace").splitlines() if f.is_file() else []):
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if days is None or r.get("when", "") >= (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d"):
            out.append(r)
    return out


def collect(cfg: dict, days: int | None = None) -> list[dict]:
    rows = {r["id"]: r for r in read_index(cfg["_root"])}
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d") if days else ""
    out = []
    for f in sorted((cfg["_layout"]["circle"] / "answers").glob("N[0-9][0-9][0-9]_usage.json")):
        oid = f.name[:4]
        d = load(cfg, oid)
        est, cut = estimate_of(cfg, oid), False
        if since:  # only the period's runs count, not the whole ledger of an order that had one run in it (rung 3 #7)
            runs = [r for r in d["runs"] if r.get("when", "") >= since]
            if not runs:
                continue
            cut, d = len(runs) < len(d["runs"]), {**d, "runs": runs}
        s = summary(d, est)
        if cut:  # part of the order is outside the period: its estimate is compared with the whole order only (r3 #1)
            s["ratio"] = None
        out.append({"order": oid, "state": rows.get(oid, {}).get("state", "?"), "closed": d.get("closed", {}).get("when", ""),
                    "partial": cut, **s})
    return out


def brief_line(cfg: dict, current: str) -> str | None:
    """One line for the SessionStart brief; None when the project has no token data yet."""
    data = collect(cfg)
    parts = []
    cur = next((x for x in data if x["order"] == current), None)
    if cur and cur["state"] != "CLOSED":
        parts.append(f"{current} so far {fmt(cur['total'])} tok" + (f" / {fmt(cur['units'])} units" if cur.get("units") else "")
                     + f" ({cur['runs']} run(s))")
    closed = sorted((x for x in data if x["closed"]), key=lambda x: x["closed"])
    if closed:
        x = closed[-1]
        amount = (f"{fmt(x['units'])} units ({fmt(x['total'])} tok)" if x.get("units_complete") else
                  f"{fmt(x['total'])} tok" + (f" / {fmt(x['units'])} units (part)" if x.get("units") else ""))
        parts.append(f"last closed {x['order']} {amount}" + (f" vs est. {fmt(x['estimate'])} x{x['ratio']:.1f}" if x["ratio"] else ""))
    week = collect(cfg, 7)
    if week:
        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        live = [r for x in week for r in load(cfg, x["order"])["runs"]
                if r.get("when", "") >= since and isinstance(r.get("tokens"), int) and not r.get("superseded")]
        units = sum(int(r.get("units") or 0) for r in live)
        parts.append(f"7 d {fmt(sum(r['tokens'] for r in live))} tok" + (f" / {fmt(units)} units" if units else "") + f" on {len(week)} order(s)")
    un = unassigned(cfg, 7)
    if un:
        parts.append(f"unassigned {len(un)} run(s) {fmt(sum(r.get('tokens', 0) for r in un))}")
    return ("  tokens: " + " · ".join(parts)) if parts else None


def report(cfg: dict, days: int | None) -> str:
    data = collect(cfg, days)
    head = f"# Claude tokens per order{f' (last {days} days)' if days else ''} - usage.py report, {today()}\n\n"
    out = []
    if not data:
        out.append(head + "No order has a token ledger" + (" with runs in this period" if days else "") + " (answers/N_usage.json): "
                   "tokens are not measured - say so, never estimate them after the fact.")
    else:
        out.append(head + "| order | state | spend units | total tokens | agents (runs) | Lead | estimate | actual/estimate | agent minutes | biggest agents |")
        out.append("|---|---|---|---|---|---|---|---|---|---|")
    for x in data:
        top = ", ".join(f"{w} {fmt(t)}" for w, t in list(x["by_who"].items())[:3]) or "-"
        ratio = f"x{x['ratio']:.2f} ({x['ratio_of']})" if x["ratio"] else ("(order spans the period)" if x.get("partial") and x["estimate"] else "-")
        minutes = round(x["duration_ms"] / 60000) if x["duration_ms"] else "-"
        lead = (f"{fmt(x['lead_units'])} u, {x['lead_requests']} req" if x.get("lead_measured") else
                f"est. {fmt(x['lead_estimate'])}" if x["lead_estimate"] else "-")
        units = fmt(x["units"]) + ("" if x.get("units_complete") or not x["units"] else " (part)") if x.get("units") else "-"
        out.append(f"| {x['order']} | {x['state']} | {units} | {fmt(x['total'])} | {fmt(x['agents_tokens'])} ({x['runs']}) | {lead} | "
                   f"{fmt(x['estimate'])} | {ratio} | {minutes} | {top} |")
    total = sum(x["total"] for x in data)
    rated = [x for x in data if x["ratio"]]
    if data:
        full = [x for x in data if x.get("units_complete")]
        out.append(f"\nTotal {fmt(total)} reported tokens on {len(data)} order(s); agents {fmt(sum(x['agents_tokens'] for x in data))}"
                   + (f"; spend {fmt(sum(x['units'] for x in full))} units on the {len(full)} order(s) measured from transcripts "
                      f"(Lead {fmt(sum(x['lead_units'] for x in full))}, agents {fmt(sum(x['agents_units'] for x in full))})" if full else "")
                   + ".")
    if rated:  # each order against its estimate in the same unit as its row (units once measured from transcripts)
        est, act = sum(x["estimate"] for x in rated), sum(x["units"] if x["ratio_of"] == "units" else x["total"] for x in rated)
        out.append(f"Estimates: {len(rated)} order(s), actual/estimate x{act / est:.2f} overall; off by more than 2x: "
                   + (", ".join(f"{x['order']} x{x['ratio']:.1f}" for x in rated if x["ratio"] > 2 or x["ratio"] < 0.5) or "none") + ".")
    un = unassigned(cfg, days)
    if un:
        out.append(f"Unassigned (no open order named in the prompt): {len(un)} run(s), {fmt(sum(r.get('tokens', 0) for r in un))} - "
                   f"<state>/cache/usage_unassigned.jsonl.")
    out.append("Metrics. Spend units = input-token equivalents summed over every request in the Claude Code transcripts (input 1, "
               "cache write 5m 1.25 / 1h 2, cache read 0.1 - 0.05 opus-5-5/sonnet-5-5, 0.025 fable - output 5; tools/transcripts.py); "
               "the very last message of an agent may count a little low. Reported tokens = an agent's context at its last request "
               "(subagent_tokens), the metric of the 2026-10-05 audit - not the sum over its requests. Orders without a ledger were not measured.")
    return "\n".join(out) + "\n"


def main() -> int:
    if "--hook" in sys.argv:
        try:
            raw = sys.stdin.buffer.read()
        except Exception:  # noqa: BLE001 - no stdin, closed pipe: nothing to book, never a failure
            raw = b""
        return hook(raw)
    from forge_common import find_root
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("add"); ad.add_argument("id"); ad.add_argument("--who", default="agent")
    ad.add_argument("--usage", help="the pasted <usage> block of an Agent result"); ad.add_argument("--tokens")
    ad.add_argument("--tool-uses", type=int); ad.add_argument("--duration-ms", type=int)
    ad.add_argument("--estimate", action="store_true", help="the figure is an estimate (the Lead's own share), not a measurement")
    sub.add_parser("show").add_argument("id")
    sc = sub.add_parser("scan", help="N020: what the transcripts say about one order (--apply books it, as close does)")
    sc.add_argument("id"); sc.add_argument("--apply", action="store_true")
    rp = sub.add_parser("report"); rp.add_argument("--days", type=int); rp.add_argument("--md")
    a = ap.parse_args()
    cfg = load_cfg(find_root())
    if a.cmd == "add":
        print(record(cfg, a.id, a.who, a.usage, a.tokens, a.tool_uses, a.duration_ms, a.estimate, src="manual")); return 0
    if a.cmd == "scan":
        res = scan_apply(cfg, a.id, apply=a.apply)
        for c in res["lead"]:
            print(f"  lead      session {c['session'][:8]}  {c['requests']:>4} requests  {fmt(c['units']):>7} units  {', '.join(c['models'])}")
        for x in res["agents"]:
            print(f"  agent     {x['who']:<22} {x['requests']:>4} requests  {fmt(x['units']):>7} units  ({x['why']})")
        if not res["sessions"]:
            print(f"  no transcript of a session that worked on {a.id} under {transcripts.claude_home() / 'projects'}")
        d = load(cfg, a.id) if a.apply else merge_scan(load(cfg, a.id), res)
        print(line(a.id, summary(d, estimate_of(cfg, a.id))) + ("" if a.apply else "   (dry run: --apply books it)"))
        return 0
    if a.cmd == "show":
        d = load(cfg, a.id)
        for r in d["runs"]:
            print(f"  {r.get('when')}  {r.get('who'):<22} {r.get('kind'):<9} {fmt(r.get('tokens')):>7}  {r.get('src')}")
        print(line(a.id, summary(d, estimate_of(cfg, a.id)))); return 0
    text = report(cfg, a.days)
    if a.md:
        Path(a.md).write_text(text, encoding="utf-8", newline="\n"); print(f"written: {a.md}")
    else:
        print(text)
    return 0


def record(cfg: dict, oid: str, who: str, block: str | None, tokens, tool_uses=None, duration_ms=None, estimate: bool = False,
           src: str = "manual") -> str:
    """Manual booking (order.py usage / answer / done, step.py end): a pasted usage block or --tokens. Raises SystemExit on
    a missing or unreadable number - a silent zero would look like a measurement."""
    if not any(r["id"] == oid for r in read_index(cfg["_root"])):
        raise SystemExit(f"{oid} not in index")
    got = parse_block(block) if block else {}
    if tokens is not None:
        n = parse_tokens(tokens)
        if n is None:
            raise SystemExit(f"tokens: cannot read {tokens!r} - give a number like 40685, 41k or 1.2M")
        got["tokens"] = n
    if "tokens" not in got:
        raise SystemExit("usage: no token count found - pass --usage \"<the usage block>\" (subagent_tokens / total_tokens) or --tokens N")
    kind = "estimate" if estimate or (who or "").lower().startswith("lead") else "measured"
    return add_run(cfg, oid, make_run(who, got["tokens"], kind=kind, src=src, tool_uses=tool_uses if tool_uses is not None else got.get("tool_uses"),
                                      duration_ms=duration_ms if duration_ms is not None else got.get("duration_ms")))


if __name__ == "__main__":
    sys.exit(main())
