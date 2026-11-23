"""0.9.3 tests (R-37: Claude tokens per order). Every check is tested both ways: the case it must catch and the case it must
leave alone (negative control) - a wrong booking is worse than no booking."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import PLUGIN, PY, TOOLS, Project  # noqa: E402

sys.path.insert(0, str(TOOLS))
import usage  # noqa: E402

RU = "# Отчёт\n\nЭтот документ написан на русском языке для владельца проекта.\n"


def hook(p: Project, payload, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the usage hook the way Claude Code does: JSON on stdin, cwd = the session folder."""
    body = payload if isinstance(payload, str) else json.dumps({"cwd": str(cwd or p.dir), **payload})
    return subprocess.run([PY, str(TOOLS / "usage.py"), "--hook"], cwd=cwd or p.dir, input=body, capture_output=True,
                          text=True, encoding="utf-8")


def agent_done(prompt: str, tokens: int = 40685, tools: int = 8, aid: str = "a1", who: str = "forge:verifier2") -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_input": {"prompt": prompt, "subagent_type": who},
            "tool_response": {"status": "completed", "agentId": aid, "agentType": who, "totalTokens": tokens,
                              "totalToolUseCount": tools, "totalDurationMs": 135916}}


def transcript(path: Path, turns: list[tuple[int, int]], start_minute: int = 0) -> None:
    """Synthetic subagent transcript in Claude Code's shape: per turn (context tokens, tool uses), appended."""
    rows = [{"type": "user", "timestamp": f"2026-10-11T10:{start_minute:02d}:00.000Z", "message": {"role": "user", "content": "task"}}]
    for i, (ctx, tools) in enumerate(turns):
        content = [{"type": "tool_use", "id": f"t{i}{k}", "name": "Read", "input": {}} for k in range(tools)] or [{"type": "text", "text": "ok"}]
        rows.append({"type": "assistant", "timestamp": f"2026-10-11T10:{start_minute:02d}:{10 + i:02d}.000Z",
                     "message": {"id": f"m{start_minute}{i}", "content": content,
                                 "usage": {"input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": ctx - 112, "output_tokens": 10}}})
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))


class Numbers(unittest.TestCase):
    def test_parse_tokens(self) -> None:
        for raw, want in (("40685", 40685), ("41k", 41000), ("~41k", 41000), ("1.2M", 1200000), ("1,2M", 1200000), ("150 000", 150000),
                          ("150,000", 150000), ("150.000", 150000), (40685, 40685), ("400к", 400000)):
            self.assertEqual(usage.parse_tokens(raw), want, raw)
        for raw in ("-", "", "abc", "1.5", "12,34", None, -5, True):  # negative controls: never a silent wrong number
            self.assertIsNone(usage.parse_tokens(raw), raw)

    def test_parse_block(self) -> None:
        self.assertEqual(usage.parse_block("<usage>subagent_tokens: 40685\ntool_uses: 8\nduration_ms: 135916</usage>"),
                         {"tokens": 40685, "tool_uses": 8, "duration_ms": 135916})
        self.assertEqual(usage.parse_block('{"totalDurationMs":135916,"totalTokens":40685,"totalToolUseCount":8}'),
                         {"tokens": 40685, "tool_uses": 8, "duration_ms": 135916})
        self.assertEqual(usage.parse_block("total_tokens: 1,234")["tokens"], 1234)
        self.assertNotIn("tokens", usage.parse_block('"output_tokens": 5, "input_tokens": 3'))  # API usage fields are not a total


class Ledger(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()
        for t in ("A", "B", "C"):
            self.p.order("new", t, "--tier", "LOCAL", "--changes", t)
        self.p.order("take", "N001")  # current order

    def tearDown(self) -> None:
        self.p.cleanup()

    def runs(self, oid: str) -> list[dict]:
        f = self.p.circle / "answers" / f"{oid}_usage.json"
        return json.loads(f.read_text(encoding="utf-8"))["runs"] if f.is_file() else []

    def unassigned(self) -> list[dict]:
        f = self.p.state / "cache" / "usage_unassigned.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []

    def close(self, oid: str, *extra: str) -> subprocess.CompletedProcess:
        d = self.p.read / f"{oid}_a.md"; d.write_text(RU, encoding="utf-8", newline="\n")
        self.p.order("answer", oid, "--executor", "ollama:x", "--summary", "s", "--files", d.relative_to(self.p.dir).as_posix(), *extra)
        self.p.order("report", oid, "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        return self.p.order("close", oid)

    def test_foreground_agent_is_booked_to_the_named_open_order(self) -> None:
        r = hook(self.p, agent_done("Review order N002: read circle/orders/N002_b.md"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))  # silent, never fails
        self.assertEqual([(x["who"], x["tokens"], x["tool_uses"], x["kind"]) for x in self.runs("N002")], [("forge:verifier2", 40685, 8, "measured")])
        self.assertEqual(self.runs("N001"), [])  # the current order is NOT charged for an agent that names another one
        hook(self.p, agent_done("Review order N002 again", aid="a1"))  # the same agent result delivered twice
        self.assertEqual(len(self.runs("N002")), 1)
        hook(self.p, agent_done("summarise the folder", tokens=5000, aid="a2"))  # no order named -> the current TAKEN order
        self.assertEqual([x["tokens"] for x in self.runs("N001")], [5000])

    def test_parallel_agents_finishing_together_are_all_booked(self) -> None:
        n = 12
        procs = [subprocess.Popen([PY, str(TOOLS / "usage.py"), "--hook"], cwd=self.p.dir, stdin=subprocess.PIPE, text=True, encoding="utf-8")
                 for _ in range(n)]
        for i, pr in enumerate(procs):  # n hooks write one ledger at the same moment (Lead launched n agents in parallel)
            pr.stdin.write(json.dumps({"cwd": str(self.p.dir), **agent_done("stage of N002", tokens=1000 + i, aid=f"p{i}")})); pr.stdin.close()
        self.assertEqual([pr.wait(timeout=60) for pr in procs], [0] * n)
        err = self.p.state / "cache" / "usage_hook_error.txt"
        self.assertEqual(sorted(x["tokens"] for x in self.runs("N002")), [1000 + i for i in range(n)],  # no lost update
                         err.read_text(encoding="utf-8") if err.exists() else "no hook error recorded")
        self.assertEqual(list((self.p.state / "cache").glob("*.lock")), [])  # locks are released

    def test_lock_treats_windows_permission_error_as_busy(self) -> None:
        """N020: while the previous holder's lock file is being deleted, Windows answers PermissionError - retry, never fail."""
        cfg = usage.load_cfg(self.p.dir)
        real, calls = usage.os.open, []

        def flaky(path, flags, *a):
            calls.append(path)
            if len(calls) <= 2:
                raise PermissionError(13, "delete pending")
            return real(path, flags, *a)
        usage.os.open = flaky
        try:
            with usage._lock(cfg, "N002_usage.json"):
                held = (self.p.state / "cache" / "N002_usage.json.lock").exists()
        finally:
            usage.os.open = real
        self.assertEqual((len(calls), held), (3, True))  # two refusals, then the lock - not an exception, not a lock-less write
        import inspect
        self.assertLess(2 * inspect.signature(usage._lock).parameters["wait"].default, 10)  # nested agents+ledger locks < hook timeout
        self.assertFalse((self.p.state / "cache" / "N002_usage.json.lock").exists())

    def test_hook_waits_for_the_ledger_lock(self) -> None:
        """Deterministic half of the race test: while the ledger is locked the hook does not write it."""
        cfg = usage.load_cfg(self.p.dir)
        with usage._lock(cfg, "N002_usage.json"):
            pr = subprocess.Popen([PY, str(TOOLS / "usage.py"), "--hook"], cwd=self.p.dir, stdin=subprocess.PIPE, text=True, encoding="utf-8")
            pr.stdin.write(json.dumps({"cwd": str(self.p.dir), **agent_done("N002 stage", aid="w1")})); pr.stdin.close()
            agents_lock = self.p.state / "cache" / "usage_agents.json.lock"
            for _ in range(200):  # the hook is inside (holds the agents map) and now waits for the ledger
                if agents_lock.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(agents_lock.exists())
            time.sleep(0.5)
            self.assertEqual(self.runs("N002"), [])  # negative control: nothing written while the lock is held
        self.assertEqual(pr.wait(timeout=30), 0)
        self.assertEqual([x["tokens"] for x in self.runs("N002")], [40685])

    def test_order_resolution_in_a_shared_folder(self) -> None:
        p = self.p
        hook(p, agent_done("Review per circle/orders/N002_b.md and the DoD there", tokens=1111, aid="n1"))  # a file name only
        self.assertEqual([x["tokens"] for x in self.runs("N002")], [1111])
        hook(p, agent_done("Check N003 against what N001 did", tokens=2222, aid="n2"))  # two open orders: the first named
        self.assertEqual([x["tokens"] for x in self.runs("N003")], [2222])
        self.assertEqual(self.runs("N001"), [])  # the current order is not preferred over the named subject
        hook(p, {**agent_done("work on N002", tokens=3333, aid="n3"), "session_id": "s1"})
        hook(p, {**agent_done("now summarise the notes", tokens=4444, aid="n4"), "session_id": "s1"})  # unnamed: s1 works on N002
        self.assertEqual([x["tokens"] for x in self.runs("N002")], [1111, 3333, 4444])
        hook(p, {**agent_done("summarise", tokens=5555, aid="n5"), "session_id": "s2"})  # another session, nothing named -> current
        self.assertEqual([x["tokens"] for x in self.runs("N001")], [5555])
        p.order("take", "N003")  # two orders TAKEN (LOCAL tiers may run alongside): an unnamed agent is not guessed
        hook(p, agent_done("summarise", tokens=6666, aid="n6"))
        self.assertEqual(self.unassigned()[-1]["tokens"], 6666); self.assertIn("several orders TAKEN", self.unassigned()[-1]["why"])

    def test_stale_current_order_is_not_charged(self) -> None:
        p = self.p  # N001 TAKEN three days ago, e.g. by a session that never closed it
        j = next((p.circle / "journal").glob("*.md"))
        old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        j.rename(j.with_name(f"{old}.md"))
        hook(p, agent_done("summarise", tokens=7777, aid="s1"))
        self.assertEqual(self.runs("N001"), [])
        self.assertIn("taken over 24 h ago", self.unassigned()[-1]["why"])
        (p.state / "session_log.md").write_text("# Session log\n\n| date | done | tokens / limits | blockers |\n|---|---|---|---|\n",
                                                encoding="utf-8", newline="\n")
        self.assertIn("TAKEN order(s) N001 taken over 24 h ago", p.step("end", "--did", "x", "--tokens", "100k").stdout)
        self.assertEqual(self.runs("N001"), [])
        hook(p, agent_done("finish N001", tokens=8888, aid="s2"))  # named explicitly: booked even though stale
        self.assertEqual([x["tokens"] for x in self.runs("N001")], [8888])

    def test_equal_numbers_retries_and_nearest_approx(self) -> None:
        p = self.p
        hook(p, agent_done("N002 part 1", tokens=5000, tools=2, aid="e1"))
        hook(p, agent_done("N002 part 2", tokens=5000, tools=2, aid="e2"))  # two agents with equal numbers are two agents
        self.assertEqual(len(self.runs("N002")), 2)
        p.order("usage", "N002", "--who", "lead", "--tokens", "70k")
        p.order("usage", "N002", "--who", "lead", "--tokens", "70k")  # a retried command does not double the Lead's share
        self.assertEqual([x["tokens"] for x in self.runs("N002") if x["kind"] == "estimate"], [70000])
        cfg = usage.load_cfg(p.dir)
        for i, t in enumerate((38000, 40000)):
            usage.add_run(cfg, "N003", usage.make_run("bg", t, src="hook:subagent-stop", tool_uses=3, key=f"b{i}#0", approx=True))
        out = p.order("usage", "N003", "--who", "bg", "--usage", "total_tokens: 40100 tool_uses: 3").stdout
        self.assertIn("replaces the hook's approximate 40k", out)
        self.assertEqual(sorted((x["tokens"], bool(x.get("approx"))) for x in self.runs("N003")), [(38000, True), (40100, False)])

    def test_rung3_fixes(self) -> None:
        """ChatGPT audit of N019 (rung 3): each case fails on the r2 code."""
        p = self.p
        r = {"hook_event_name": "PostToolUse", "tool_name": "Agent", "session_id": "s9",
             "tool_input": {"description": "Compare with N003", "prompt": "Execute order N002", "subagent_type": "x"},
             "tool_response": {"status": "completed", "agentId": "g1", "totalTokens": 1234}}
        hook(p, r)  # #4: the brief (prompt) names the subject, the short description only a reference
        self.assertEqual([x["tokens"] for x in self.runs("N002")], [1234]); self.assertEqual(self.runs("N003"), [])
        hook(p, {**agent_done("Compare N002 with N003", tokens=2345, aid="g2"), "session_id": "s8"})  # two named: not the session's subject
        hook(p, {**agent_done("summarise", tokens=3456, aid="g3"), "session_id": "s8"})
        self.assertEqual([x["tokens"] for x in self.runs("N001")], [3456])  # falls to the fresh current order, not to N002
        p.order("usage", "N003", "--who", "a", "--tokens", "5k"); p.order("usage", "N003", "--who", "b", "--tokens", "5k")  # #3
        self.assertEqual(len(self.runs("N003")), 2)  # two bare figures for two agents stay two runs
        blk = "<usage>total_tokens: 7000\ntool_uses: 2</usage>"
        p.order("usage", "N003", "--who", "c", "--usage", blk); p.order("usage", "N003", "--who", "c", "--usage", blk)
        self.assertEqual(len(self.runs("N003")), 3)  # the same block pasted twice counts once
        cfg = usage.load_cfg(p.dir)
        usage.add_run(cfg, "N003", usage.make_run("Explore", 40000, src="hook:subagent-stop", key="bx#0", approx=True))
        out = p.order("usage", "N003", "--who", "verifier2", "--tokens", "40100").stdout
        self.assertNotIn("replaces", out)  # another agent's figure never overwrites this one
        self.assertIn("replaces", p.order("usage", "N003", "--who", "Explore", "--tokens", "40300").stdout)
        f = p.circle / "answers" / "N003_usage.json"; d = json.loads(f.read_text(encoding="utf-8"))
        d["runs"].append(usage.make_run("lead", 150000, kind="estimate", src="session-end")); d["runs"][-1]["when"] = "2026-01-01 10:00"
        f.write_text(json.dumps(d), encoding="utf-8", newline="\n")
        p.order("usage", "N003", "--who", "lead", "--tokens", "150k")  # the same figure on another day = another session
        self.assertEqual(sum(1 for x in self.runs("N003") if x["kind"] == "estimate"), 2)

    def test_snapshot_after_close_period_report_and_unassigned_only(self) -> None:
        p = self.p
        hook(p, agent_done("N001 work", tokens=90000, aid="h1"))
        out = self.close("N001").stdout
        self.assertIn("the Lead's own share is not recorded", out)  # rung 3 #2: the gap is visible at close
        p.order("usage", "N001", "--who", "lead", "--tokens", "60k")  # booked after the close (as step.py end does)
        d = json.loads((p.circle / "answers" / "N001_usage.json").read_text(encoding="utf-8"))
        self.assertEqual((d["closed"]["total"], d["closed"]["lead_estimate"]), (150000, 60000)); self.assertIn("updated", d["closed"])  # #6
        f = p.circle / "answers" / "N001_usage.json"; d["runs"][0]["when"] = "2026-01-01 10:00"
        f.write_text(json.dumps(d), encoding="utf-8", newline="\n")
        week = subprocess.run([PY, str(TOOLS / "usage.py"), "report", "--days", "7"], cwd=p.dir, capture_output=True, text=True, encoding="utf-8").stdout
        self.assertRegex(week, r"\| N001 \| CLOSED \| - \| 60k \|")  # #7: the January run is outside the 7-day period
        q = Project()
        try:
            hook(q, agent_done("no order named", tokens=4000, aid="u9"))  # nothing TAKEN in q: unassigned, and no ledger anywhere
            rep = subprocess.run([PY, str(TOOLS / "usage.py"), "report"], cwd=q.dir, capture_output=True, text=True, encoding="utf-8").stdout
            self.assertIn("not measured", rep); self.assertIn("Unassigned (no open order named in the prompt): 1 run(s), 4k", rep)  # #8
        finally:
            q.cleanup()

    def test_review3_fixes(self) -> None:
        """verifier2 round 3 of N019: each case fails on the round-3 code."""
        p = self.p
        cfg = usage.load_cfg(p.dir)
        p.order("amend", "N002", "--why", "e", "--estimate", "450k")
        old = usage.make_run("x", 390000); old["when"] = "2026-01-01 10:00"
        usage.add_run(cfg, "N002", old); usage.add_run(cfg, "N002", usage.make_run("y", 60000))
        run = lambda *a: subprocess.run([PY, str(TOOLS / "usage.py"), "report", *a], cwd=p.dir, capture_output=True, text=True, encoding="utf-8").stdout  # noqa: E731
        week, full = run("--days", "7"), run()
        self.assertRegex(week, r"\| N002 \| OPEN \| - \| 60k \|.*\| 450k \| \(order spans the period\) \|")  # #1: no x0.13
        self.assertNotIn("off by more than 2x: N002", week)
        self.assertRegex(full, r"\| N002 \| OPEN \| - \| 450k \|.*\| 450k \| x1\.00 \(tokens\) \|")
        # #2: a background agent launched without a type takes SubagentStop's agent_type; an unnamed paste never picks one of several
        tr = p.dir / "agent-t1.jsonl"; transcript(tr, [(30000, 1)])
        hook(p, {"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_input": {"prompt": "N003 research"},
                 "tool_response": {"status": "async_launched", "agentId": "t1"}})
        hook(p, {"hook_event_name": "SubagentStop", "agent_id": "t1", "agent_type": "Explore", "agent_transcript_path": str(tr)})
        self.assertEqual([(x["who"], x["tokens"]) for x in self.runs("N003")], [("Explore", 30000)])
        usage.add_run(cfg, "N003", usage.make_run("Plan", 31000, src="hook:subagent-stop", key="t2#0", approx=True))
        self.assertNotIn("replaces", p.order("usage", "N003", "--tokens", "30500").stdout)  # no --who, two candidates: appended
        self.assertEqual(sum(1 for x in self.runs("N003") if x.get("approx")), 2)
        self.assertIn("replaces the hook's approximate 31k (Plan)", p.order("usage", "N003", "--who", "Plan", "--tokens", "31800").stdout)
        # r4 A: a hand-entered figure equal to a hook run of ANOTHER agent is another run (negative control of the identity check)
        hook(p, agent_done("N001 review", tokens=40685, aid="v9", who="forge:verifier2"))
        self.assertNotIn("not added twice", p.order("usage", "N001", "--who", "Explore", "--tokens", "40685").stdout)
        self.assertIn("not added twice", p.order("usage", "N001", "--who", "verifier2", "--tokens", "40685").stdout)  # same agent
        self.assertEqual(sorted(x["who"] for x in self.runs("N001")), ["Explore", "forge:verifier2"])
        # #3: lead and lead:claude are one actor for the estimate dedupe
        p.order("usage", "N001", "--who", "lead:claude", "--tokens", "120k"); p.order("usage", "N001", "--who", "lead", "--tokens", "120k")
        self.assertEqual([x["tokens"] for x in self.runs("N001") if x["kind"] == "estimate"], [120000])

    def test_step_end_after_close_is_journaled(self) -> None:
        p = self.p
        (p.state / "session_log.md").write_text("# Session log\n\n| date | done | tokens / limits | blockers |\n|---|---|---|---|\n",
                                                encoding="utf-8", newline="\n")
        hook(p, agent_done("N001 work", tokens=90000, aid="j1"))
        self.close("N001")
        p.step("end", "--did", "x", "--tokens", "60k")  # N001 closed today is the only candidate
        jtext = next((p.circle / "journal").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("[N001] usage: Lead estimate 60k booked at session end (after the close: total now 150k)", jtext)  # r3 #4

    def test_step_end_considers_every_taken_order(self) -> None:
        p = self.p
        (p.state / "session_log.md").write_text("# Session log\n\n| date | done | tokens / limits | blockers |\n|---|---|---|---|\n",
                                                encoding="utf-8", newline="\n")
        p.order("take", "N002")  # N001 current, N002 TAKEN alongside (LOCAL tiers): who owns the Lead's tokens is unknown
        self.assertIn("several orders could own them (N001, N002)", p.step("end", "--did", "x", "--tokens", "90k").stdout)  # rung 3 #5
        self.assertEqual(self.runs("N001") + self.runs("N002"), [])

    def test_hook_reads_utf8_whatever_the_console_code_page(self) -> None:
        p = self.p
        # a legacy console code page with strict decoding: `sys.stdin.read()` raised UnicodeDecodeError here (0x8f, checked
        # 2026-10-11 - the 0.9.3 r1 read); Python 3.12's default for a pipe is cp1251 + surrogateescape, which hides it
        env = {**{k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}, "PYTHONIOENCODING": "cp1252:strict"}
        body = json.dumps({"cwd": str(p.dir), **agent_done("Проверка наряда N002: Итоги, Анализ", tokens=9999, aid="u1")}, ensure_ascii=False)
        r = subprocess.run([PY, str(TOOLS / "usage.py"), "--hook"], cwd=p.dir, input=body.encode("utf-8"), capture_output=True, env=env)
        self.assertEqual((r.returncode, r.stdout), (0, b""))
        self.assertEqual([x["tokens"] for x in self.runs("N002")], [9999])
        self.assertFalse((p.state / "cache" / "usage_hook_error.txt").exists())

    def test_closed_or_no_order_goes_to_unassigned(self) -> None:
        self.close("N001")
        hook(self.p, agent_done("audit what N001 did", tokens=7000, aid="a3"))  # only a closed order named
        hook(self.p, agent_done("audit what N001 did", tokens=7000, aid="a3"))  # the same result twice: logged once
        hook(self.p, agent_done("no order here", tokens=8000, aid="a4"))        # nothing named, nothing TAKEN
        self.assertEqual([x["tokens"] for x in self.runs("N001")], [])
        self.assertEqual([x["tokens"] for x in self.unassigned()], [7000, 8000])
        self.assertIn("closed order(s) N001", self.unassigned()[0]["why"])

    def test_background_agent_is_booked_at_subagent_stop(self) -> None:
        p = self.p
        tr = p.dir / "agent-b1.jsonl"
        launch = {"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_input": {"prompt": "build N003 stage 2"},
                  "tool_response": {"status": "async_launched", "agentId": "b1", "description": "stage 2"}}
        hook(p, launch)
        self.assertEqual(self.runs("N003"), [])  # nothing known yet
        hook(p, {"hook_event_name": "SubagentStop", "agent_id": "zz", "agent_transcript_path": str(tr)})  # unknown agent -> ignored
        transcript(tr, [(20000, 2), (30000, 1), (38000, 0)])
        stop = {"hook_event_name": "SubagentStop", "agent_id": "b1", "agent_type": "general-purpose", "agent_transcript_path": str(tr)}
        hook(p, stop)
        runs = self.runs("N003")
        self.assertEqual([(x["tokens"], x["tool_uses"], x.get("approx")) for x in runs], [(38000, 3, True)])
        hook(p, stop)  # the same stop again: nothing new in the transcript
        self.assertEqual(len(self.runs("N003")), 1)
        out = p.order("usage", "N003", "--who", "general-purpose", "--usage", "<usage>total_tokens: 40100\ntool_uses: 3\nduration_ms: 21000</usage>").stdout
        self.assertIn("replaces the hook's approximate", out)
        self.assertEqual([(x["tokens"], x.get("approx")) for x in self.runs("N003")], [(40100, None)])  # one agent, counted once
        transcript(tr, [(52000, 4)], start_minute=5)  # a continuation (SendMessage) of the same agent
        hook(p, stop)
        self.assertEqual([x["tokens"] for x in self.runs("N003")], [40100, 52000])

    def test_continuation_of_a_foreground_agent(self) -> None:
        p = self.p
        main = p.dir / "sess.jsonl"
        sub = p.dir / "sess" / "subagents" / "agent-f1.jsonl"; sub.parent.mkdir(parents=True)
        transcript(sub, [(30000, 2)])
        hook(p, {**agent_done("check N002", tokens=30500, tools=2, aid="f1"), "transcript_path": str(main)})
        stop = {"hook_event_name": "SubagentStop", "agent_id": "f1", "agent_transcript_path": str(sub)}
        hook(p, stop)  # stop after the result (either order of events): the run is already booked exactly
        self.assertEqual([x["tokens"] for x in self.runs("N002")], [30500])
        transcript(sub, [(45000, 1)], start_minute=3)
        hook(p, stop)
        self.assertEqual([x["tokens"] for x in self.runs("N002")], [30500, 45000])

    def test_manual_paste_is_not_counted_twice_and_bad_input_refused(self) -> None:
        p = self.p
        hook(p, agent_done("review N002"))
        out = p.order("usage", "N002", "--who", "verifier2", "--usage", "<usage>subagent_tokens: 40685\ntool_uses: 8\nduration_ms: 135916</usage>").stdout
        self.assertIn("not added twice", out)
        self.assertEqual(len(self.runs("N002")), 1)
        r = p.order("usage", "N002", "--who", "verifier2", "--usage", "no numbers here", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("no token count", r.stdout + r.stderr)
        r = p.order("usage", "N002", "--tokens", "lots", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertEqual(len(self.runs("N002")), 1)
        r = p.order("usage", "N099", "--tokens", "5k", ok=False)
        self.assertNotEqual(r.returncode, 0)

    def test_hook_never_fails(self) -> None:
        p = self.p
        for bad in ("not json", "{}", json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_response": "oops"})):
            r = hook(p, bad)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        outside = p.tmp / "elsewhere"; outside.mkdir()
        r = hook(p, agent_done("N001"), cwd=outside)  # not a Forge project: nothing written anywhere
        self.assertEqual((r.returncode, r.stdout), (0, "")); self.assertEqual(self.runs("N001"), [])
        (p.circle / "answers" / "N001_usage.json").write_text("{broken", encoding="utf-8")
        r = hook(p, agent_done("N001", aid="c9"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual([x["tokens"] for x in self.runs("N001")], [40685])  # a broken ledger is replaced, not a crash

    def test_close_reports_total_and_estimate_ratio(self) -> None:
        p = self.p
        p.order("amend", "N001", "--why", "estimate set", "--estimate", "100k")
        hook(p, agent_done("review N001", tokens=90000, aid="r1"))
        p.order("usage", "N001", "--who", "lead", "--tokens", "60k")  # the Lead's own share = an estimate
        out = self.close("N001").stdout
        self.assertIn("N001 tokens 150k (agents 90k in 1 run(s) + Lead est. 60k) vs estimate 100k = x1.50", out)
        d = json.loads((p.circle / "answers" / "N001_usage.json").read_text(encoding="utf-8"))
        self.assertEqual((d["closed"]["total"], d["closed"]["ratio"]), (150000, 1.5))
        self.assertRegex((p.circle / "journal").glob("*.md").__next__().read_text(encoding="utf-8"), r"\[N001\] usage: tokens 150k")
        self.assertEqual(p.red(), [])  # the ledger passes every gate

    def test_close_without_data_only_hints(self) -> None:
        out = self.close("N001").stdout
        self.assertIn("N001 tokens: none recorded", out)
        self.assertFalse((self.p.circle / "answers" / "N001_usage.json").exists())  # no empty ledger = no fake zero

    def test_estimate_sources_and_off_by_2x(self) -> None:
        p = self.p
        p.order("new", "D", "--tier", "LOCAL", "--changes", "d", "--estimate", "1.2M")
        self.assertIn("**Token estimate:** 1.2M", next((p.circle / "orders").glob("N004_*.md")).read_text(encoding="utf-8"))
        cfg = usage.load_cfg(p.dir)
        self.assertEqual(usage.estimate_of(cfg, "N004"), 1200000)
        p.order("amend", "N004", "--why", "re-planned", "--estimate", "2M")
        self.assertEqual(usage.estimate_of(cfg, "N004"), 2000000)  # the last amendment wins
        (p.circle / "plans").mkdir(exist_ok=True)
        (p.circle / "plans" / "cycle.json").write_text(json.dumps({"cycle": "N002", "estimate_tokens": "300k", "files": []}), encoding="utf-8")
        self.assertEqual(usage.estimate_of(cfg, "N002"), 300000)  # a swarm manifest carries the cycle's estimate
        self.assertIsNone(usage.estimate_of(cfg, "N003"))
        r = p.order("new", "E", "--changes", "e", "--estimate", "a lot", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("--estimate", r.stdout + r.stderr)
        s = usage.summary({"runs": [usage.make_run("x", 900000)]}, 300000)
        self.assertIn("off by more than 2x", usage.line("N002", s))
        self.assertNotIn("off by", usage.line("N002", usage.summary({"runs": [usage.make_run("x", 400000)]}, 300000)))

    def test_answer_and_done_take_usage(self) -> None:
        p = self.p
        d = p.read / "N001_a.md"; d.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "executor-fast", "--summary", "s", "--files", d.relative_to(p.dir).as_posix(),
                "--usage", "<usage>subagent_tokens: 12000\ntool_uses: 3\nduration_ms: 9000</usage>")
        self.assertEqual([(x["who"], x["tokens"], x["kind"]) for x in self.runs("N001")], [("executor-fast", 12000, "measured")])
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        p.order("take", "N002")
        p.order("done", "N002", "--text", "ok", "--executor", "lead:claude", "--summary", "s", "--tokens", "30k", "--by", "owner", "--owner-text", "ok")
        self.assertEqual([(x["who"], x["tokens"], x["kind"]) for x in self.runs("N002")], [("lead:claude", 30000, "estimate")])

    def test_step_end_books_the_lead_estimate_only_when_unambiguous(self) -> None:
        p = self.p
        (p.state / "session_log.md").write_text("# Session log\n\n| date | done | tokens / limits | blockers |\n|---|---|---|---|\n", encoding="utf-8", newline="\n")
        out = p.step("end", "--did", "x", "--tokens", "150k").stdout  # N001 is the only candidate (TAKEN, nothing closed today)
        self.assertIn("N001: +150k tokens (lead, estimate)", out)
        self.close("N001"); p.order("take", "N002")
        out = p.step("end", "--did", "y", "--tokens", "90k").stdout  # N002 current AND N001 closed today -> never guess
        self.assertIn("several orders could own them", out)
        self.assertEqual([x["tokens"] for x in self.runs("N002")], [])
        p.step("end", "--did", "z", "--tokens", "90k", "--order", "N002")
        self.assertEqual([(x["tokens"], x["kind"]) for x in self.runs("N002")], [(90000, "estimate")])
        self.assertIn("| 90k |", (p.state / "session_log.md").read_text(encoding="utf-8"))


class BriefReportHooks(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()

    def tearDown(self) -> None:
        self.p.cleanup()

    def test_brief_tokens_line_and_16_lines(self) -> None:
        p = self.p
        for i in range(14):
            p.order("new", f"Order {i}", "--tier", "LOCAL", "--changes", f"c{i}")
        b0 = p.step("--brief").stdout
        self.assertNotIn("tokens:", b0)  # negative control: no ledger -> no line
        p.order("take", "N001")
        hook(p, agent_done("work on N001", tokens=250000))
        b = p.step("--brief").stdout.splitlines()
        self.assertLessEqual(len(b), 16)
        self.assertTrue(any(x.startswith("  tokens: N001 so far 250k tok (1 run(s))") for x in b), b)
        self.assertTrue(any(x.startswith("  next:") for x in b))  # a long order list no longer pushes `next:` out

    def test_report_for_the_audit(self) -> None:
        p = self.p
        r = p.order("new", "A", "--tier", "LOCAL", "--changes", "a", "--estimate", "100k")
        self.assertEqual(r.returncode, 0)
        empty = subprocess.run([PY, str(TOOLS / "usage.py"), "report"], cwd=p.dir, capture_output=True, text=True, encoding="utf-8").stdout
        self.assertIn("not measured", empty)
        p.order("take", "N001")
        hook(p, agent_done("N001 review", tokens=310000))
        md = p.docs / "usage.md"
        subprocess.run([PY, str(TOOLS / "usage.py"), "report", "--md", str(md)], cwd=p.dir, check=True, capture_output=True)
        text = md.read_text(encoding="utf-8")
        self.assertRegex(text, r"\| N001 \| TAKEN \| - \| 310k \| 310k \(1\) \| - \| 100k \| x3\.10 \(tokens\) \|")
        self.assertIn("off by more than 2x: N001 x3.1", text)

    def test_verifier_recheck_rule_in_the_skills(self) -> None:
        """N021: the re-check rule lives where the Lead reads it before a second review round."""
        review = (PLUGIN / "skills" / "forge-review" / "SKILL.md").read_text(encoding="utf-8")
        rule = review.split("**Re-check after fixes (0.9.3):**", 1)
        self.assertEqual(len(rule), 2, "forge-review lost the re-check rule")
        text = rule[1].split("\n   **Brief", 1)[0]
        for must in ("under 5 minutes", "fresh `verifier2`", "previous review file", "fix list", "paths"):
            self.assertIn(must, text)
        for must in ("191 subagent runs", "4.7M units", "+15%", "time its last result reached you"):  # the measured reason, observable
            self.assertIn(must, text)
        done = (PLUGIN / "skills" / "forge-done" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("within 5 minutes of its last activity, else by a fresh one", done)

    def test_lean_research_agent_and_no_general_purpose(self) -> None:
        """N022: forge:researcher has an explicit, lean tool list and keeps the Lead's model; Forge flows never name general-purpose."""
        text = (PLUGIN / "agents" / "researcher.md").read_text(encoding="utf-8")
        head = text.split("---", 2)[1]
        meta = dict(ln.split(":", 1) for ln in head.strip().splitlines())
        self.assertEqual(meta["name"].strip(), "researcher")
        tools = [x.strip() for x in meta["tools"].split(",")]
        self.assertEqual(sorted(tools), sorted(["Read", "Grep", "Glob", "WebSearch", "WebFetch", "Write"]))
        self.assertFalse({"Bash", "Edit", "Agent", "Task"} & set(tools))  # lean and unable to edit beyond its one file
        self.assertNotIn("model", meta)  # inherits the Lead's model: no silent downgrade (owner's quality rule)
        self.assertIn("Write only that file", text)
        for skill in ("forge-swarm", "forge-route", "forge-review"):
            body = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertRegex(body, r"never (a )?`general-purpose`", f"{skill} must forbid general-purpose")
            # every mention is that prohibition: with the prohibitions taken out, the word is gone from the skill
            self.assertNotIn("general-purpose", re.sub(r"never (a )?`general-purpose`", "", body), f"{skill}: a mention that is not a ban")
            self.assertIn("~73k", body, f"{skill}: the ban carries its measured reason")
        for skill in ("forge-swarm", "forge-route"):
            self.assertIn("forge:researcher", (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("`researcher`", (PLUGIN / "templates" / "CLAUDE.project.md").read_text(encoding="utf-8"))

    def profile_run(self, *extra: str, ok: bool = True, skills: str | None = None, no_list: bool = False,
                    cwd: Path | None = None) -> subprocess.CompletedProcess:
        home = self.p.tmp / "home"
        inst = self.p.tmp / "plugins" / "toolkit"  # install key toolkit@market, manifest name "finance" (rung 3 #1)
        (inst / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (inst / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "finance"}), encoding="utf-8")
        for sk in ("variance-analysis", "close-management", "reconciliation"):
            (inst / "skills" / sk).mkdir(parents=True, exist_ok=True)
            (inst / "skills" / sk / "SKILL.md").write_text(f"---\nname: {sk}\ndescription: x\n---\n", encoding="utf-8")
        mixed = self.p.tmp / "plugins" / "mixed"  # skills AND an MCP server, none of its skills in the cards
        (mixed / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (mixed / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "mixed"}), encoding="utf-8")
        (mixed / ".mcp.json").write_text("{}", encoding="utf-8")
        (mixed / "skills" / "tool").mkdir(parents=True, exist_ok=True)
        (mixed / "skills" / "tool" / "SKILL.md").write_text("---\nname: tool\ndescription: x\n---\n", encoding="utf-8")
        conn = self.p.tmp / "plugins" / "connector"  # an MCP-only plugin: no skills, nothing for the skills map to judge
        (conn / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (conn / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "connector"}), encoding="utf-8")
        (conn / ".mcp.json").write_text("{}", encoding="utf-8")
        reg = home / ".claude" / "plugins" / "installed_plugins.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"version": 2, "plugins": {
            "toolkit@market": [{"scope": "user", "installPath": str(inst)}],
            "gsap-skills@gsap-skills": [{"scope": "user"}], "gsap-skills@mirror": [{"scope": "user"}],   # two marketplaces
            "other-proj@x": [{"scope": "project", "projectPath": str(self.p.tmp / "elsewhere")}],
            "connector@market": [{"scope": "user", "installPath": str(conn)}],
            "mixed@market": [{"scope": "user", "installPath": str(mixed)}]}}), encoding="utf-8")
        avail = self.p.tmp / "skills.txt"
        avail.write_text(skills if skills is not None else
                         "- operations:runbook: x\n- small-business:ad-manager: x\n- anthropic-skills:docx: x\n"
                         "- gsap-skills:gsap-core: x\n- forge:forge-done: x\n- 10k-digest: x\n", encoding="utf-8")
        args = [PY, str(TOOLS / "skills_map.py"), "profile", str(self.p.dir)] + ([] if no_list else ["--available-file", str(avail)])
        r = subprocess.run(args + ["--home", str(home), *extra], capture_output=True, text=True, encoding="utf-8", cwd=cwd)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def set_domains(self, value) -> None:
        cfg = json.loads((self.p.dir / "forge.json").read_text(encoding="utf-8"))
        if value is None:
            cfg.pop("domains", None)
        else:
            cfg["domains"] = value
        (self.p.dir / "forge.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8", newline="\n")

    def test_profile_keeps_what_the_domains_use_and_writes_no_settings(self) -> None:
        """N023: invest's cards name finance:variance-analysis and anthropic-skills:xlsx. finance comes from the registry under
        another install key; anthropic-skills is listed without xlsx and is still kept (a false keep is harmless, a false off
        is not); operations, small-business and both gsap keys are proposed off; another project's install is not seen."""
        p = self.p
        self.set_domains(["invest"])
        local = p.dir / ".claude" / "settings.local.json"
        local.parent.mkdir(parents=True)
        local.write_text(json.dumps({"enabledPlugins": {"gsap-skills@gsap-skills": True}}), encoding="utf-8")
        before = local.read_text(encoding="utf-8")
        out = self.profile_run().stdout
        self.assertIn("domains invest (forge.json domains)", out)
        led = json.loads((p.state / "skill_profile.json").read_text(encoding="utf-8"))
        keep = {k["plugin"]: k for k in led["keep"]}
        self.assertEqual(sorted(keep), ["anthropic-skills", "connector", "finance", "forge"])
        self.assertEqual(keep["forge"]["why"], "Forge itself")  # no card names forge: it is kept as the plugin itself (r2 #5)
        self.assertIn("brings MCP, no skills", keep["connector"]["why"])
        self.assertEqual((keep["finance"]["used"], keep["finance"]["skills"]), (["finance:variance-analysis"], 3))  # installPath skills
        self.assertEqual(keep["anthropic-skills"]["not_seen"], ["anthropic-skills:xlsx"])
        off = {o["plugin"]: o for o in led["off"]}
        self.assertEqual(sorted(off), ["gsap-skills", "mixed", "operations", "small-business"])  # other-proj: another project's install
        self.assertIn("it also brings MCP - check those before switching it off", off["mixed"]["why"])  # r2 new #1
        self.assertEqual(off["gsap-skills"]["keys"], ["gsap-skills@gsap-skills", "gsap-skills@mirror"])
        self.assertEqual(off["operations"]["keys"], ["operations@synced"]); self.assertIn("operations@synced", led["unknown_keys"])
        self.assertEqual(led["restore"]["set"], {"gsap-skills@gsap-skills": True})       # the local value it had before
        self.assertIn("operations@synced", led["restore"]["remove"])                     # absent before -> remove
        self.assertIn('"small-business@synced": false', out); self.assertIn("/context", out)
        self.assertEqual(local.read_text(encoding="utf-8"), before)  # the settings file is read, never written
        self.assertEqual(sorted(x.name for x in local.parent.iterdir()), ["settings.local.json"])

    def test_profile_typo_face_value_and_corrupt_ledger(self) -> None:
        """r2 new #2/#3 and #5: a typo among the domains withholds the snippet; without a skill list a registry set the cards
        name is kept at face value; an unreadable ledger is never replaced."""
        p = self.p
        self.set_domains(["invest", "nonsense"])
        out = self.profile_run().stdout
        self.assertIn("ignored in forge.json (not in domains.json): nonsense", out)
        self.assertIn("snippet withheld", out)
        led = json.loads((p.state / "skill_profile.json").read_text(encoding="utf-8"))
        self.assertEqual((led["snippet"]["enabledPlugins"], led["restore"]["remove"], led["restore"]["set"]), ({}, [], {}))
        (p.state / "skill_profile.json").unlink()
        self.set_domains(["motion"])
        self.profile_run(no_list=True)
        led = json.loads((p.state / "skill_profile.json").read_text(encoding="utf-8"))
        gsap = next(k for k in led["keep"] if k["plugin"] == "gsap-skills")  # registry entries without an installPath: no skills seen
        self.assertIn("gsap-skills:gsap-core", gsap["used"])                  # the cards' names at face value
        (p.state / "skill_profile.json").write_text("{broken", encoding="utf-8")
        r = self.profile_run(ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("cannot be read", r.stdout + r.stderr)
        self.assertEqual((p.state / "skill_profile.json").read_text(encoding="utf-8"), "{broken")

    def test_profile_restore_survives_the_applied_snippet(self) -> None:
        """r2 #1: after the owner applied the snippet, the local file holds false - the restore record keeps the earlier true."""
        p = self.p
        self.set_domains(["invest"])
        local = p.dir / ".claude" / "settings.local.json"
        local.parent.mkdir(parents=True)
        local.write_text(json.dumps({"enabledPlugins": {"gsap-skills@gsap-skills": True}}), encoding="utf-8")
        self.profile_run()
        local.write_text(json.dumps({"enabledPlugins": {"gsap-skills@gsap-skills": False, "operations@synced": False}}), encoding="utf-8")
        self.profile_run()
        led = json.loads((p.state / "skill_profile.json").read_text(encoding="utf-8"))
        self.assertEqual(led["restore"]["set"], {"gsap-skills@gsap-skills": True})  # the value before the snippet, not the applied false
        self.assertIn("operations@synced", led["restore"]["remove"])

    def test_profile_relative_and_linked_settings_paths(self) -> None:
        """rung 3 #3: a relative settings name and a link that points at settings are refused like the plain path."""
        p = self.p
        self.set_domains(["invest"])
        r = self.profile_run("--apply-file", "settings.json", ok=False, cwd=p.tmp)
        self.assertNotEqual(r.returncode, 0); self.assertFalse((p.tmp / "settings.json").exists())
        from unittest import mock
        import skills_map
        with mock.patch.object(skills_map.os.path, "realpath", lambda q: str(p.tmp / "real" / "settings.local.json")):
            self.assertTrue(skills_map.is_settings_path(p.tmp / "snippet.json"))  # judged on where it points, not its name
        self.assertFalse(skills_map.is_settings_path(p.tmp / "snippet.json"))
        target = p.tmp / "real" / "settings.local.json"; target.parent.mkdir()
        link = p.tmp / "snippet.json"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot create symlinks")
        r = self.profile_run("--apply-file", str(link), ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertFalse(target.exists())

    def test_profile_rerun_keeps_the_restore_record(self) -> None:
        """r1 #1: after the owner applied the snippet the switched-off sets vanish from the list - a second run must not lose
        their restore values."""
        self.set_domains(["invest"])
        self.profile_run()
        self.profile_run(skills="- anthropic-skills:docx: x\n")  # second run: operations/small-business no longer listed
        led = json.loads((self.p.state / "skill_profile.json").read_text(encoding="utf-8"))
        self.assertEqual(led["runs"], 2)
        self.assertIn("operations@synced", led["restore"]["remove"])
        self.assertRegex(self.profile_run("--restore").stdout, r"remove these keys: .*operations@synced")

    def test_profile_write_guards(self) -> None:
        """r1 #3 / rung 3 #3: the snippet file is never a settings path, never an existing file, never the ledger; the ledger
        itself is refused under .claude/."""
        p = self.p
        self.set_domains(["invest"])
        for bad in (p.dir / ".claude" / "x.json", p.tmp / "settings.json"):  # .claude/ branch on its own; the settings name on its own
            r = self.profile_run("--apply-file", str(bad), ok=False)
            self.assertNotEqual(r.returncode, 0); self.assertFalse(bad.exists())
        existing = p.tmp / "keep.txt"; existing.write_text("owner data", encoding="utf-8")
        r = self.profile_run("--apply-file", str(existing), ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertEqual(existing.read_text(encoding="utf-8"), "owner data")
        self.profile_run()
        r = self.profile_run("--apply-file", str(p.state / "skill_profile.json"), ok=False)
        self.assertNotEqual(r.returncode, 0)
        target = p.tmp / "snippet.json"
        self.profile_run("--apply-file", str(target))
        self.assertIn("enabledPlugins", json.loads(target.read_text(encoding="utf-8")))
        cfg = json.loads((p.dir / "forge.json").read_text(encoding="utf-8")); cfg.setdefault("layout", {})["state"] = ".claude/state"
        (p.dir / "forge.json").write_text(json.dumps(cfg), encoding="utf-8", newline="\n")
        r = self.profile_run(ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("never writes settings", r.stdout + r.stderr)
        self.assertFalse((p.dir / ".claude" / "state").exists())

    def test_profile_without_domains_proposes_nothing_off(self) -> None:
        """r1 #2: no usable domain (unknown ids, nothing detectable) - every set is kept and the snippet is empty but printed."""
        p = self.p
        r = self.profile_run("--restore", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("nothing to restore", r.stdout + r.stderr)
        self.set_domains(["nonsense"])
        (p.docs / "CONTEXT.md").write_text("# CONTEXT\n\nzzz\n", encoding="utf-8", newline="\n")
        (p.dir / "01_PROJECT_CORE" / "PLAN.md").write_text("# PLAN\n\nqqq\n", encoding="utf-8", newline="\n")
        out = self.profile_run().stdout
        self.assertIn("no usable domain: nothing is proposed off", out)
        self.assertIn('"enabledPlugins": {}', out)  # rung 3 #7: the snippet is printed even when empty
        self.assertEqual(json.loads((p.state / "skill_profile.json").read_text(encoding="utf-8"))["off"], [])

    def test_profile_falls_back_to_detect(self) -> None:
        p = self.p
        self.set_domains(None)
        (p.docs / "CONTEXT.md").write_text("# CONTEXT\n\nИнвестиционный портфель: акции, дивиденды, отчётность эмитентов, портфель и риск.\n",
                                            encoding="utf-8", newline="\n")
        out = self.profile_run().stdout
        self.assertRegex(out, r"domains [a-z, ]*invest[a-z, ]* \(detect over CONTEXT.md and PLAN.md\)")
        self.assertIn("finance", [k["plugin"] for k in json.loads((p.state / "skill_profile.json").read_text(encoding="utf-8"))["keep"]])

    def test_hooks_json_registers_the_usage_hook(self) -> None:
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        post = [h for h in hooks["PostToolUse"] if "usage.py" in h["hooks"][0]["command"]]
        self.assertEqual(len(post), 1)
        m = re.compile(post[0]["matcher"])
        self.assertTrue(m.search("Agent") and m.search("Task"))
        self.assertFalse(m.search("TaskCreate") or m.search("SendMessage"))  # other tools never start the hook
        self.assertIn("usage.py\" --hook", hooks["SubagentStop"][0]["hooks"][0]["command"])
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", hooks["SubagentStop"][0]["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
