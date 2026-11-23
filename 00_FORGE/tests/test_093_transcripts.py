"""0.9.3 tests, part 2 (N020): the token meter reads Claude Code transcripts. Synthetic transcripts in the shape Claude Code
writes; expected units are computed by hand in the tests, before the run. Split from test_093.py (gate_size 64 KB)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import PY, TOOLS, Project  # noqa: E402
from test_093 import RU, agent_done, hook  # noqa: E402

sys.path.insert(0, str(TOOLS))
import transcripts  # noqa: E402
import usage  # noqa: E402


BASE = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=60)  # synthetic transcripts: a minute ago


def ts(t: float) -> str:
    """ISO time of a synthetic row, `t` seconds after BASE (local and UTC dates agree with today but in the first minute
    after local midnight - r2 #2: a UTC date against local dates made the suite fail for hours a day)."""
    return (BASE + timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def usage_row(mid: str, t: int, cwd, *, I=0, W=0, W1=0, R=0, O=0, model="claude-opus-5-5", content=None) -> dict:
    """One assistant row as Claude Code writes it; `t` = seconds after 10:00:00."""
    return {"type": "assistant", "timestamp": ts(t), "cwd": str(cwd),
            "message": {"id": mid, "model": model, "content": content or [{"type": "text", "text": "x"}],
                        "usage": {"input_tokens": I, "cache_creation_input_tokens": W, "cache_creation": {"ephemeral_1h_input_tokens": W1},
                                  "cache_read_input_tokens": R, "output_tokens": O}}}


def result_row(t: int, cwd, text: str, call: str = "x") -> dict:
    return {"type": "user", "timestamp": ts(t), "cwd": str(cwd),
            "message": {"content": [{"type": "tool_result", "tool_use_id": call, "content": text}]}}


def bash(cmd: str, call: str = "b") -> list:
    return [{"type": "tool_use", "id": call, "name": "Bash", "input": {"command": cmd}}]


def write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")


class Transcripts(unittest.TestCase):
    """N020: the meter reads the transcripts Claude Code writes. Expected units are computed by hand below, before the run."""

    def setUp(self) -> None:
        self.p = Project()
        self.p.order("new", "A", "--tier", "LOCAL", "--changes", "a")
        self.p.order("new", "B", "--tier", "LOCAL", "--changes", "b")
        self.home = self.p.tmp / "claude_home"
        self.env = {"CLAUDE_CONFIG_DIR": str(self.home)}
        d, out = self.p.dir, self.p.tmp / "elsewhere"
        # the session was started in the parent folder (filed under its slug) and works in the project - like C--ai for C:/ai/agi
        self.sess = self.home / "projects" / transcripts.slug(self.p.tmp.resolve()) / "s1.jsonl"
        write_jsonl(self.sess, [
            {"type": "user", "timestamp": ts(0), "cwd": str(d), "message": {"content": "start"}},
            usage_row("m1", 1, d, I=7, R=500, O=9),                                                  # before any order: nobody's
            usage_row("m2", 2, d, I=1, R=600, O=5, content=bash("python C:/x/tools/order.py take N001", "b1")),  # decides: still nobody's
            result_row(3, d, "N001 TAKEN — STATE RUNNING", "b1"),
            usage_row("m3", 4, d, I=100, W=1000, W1=1000, R=10000, O=1, content=[{"type": "tool_use", "id": "tu1", "name": "Agent", "input": {}}]),
            usage_row("m3", 4, d, I=100, W=1000, W1=1000, R=10000, O=400),                         # same request, final output
            usage_row("o1", 5, out, I=3, R=50, O=1, content=bash("python order.py take N002")),             # cwd outside: never switches
            usage_row("m4", 6, d, I=10, R=20000, O=50, content=bash("python order.py done N001 --text ok", "b2")),
            result_row(8, d, "N001 CLOSED — gates green, STATE READY", "b2"),
            usage_row("m5", 9, d, I=4, R=700, O=6)])                                                  # after the close: nobody's
        sub = self.sess.with_suffix("") / "subagents"
        son = "claude-sonnet-4"  # cache read 0.1
        write_jsonl(sub / "agent-x1.jsonl", [{"type": "user", "timestamp": ts(4), "message": {"content": "check the code"}},
                                             usage_row("k1", 4, d, I=5, W=2000, O=5, model=son), usage_row("k1", 4, d, I=5, W=2000, O=300, model=son),
                                             usage_row("k2", 5, d, I=1, W=100, R=2000, O=200, model=son)])
        (sub / "agent-x1.meta.json").write_text(json.dumps({"agentType": "forge:verifier2", "description": "review", "toolUseId": "tu1"}), encoding="utf-8")
        write_jsonl(sub / "agent-x2.jsonl", [{"type": "user", "timestamp": ts(5), "message": {"content": "Review order N002"}},
                                             usage_row("q1", 5, d, I=9, R=9000, O=90, model=son)])
        (sub / "agent-x2.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "review N002", "toolUseId": "tu9"}), encoding="utf-8")
        write_jsonl(sub / "agent-x3.jsonl", [{"type": "user", "timestamp": ts(5), "message": {"content": "look around"}},
                                             usage_row("r1", 5, d, R=1000, O=10, model=son), usage_row("r2", 12, d, R=5000, O=10, model=son)])
        (sub / "agent-x3.meta.json").write_text(json.dumps({"agentType": "Plan", "description": "look", "toolUseId": "tu8"}), encoding="utf-8")
        # a sibling project's session (slug ...-proj2) naming N001 must never be read
        write_jsonl(self.home / "projects" / (transcripts.slug(self.p.dir.resolve()) + "2") / "s9.jsonl",
                    [usage_row("z1", 4, self.p.tmp / "proj2", I=1, R=1, O=1, content=bash("python order.py take N001")), usage_row("z2", 5, self.p.tmp / "proj2", I=99999)])

    def tearDown(self) -> None:
        self.p.cleanup()

    def test_scan_charges_the_lead_window_and_the_agents(self) -> None:
        import os as _os
        old = _os.environ.get("CLAUDE_CONFIG_DIR"); _os.environ["CLAUDE_CONFIG_DIR"] = str(self.home)
        try:
            res = transcripts.scan(self.p.dir, "N001", known={"N001", "N002"})
            closed2 = transcripts.scan(self.p.dir, "N001", known={"N001", "N002"}, closed_at={"N002": 0.0})  # closed before: a reference
            # the folders read: the project's and its ancestors' (s1, filed under the parent), never a sibling's (s9)
            self.assertEqual([f.stem for f in transcripts.session_files(self.p.dir)], ["s1"])
        finally:
            _os.environ["CLAUDE_CONFIG_DIR"] = old
        # EXPECTED (by hand): m3 = 100 + 2*1000 (1h write) + 0.05*10000 (opus-5-5 read) + 5*400 = 4600; m4 = 10 + 0.05*20000 + 5*50 = 1260;
        # o1 = 3 + 0.05*50 + 5*1 = 10.5 - its cwd is outside, so its command cannot switch the order, but the request itself is
        # the Lead's spend while N001 is active (first expectation said 2 requests / 5860; corrected: cwd decides switching only)
        self.assertEqual([(c["requests"], c["units"]) for c in res["lead"]], [(3, 5870)])
        self.assertEqual(res["lead"][0]["cache_write_1h"], 1000)
        # x1: k1 = 5 + 1.25*2000 + 5*300 (last row, not the first row's 5) = 4005; k2 = 1 + 1.25*100 + 0.1*2000 + 5*200 = 1326
        got = {a["agent_id"]: (a["units"], a["why"]) for a in res["agents"]}
        self.assertEqual(got["x1"], (5331, "spawned while the order was active"))
        self.assertEqual(got["x3"], (150, "requests in the order's window"))  # r1 inside the window (0.1*1000 + 5*10), r2 after the close
        self.assertNotIn("x2", got)  # names N002, an open order of this project
        self.assertIn(("x2", "requests in the order's window"), [(a["agent_id"], a["why"]) for a in closed2["agents"]])
        self.assertEqual(res["agents"][0]["context_last"], 1 + 100 + 2000 + 200)

    def test_close_books_the_transcripts_and_supersedes_the_rest(self) -> None:
        p, env = self.p, self.env
        p.order("take", "N001")
        hook(p, agent_done("check the code", tokens=2400, aid="x1"))               # the hook saw x1 (before the scan)
        p.order("usage", "N001", "--who", "verifier2", "--tokens", "2350")         # and the Lead pasted it by hand too
        p.order("usage", "N001", "--who", "lead", "--tokens", "999k")              # and estimated its own share
        d = p.read / "N001_a.md"; d.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", d.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        out = p.order("close", "N001", env=env).stdout
        self.assertIn("N001 spend 11k units (Lead 6k in 3 requests + agents 5k in 2 run(s); from the transcripts)", out)
        led = json.loads((p.circle / "answers" / "N001_usage.json").read_text(encoding="utf-8"))
        live = [r for r in led["runs"] if not r.get("superseded")]
        self.assertEqual(sorted((r["who"], r["units"]) for r in live), [("Plan", 150), ("forge:verifier2", 5331), ("lead", 5870)])
        self.assertEqual(sorted(r["src"] for r in led["runs"] if r.get("superseded")), ["hook:agent", "manual", "manual"])
        self.assertEqual(len([r for r in led["runs"] if r.get("superseded")]), 3)  # hook x1, hand copy, the Lead's estimate
        self.assertEqual(led["closed"]["units"], 11351)
        self.assertFalse((p.circle / "answers" / "N002_usage.json").exists())  # the outside-cwd command never made N002 active
        again = subprocess.run([PY, str(TOOLS / "usage.py"), "scan", "N001", "--apply"], cwd=p.dir, capture_output=True, text=True,
                               encoding="utf-8", env={**os.environ, **env})
        self.assertEqual(again.returncode, 0)
        led2 = json.loads((p.circle / "answers" / "N001_usage.json").read_text(encoding="utf-8"))
        self.assertEqual(len(led2["runs"]), len(led["runs"]))  # a re-scan replaces its runs, it does not add them twice

    def test_without_transcripts_the_hook_and_hand_figures_stay(self) -> None:
        p = self.p
        p.order("take", "N001")
        hook(p, agent_done("check N001", tokens=2400, aid="x1"))
        d = p.read / "N001_a.md"; d.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", d.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        out = p.order("close", "N001").stdout  # the suite's empty Claude home: no transcript at all
        self.assertIn("N001 tokens 2k (agents 2k in 1 run(s))", out)
        self.assertIn("no transcript found", out)

    def scan_with(self, home: Path, oid: str, known=None, closed_at=None) -> dict:
        import os as _os
        old = _os.environ.get("CLAUDE_CONFIG_DIR"); _os.environ["CLAUDE_CONFIG_DIR"] = str(home)
        try:
            return transcripts.scan(self.p.dir, oid, known=known, closed_at=closed_at)
        finally:
            _os.environ["CLAUDE_CONFIG_DIR"] = old

    def test_window_rules(self) -> None:
        """N020 r1 #2: only the session's own order.py results move the window; done of another order hands it back; hold ends it."""
        d, home = self.p.dir, self.p.tmp / "home2"
        write_jsonl(home / "projects" / transcripts.slug(d.resolve()) / "w.jsonl", [
            usage_row("a0", 0, d, I=1, content=bash("python order.py take N001", "c1")), result_row(1, d, "N001 TAKEN", "c1"),
            usage_row("a1", 2, d, I=10),                                                       # N001
            result_row(3, d, "file text: N001 created ... N001 CLOSED", "read7"),              # a Read result, not an order.py call
            usage_row("a2", 4, d, I=20, content=bash("python order.py done N002 --text ok", "c2")),  # still N001
            result_row(5, d, "N002 CLOSED — gates green", "c2"),
            usage_row("a3", 6, d, I=40, content=bash("python order.py hold N001 --why wait", "c3")),  # N001 again (handed back)
            result_row(7, d, "N001 -> HOLD", "c3"),
            usage_row("a4", 8, d, I=80)])                                                      # nobody's: held
        lead = self.scan_with(home, "N001")["lead"]
        self.assertEqual([(c["requests"], c["input"]) for c in lead], [(3, 10 + 20 + 40)])
        self.assertEqual(self.scan_with(home, "N002")["lead"], [])  # `done N002` decided by N001's request; N002 closed at once

    def test_named_agent_outside_the_project_and_round_ends(self) -> None:
        d, home = self.p.dir, self.p.tmp / "home3"
        sess = home / "projects" / transcripts.slug(d.resolve()) / "v.jsonl"
        write_jsonl(sess, [usage_row("b0", 0, d, I=1, content=bash("python order.py take N001", "c1")), result_row(1, d, "N001 TAKEN — STATE RUNNING", "c1"),
                           usage_row("b1", 2, d, I=1)])
        sub = sess.with_suffix("") / "subagents"
        far = self.p.tmp / "other_project"
        write_jsonl(sub / "agent-o1.jsonl", [{"type": "user", "timestamp": ts(-3601), "cwd": str(far), "message": {"content": "N001 of the other project"}},
                                             {**usage_row("o1", 0, far, I=500), "timestamp": ts(-3600)}])  # before the window, elsewhere
        (sub / "agent-o1.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "N001 elsewhere", "toolUseId": "zz"}), encoding="utf-8")
        self.assertEqual(self.scan_with(home, "N001", known={"N001"})["agents"], [])  # a name alone does not charge it
        rs = [{"type": "user", "message": {"content": "go"}}, usage_row("x1", 1, d, R=1000), usage_row("x2", 2, d, R=50000),
              usage_row("x3", 3, d, R=80000), {"type": "user", "message": {"content": "round 2"}}, usage_row("x4", 4, d, R=90000)]
        ends = transcripts.round_ends(rs)
        self.assertEqual(ends, [80000, 90000])
        self.assertFalse(usage.covers({"round_ends": ends}, 50500))  # a mid-round context is not a reported figure
        self.assertTrue(usage.covers({"round_ends": ends}, 81000))

    def test_units_in_close_report_brief_and_run_time(self) -> None:
        p, env = self.p, self.env
        p.order("take", "N001"); p.order("amend", "N001", "--why", "e", "--estimate", "10k")
        p.order("usage", "N001", "--who", "lead", "--tokens", "999k")                 # today: superseded by the measured Lead
        f = p.circle / "answers" / "N001_usage.json"
        d = p.read / "N001_a.md"; d.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", d.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        out = p.order("close", "N001", env=env).stdout
        led = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual([r["tokens"] for r in led["runs"] if r.get("kind") == "estimate" and not r.get("superseded")], [])
        lead_run = next(r for r in led["runs"] if r.get("src") == "transcript" and r["who"] == "lead")
        want = datetime.fromtimestamp((BASE + timedelta(seconds=6)).timestamp()).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(lead_run["when"], want)  # r1 #5: the run's own time (its last request), not the scan's
        self.assertIn("vs estimate 10k = x1.14 (units)", out)  # 11351 measured units / 10k
        rep = subprocess.run([PY, str(TOOLS / "usage.py"), "report"], cwd=p.dir, capture_output=True, text=True, encoding="utf-8",
                             env={**os.environ, **env}).stdout
        self.assertRegex(rep, r"\| N001 \| CLOSED \| 11k \|")
        self.assertIn("x1.14 (units)", rep); self.assertIn("actual/estimate x1.14 overall", rep)  # r1 #1: footer in the rows' unit
        brief = p.step("--brief").stdout
        self.assertIn("last closed N001 11k units", brief)
        week = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        live = [r for r in led["runs"] if not r.get("superseded") and r["when"] >= week]
        self.assertIn(f"7 d {usage.fmt(sum(r['tokens'] for r in live))} tok / 11k units on 1 order(s)", brief)  # r1 #1 + rung 3 #6
        self.assertIn("last closed N001 11k units (", brief)  # units next to tokens (rung 3 #6)
        # rung 3 #2: figures typed after the scan never double what the transcripts measured
        out2 = p.order("usage", "N001", "--who", "verifier2", "--tokens", "2350").stdout   # x1's round end reads 2301
        self.assertIn("already measured from the transcripts", out2)
        self.assertIn("already measured from the transcripts", p.order("usage", "N001", "--who", "lead", "--tokens", "50k").stdout)
        self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["closed"]["units"], 11351)
        # rung 3 #4: an estimate the transcripts do not cover (another day) leaves the units partial; the ratio falls back to tokens
        led = json.loads(f.read_text(encoding="utf-8"))
        led["runs"].append(usage.make_run("lead", 777000, kind="estimate", src="session-end")); led["runs"][-1]["when"] = "2026-01-01 10:00"
        s = usage.summary(led, 10000)
        self.assertFalse(s["units_complete"])
        self.assertIn("Lead est. 777k", usage.line("N001", s)); self.assertIn("(part of the order)", usage.line("N001", s))
        self.assertEqual(s["ratio_of"], "tokens")

    def test_window_needs_a_successful_run(self) -> None:
        """r2 #A/#2: a refused take, a quoted command, a heredoc fixture and a report of another order open no window;
        `N created` from an `order.py new` run does."""
        d, home = self.p.dir, self.p.tmp / "home4"
        write_jsonl(home / "projects" / transcripts.slug(d.resolve()) / "r.jsonl", [
            usage_row("g0", 0, d, I=1, content=bash('python C:/p/tools/order.py new "first" --tier LEAD', "n1")),
            result_row(1, d, "K26: single order is optimal\r\nN001 created: circle/orders/N001_first.md", "n1"),  # text after \r\n
            usage_row("g1", 2, d, I=10),                                                                       # N001
            usage_row("g1b", 2, d, I=5, content=bash("python order.py take N002 2>&1 | tail -5", "p2")),   # refused, piped: no is_error
            result_row(3, d, "already TAKEN (Claude tier): ['N001'] - close or hold it first", "p2"),
            usage_row("g2", 3, d, I=20, content=bash("python order.py take N002", "n2")),
            {**result_row(4, d, "already TAKEN (Claude tier): ['N001']", "n2"), "message": {"content": [
                {"type": "tool_result", "tool_use_id": "n2", "is_error": True, "content": "Exit code 1\nalready TAKEN"}]}},
            usage_row("g3", 5, d, I=40, content=bash('git commit -m "python order.py take N002"', "n3")),      # quoted: data
            result_row(5, d, "[main abc123] N002 TAKEN in the message", "n3"),                                # even with a success-like text
            usage_row("g4", 6, d, I=80, content=bash("cat > t.py <<'EOF'\nbash('cd x; python order.py take N002')\nEOF\npython t.py", "n4")),
            result_row(7, d, "N002 TAKEN — STATE RUNNING (the fixture's own output)", "n4"),
            usage_row("g5", 8, d, I=160, content=bash("python order.py report N002 --verdict APPROVE --text ok", "n5")),
            result_row(9, d, "reports/N002.md written: APPROVE", "n5"),
            usage_row("g6", 10, d, I=320)])                                                                    # still N001
        lead = self.scan_with(home, "N001")["lead"]
        self.assertEqual([(c["requests"], c["input"]) for c in lead], [(7, 10 + 5 + 20 + 40 + 80 + 160 + 320)])
        self.assertEqual(self.scan_with(home, "N002")["lead"], [])
        # a take that succeeded inside a command that then failed (exit 1, is_error) still opens the window
        write_jsonl(home / "projects" / transcripts.slug(d.resolve()) / "r.jsonl", [
            usage_row("k0", 0, d, I=1, content=bash("python order.py take N002 && false", "k1")),
            {**result_row(1, d, "", "k1"), "message": {"content": [{"type": "tool_result", "tool_use_id": "k1", "is_error": True,
                                                                     "content": "N002 TAKEN — STATE RUNNING\nExit code 1"}]}},
            usage_row("k2", 2, d, I=7)])
        self.assertEqual([(c["requests"], c["input"]) for c in self.scan_with(home, "N002")["lead"]], [(1, 7)])

    def test_agent_time_rules(self) -> None:
        """r2 #3/#4: an agent stays with the order it was spawned under, and its name is judged at its own start time -
        a later scan of another order never charges it again."""
        d, home = self.p.dir, self.p.tmp / "home5"
        sess = home / "projects" / transcripts.slug(d.resolve()) / "a.jsonl"
        write_jsonl(sess, [
            usage_row("h0", 0, d, I=1, content=bash("python order.py take N001", "c1")), result_row(1, d, "N001 TAKEN — STATE RUNNING", "c1"),
            usage_row("h1", 2, d, I=1, content=[{"type": "tool_use", "id": "sp1", "name": "Agent", "input": {}}]),
            usage_row("h2", 3, d, I=1, content=bash("python order.py done N001 --text ok", "c2")), result_row(4, d, "N001 CLOSED", "c2"),
            usage_row("h3", 5, d, I=1, content=bash("python order.py take N002", "c3")), result_row(6, d, "N002 TAKEN — STATE RUNNING", "c3"),
            usage_row("h4", 9, d, I=1)])
        sub = sess.with_suffix("") / "subagents"
        son = "claude-sonnet-4"
        # bg1: spawned under N001, still running after N001 closed and N002 began
        write_jsonl(sub / "agent-bg1.jsonl", [{"type": "user", "timestamp": ts(2), "message": {"content": "keep looking"}},
                                              usage_row("p1", 3, d, R=1000, model=son), usage_row("p2", 8, d, R=2000, model=son)])
        (sub / "agent-bg1.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "dig", "toolUseId": "sp1"}), encoding="utf-8")
        # nm1: names N001 then N002, started after N001 closed -> N002; nm0: same names, started before -> N001
        for name, start in (("nm1", 7), ("nm0", 2)):
            write_jsonl(sub / f"agent-{name}.jsonl", [{"type": "user", "timestamp": ts(start), "cwd": str(d), "message": {"content": "compare N001 and N002"}},
                                                     usage_row(f"{name}r", start + 0.5, d, R=3000, model=son)])
            (sub / f"agent-{name}.meta.json").write_text(json.dumps({"agentType": "Plan", "description": "compare", "toolUseId": "none"}), encoding="utf-8")
        closed = {"N001": (BASE + timedelta(seconds=4)).timestamp()}
        n1 = {a["agent_id"]: a["why"] for a in self.scan_with(home, "N001", {"N001", "N002"}, closed)["agents"]}
        n2 = {a["agent_id"]: a["why"] for a in self.scan_with(home, "N002", {"N001", "N002"}, closed)["agents"]}
        self.assertEqual(n1, {"bg1": "spawned while the order was active", "nm0": "named N001"})
        self.assertEqual(n2, {"nm1": "named N002"})  # bg1 stays N001's (spawn), nm0 started while N001 was open
        self.assertEqual(set(n1) & set(n2), set())   # every agent in exactly one order, whatever the scan order
        no_journal = {a["agent_id"] for a in self.scan_with(home, "N001", {"N001", "N002"})["agents"]}
        self.assertEqual(no_journal, set(n1))        # the transcript's own `N001 CLOSED` gives the same answer without the journal

    def test_sibling_slug_folder_is_kept_out_by_cwd(self) -> None:
        d, home = self.p.dir, self.p.tmp / "home6"
        old = self.p.tmp / (self.p.dir.name + "-old")
        sess = home / "projects" / transcripts.slug(old.resolve()) / "o.jsonl"  # slug "...-proj-old" also matches "...-proj-"
        write_jsonl(sess, [usage_row("s0", 0, old, I=1, content=bash("python order.py take N001", "c1")), result_row(1, old, "N001 TAKEN — STATE RUNNING", "c1"),
                           usage_row("s1", 2, old, I=500)])
        write_jsonl(sess.with_suffix("") / "subagents" / "agent-f1.jsonl",
                    [{"type": "user", "timestamp": ts(2), "cwd": str(old), "message": {"content": "review N001"}}, usage_row("f1", 3, old, R=9000)])
        res = self.scan_with(home, "N001", {"N001"})
        self.assertEqual((res["lead"], res["agents"]), ([], []))

    def test_long_claude_home_path(self) -> None:
        import os as _os
        deep = self.p.tmp / ("h" * 60) / ("i" * 60) / ("j" * 60) / ("k" * 60)
        if _os.name == "nt":
            _os.makedirs(transcripts._fs(deep / "projects"), exist_ok=True)
        else:
            deep.mkdir(parents=True)
        target = deep / "projects" / transcripts.slug(self.p.tmp.resolve()) / "s1.jsonl"
        self.assertGreater(len(str(target)), 260)
        _os.makedirs(transcripts._fs(target.parent), exist_ok=True)
        with open(transcripts._fs(target), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.sess.read_text(encoding="utf-8"))
        self.assertEqual([(c["requests"], c["units"]) for c in self.scan_with(deep, "N001")["lead"]], [(3, 5870)])

    def test_agent_only_session_prompt_first_and_per_run_cd(self) -> None:
        """rung 3 #1, #3, #5: a session that only spawned an agent named for N001 is read; the prompt outranks the description;
        a command with two cd + order.py pairs counts only the run inside this project."""
        d, home = self.p.dir, self.p.tmp / "home7"
        side = home / "projects" / transcripts.slug(d.resolve()) / "side.jsonl"
        write_jsonl(side, [{"type": "user", "timestamp": ts(0), "cwd": str(d), "message": {"content": "help with the review"}},
                           usage_row("y0", 1, d, I=1)])
        write_jsonl(side.with_suffix("") / "subagents" / "agent-q1.jsonl",
                    [{"type": "user", "timestamp": ts(1), "cwd": str(d), "message": {"content": "Execute order N001; compare later"}},
                     usage_row("q1", 2, d, R=1000, model="claude-sonnet-4")])
        (side.with_suffix("") / "subagents" / "agent-q1.meta.json").write_text(
            json.dumps({"agentType": "Explore", "description": "compare with N002", "toolUseId": "tq"}), encoding="utf-8")
        res = self.scan_with(home, "N001", {"N001", "N002"})
        self.assertEqual([(a["agent_id"], a["why"]) for a in res["agents"]], [("q1", "named N001")])
        other = self.p.tmp / "other"
        main = home / "projects" / transcripts.slug(d.resolve()) / "two.jsonl"
        cmd = f'cd "{d}" && python order.py take N001; cd "{other}" && python order.py take N002'
        write_jsonl(main, [usage_row("t0", 0, d, I=1, content=bash(cmd, "w1")),
                           result_row(1, d, "N001 TAKEN — STATE RUNNING\nN002 TAKEN — STATE RUNNING", "w1"), usage_row("t1", 2, d, I=9)])
        self.assertEqual([(c["requests"], c["input"]) for c in self.scan_with(home, "N001", {"N001", "N002"})["lead"]], [(1, 9)])
        self.assertEqual(self.scan_with(home, "N002", {"N001", "N002"})["lead"], [])  # N002 was taken in another project

    def test_lead_estimate_gives_way_the_same_or_next_day_only(self) -> None:
        """r4: a typed Lead estimate from the measured days or the day after is superseded; two days after, it stays."""
        last = datetime(2026, 10, 11, 23, 50).timestamp()
        c = {"session": "s", "context_last": 100, "tool_uses": 0, "duration_ms": 0, "units": 10, "requests": 1, "models": [],
             "input": 1, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 0, "first_t": last - 600, "last_t": last}
        runs = []
        for day in ("2026-10-11", "2026-10-12", "2026-10-13"):
            r = usage.make_run("lead", 50000, kind="estimate", src="session-end"); r["when"] = f"{day} 00:30"; runs.append(r)
        d = usage.merge_scan({"order": "N001", "runs": runs}, {"lead": [c], "agents": [], "sessions": ["s"]})
        kept = [r["when"][:10] for r in d["runs"] if r.get("kind") == "estimate" and not r.get("superseded")]
        self.assertEqual(kept, ["2026-10-13"])

    def test_covers_a_round_of_a_continued_agent(self) -> None:
        a = {"round_ends": [40000, 60000, 90000], "context_last": 90000}
        self.assertTrue(usage.covers(a, 61000))       # round 2 reported 61k, its last request in the transcript reads 60k
        self.assertFalse(usage.covers(a, 75000))      # no request near 75k: another agent's figure
        self.assertFalse(usage.covers(a, 35000))      # the transcript never reads ABOVE a reported figure by more than 2%


if __name__ == "__main__":
    unittest.main()
