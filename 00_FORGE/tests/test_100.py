"""1.0.0 tests (owner's request 2026-10-11: compile agent-playbook/ambatupipe with Forge, delegate more to Codex, Serena,
Context7, the mod). Every check is tested both ways: the case it must catch and the case it must leave alone."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import GATES, PLUGIN, PY, TOOLS, Project, run  # noqa: E402

FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")
RU = "# Документ\n\nТекст документа для владельца проекта на русском языке.\n" * 3


def hook(script: Path, payload: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(script), *(["--hook"] if script.name == "gpt_guard.py" else [])], cwd=cwd,
                          input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8")


def set_cfg(p: Project, **kv) -> None:
    f = p.dir / "forge.json"
    cfg = json.loads(f.read_text(encoding="utf-8"))
    cfg.update(kv)
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def git(p: Project, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *a], cwd=p.dir, capture_output=True, text=True, encoding="utf-8", errors="replace")


class Release(unittest.TestCase):
    def test_versions_hooks_agents(self) -> None:
        pj = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        k = json.loads((PLUGIN / "kernel.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["version"], k["kernel_version"]); self.assertGreaterEqual(tuple(map(int, k["kernel_version"].split("."))), (1, 0, 1))
        versions = [c["version"] for c in k["changelog"]]
        self.assertIn("CODEX", k["tiers"]); self.assertLess(versions.index("1.0.0"), versions.index("1.0.1"))
        self.assertEqual(versions[-1], k["kernel_version"])  # 1.1.0: the newest changelog entry is the current version
        h = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        for event, script in (("UserPromptSubmit", "tools/prompt_knobs.py"), ("PreToolUse", "gates/gpt_guard.py")):
            cmd = h[event][0]["hooks"][0]["command"]
            self.assertIn(script, cmd); self.assertTrue((PLUGIN / script).is_file(), script)
        inv = (PLUGIN / "agents" / "investigator.md").read_text(encoding="utf-8")
        tools = next(ln for ln in inv.splitlines() if ln.startswith("tools:"))
        for forbidden in ("Write", "Edit", "Bash", "replace_symbol"):  # read-only researcher: no way to change anything
            self.assertNotIn(forbidden, tools)
        self.assertIn("mcp__serena__find_symbol", tools); self.assertIn("mcp__context7__query-docs", tools)
        ex = (PLUGIN / "agents" / "executor.md").read_text(encoding="utf-8")
        self.assertIn("model: opus", ex); self.assertIn("effort: medium", ex)
        self.assertTrue((PLUGIN / "skills" / "forge-code" / "SKILL.md").is_file())
        con = (PLUGIN / "CONSTITUTION.md").read_text(encoding="utf-8")
        for rule in ("**K35", "**K36", "Default lane", "Waivers (1.0)"):
            self.assertIn(rule, con)


class Knobs(unittest.TestCase):
    def ctx(self, text: str) -> str:
        r = hook(TOOLS / "prompt_knobs.py", {"prompt": text}, PLUGIN)
        self.assertEqual(r.returncode, 0)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""

    def test_knobs_fire_only_on_their_words(self) -> None:
        self.assertIn("read only", self.ctx("ss что такое волна?"))
        self.assertIn("read only", self.ctx("ss"))
        self.assertIn("--waive", self.ctx("сделай фичу без тестов"))
        self.assertIn("verify_gpt.py", self.ctx("а теперь кодекс ревью"))
        self.assertIn("autonomous", self.ctx("работай дальше без меня"))
        for quiet in ("ssh ключ не работает", "посмотри тесты", "classic task", "Без паники, всё ок", "",
                      "не работай без меня, я рядом", "нельзя без тестов", "кодекс ревьюер сказал", "без тестовых данных",
                      "don't codex review it"):  # negative controls: other words, partial words, negations (N024 Codex audit)
            self.assertEqual(self.ctx(quiet), "", quiet)


class Guard(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()
        self.pol = self.p.tmp / "policy.json"
        self.pol.write_text(json.dumps({"deny": []}), encoding="utf-8")
        os.environ["FORGE_GPT_POLICY"] = str(self.pol)

    def tearDown(self) -> None:
        os.environ.pop("FORGE_GPT_POLICY", None)
        self.p.cleanup()

    def decide(self, tool: str, inp: dict, cwd: Path) -> str:
        r = subprocess.run([PY, str(GATES / "gpt_guard.py"), "--hook"], input=json.dumps({"tool_name": tool, "tool_input": inp, "cwd": str(cwd)}),
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ})
        self.assertEqual(r.returncode, 0)
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if r.stdout.strip() else ""

    def test_codex_doors_closed_in_a_denied_project_open_elsewhere(self) -> None:
        d = self.p.dir
        for tool, inp in (("Agent", {"subagent_type": "codex:codex-rescue", "prompt": "x"}), ("Skill", {"skill": "codex:review"}),
                          ("Bash", {"command": "codex exec 'fix it'"}), ("Bash", {"command": 'cd x && "C:/bin/codex.exe" review'}),
                          ("PowerShell", {"command": "node codex-companion.mjs task"})):
            self.assertIn("K15", self.decide(tool, inp, d), (tool, inp))          # gpt denied by default (init_project)
        for tool, inp in (("Agent", {"subagent_type": "forge:verifier2"}), ("Bash", {"command": "python tools/codex_exec.py run N001"}),
                          ("Bash", {"command": "git log --grep codexes"}), ("Read", {"file_path": "codex.md"}),
                          ("Bash", {"command": "python verify_gpt.py N001"})):
            self.assertEqual(self.decide(tool, inp, d), "", (tool, inp))          # not a Codex call, or a bridge that checks K15 itself
        set_cfg(self.p, gpt="allowed")
        self.assertEqual(self.decide("Bash", {"command": "codex exec x"}, d), "")
        self.pol.write_text(json.dumps({"deny": [str(self.p.tmp)]}), encoding="utf-8")
        self.assertIn("deny list", self.decide("Bash", {"command": "codex exec x"}, d))  # the owner's deny list wins over "allowed"
        self.pol.write_text(json.dumps({"deny": []}), encoding="utf-8")
        outside = self.p.tmp / "plain"; outside.mkdir()
        self.assertEqual(self.decide("Bash", {"command": "codex exec x"}, outside), "")   # no Forge project: the owner's own use
        set_cfg(self.p, gpt="denied")
        for cmd in (f'cd "{d}" && codex exec x', f"codex exec -C {d} x", f"pushd {d}; codex exec x",
                    "codex exec x; echo verify_gpt.py"):                               # N024 reviews: doors around the cwd check
            self.assertIn("K15", self.decide("Bash", {"command": cmd}, outside if "verify_gpt" not in cmd else d), cmd)
        self.assertEqual(self.decide("Bash", {"command": f'cd "{outside}" && codex exec x'}, outside), "")  # an open folder stays open
        self.pol.write_text("{not json", encoding="utf-8")
        self.assertIn("unreadable", self.decide("Bash", {"command": "codex exec x"}, outside))  # cannot tell what is denied -> refuse
        for bad in ('{"deny": 5}', '{"deny": [null]}', '[1, 2]'):
            self.pol.write_text(bad, encoding="utf-8")
            self.assertIn("unreadable", self.decide("Bash", {"command": "codex exec x"}, outside), bad)
        self.assertEqual(self.decide("Bash", {"command": "git status"}, outside), "")   # ...but only Codex calls are judged


class Orders(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()

    def tearDown(self) -> None:
        self.p.cleanup()

    def deliver(self, oid: str, name: str) -> str:
        (self.p.read / name).write_text(RU, encoding="utf-8", newline="\n")
        return f"{self.p.read.relative_to(self.p.dir).as_posix()}/{name}"

    def test_codex_tier_needs_k15_and_closes_on_the_leads_report(self) -> None:
        p = self.p
        self.assertIn("K15", p.order("new", "Feature", "--tier", "CODEX", "--changes", "x", ok=False).stderr)
        set_cfg(p, gpt="allowed")
        p.order("new", "Feature", "--tier", "CODEX", "--changes", "x", "--deliverable", "02_x/a.md")
        p.order("new", "Lead work", "--tier", "HEAVY", "--changes", "y")
        p.order("take", "N001"); p.order("take", "N002")                                  # CODEX runs alongside the Lead's order
        f = self.deliver("N001", "a.md")
        p.order("answer", "N001", "--executor", "codex:gpt-5", "--tier", "CODEX", "--summary", "s", "--files", f)
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "diff read", "--by", "lead:claude")
        p.order("close", "N001")                                                           # two families: Codex wrote, Claude judged
        p.order("report", "N002", "--verdict", "APPROVE", "--text", "x", "--by", "gpt:codex", ok=False)

    def test_important_codex_work_needs_verifier2_but_no_rung3(self) -> None:
        p = self.p
        set_cfg(p, gpt="allowed")
        p.order("new", "Payments", "--tier", "CODEX", "--changes", "x", "--important"); p.order("take", "N001")
        f = self.deliver("N001", "b.md")
        p.order("answer", "N001", "--executor", "codex:gpt-5", "--summary", "s", "--files", f)
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        self.assertIn("verifier2", p.order("close", "N001", ok=False).stderr)            # important: Claude's reviewer is mandatory
        p.evidence("N001", "v2", "APPROVE")
        p.order("close", "N001")                                                           # and no ChatGPT audit of ChatGPT's own work

    def test_codex_review_counts_for_ordinary_claude_work_only(self) -> None:
        p = self.p
        p.order("new", "Doc", "--tier", "LEAD", "--changes", "x"); p.order("take", "N001")
        f = self.deliver("N001", "c.md")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "s", "--files", f)
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        self.assertIn("second actor", p.order("close", "N001", ok=False).stderr)          # negative control: nothing yet
        p.evidence("N001", "gpt", "CHANGES")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--rung3", "x")
        self.assertNotEqual(p.order("close", "N001", ok=False).returncode, 0)              # a CHANGES review is not an approval
        p.evidence("N001", "gpt", "APPROVE")
        p.order("close", "N001")                                                           # 1.0: Codex is the second family
        p.order("new", "Memo", "--tier", "LEAD", "--changes", "y", "--important"); p.order("take", "N002")
        f2 = self.deliver("N002", "d.md")
        p.order("answer", "N002", "--executor", "lead:claude", "--summary", "s", "--files", f2)
        p.evidence("N002", "gpt", "APPROVE")
        p.order("report", "N002", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--rung3", "accepted 0")
        self.assertIn("verifier2", p.order("close", "N002", ok=False).stderr)            # important: a Codex APPROVE is not enough

    def test_waiver_is_recorded_word_for_word_and_lifts_only_its_rule(self) -> None:
        p = self.p
        self.assertIn("K19", p.order("new", "A", "--changes", "x", "--waive", "money: заплати сам", ok=False).stderr)
        self.assertIn("K19", p.order("new", "A", "--changes", "x", "--waive", "review:", ok=False).stderr)
        self.assertEqual(list((p.circle / "orders").glob("N*.md")), [])                  # a bad waiver writes nothing
        p.order("new", "A", "--changes", "x", "--waive", "tests: без тестов; это черновик, review: потом решим"); p.order("take", "N001")
        md = next((p.circle / "orders").glob("N001_*.md")).read_text(encoding="utf-8")
        self.assertIn("**Waiver (owner's words, K19):** tests: без тестов; это черновик, review: потом решим", md)  # ';' kept whole
        f = self.deliver("N001", "e.md")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "s", "--files", f)
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        self.assertIn("second actor", p.order("close", "N001", ok=False).stderr)          # 'review:' inside the tests waiver's words lifts nothing
        p.order("amend", "N001", "--why", "owner: сам посмотрю", "--waive", "review: без ревью, сам посмотрю")
        p.order("close", "N001")
        journal = "".join(x.read_text(encoding="utf-8") for x in (p.circle / "journal").glob("*.md"))
        self.assertIn("waiver (owner's words): review: без ревью, сам посмотрю", journal)
        self.assertIn("close under the owner's waiver", journal)


class CodexExec(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()
        home = self.p.tmp / "codex_home"; home.mkdir()
        self.env = {"FORGE_CODEX": FAKE, "FORGE_GPT_POLICY": str(self.p.tmp / "policy.json"), "FAKE_CODEX_MODE": "ok",
                    "CODEX_HOME": str(home)}
        (self.p.tmp / "policy.json").write_text(json.dumps({"deny": []}), encoding="utf-8")
        git(self.p, "init", "-q"); git(self.p, "config", "user.email", "t@example.com"); git(self.p, "config", "user.name", "t")
        git(self.p, "add", "-A"); git(self.p, "commit", "-q", "-m", "init")

    def tearDown(self) -> None:
        self.p.cleanup()

    def cx(self, *args: str, ok: bool = True, **env: str) -> subprocess.CompletedProcess:
        return run([str(TOOLS / "codex_exec.py"), *args], cwd=self.p.dir, ok=ok, env={**self.env, **env})

    def test_k15_refuses_before_anything_is_sent(self) -> None:
        self.p.order("new", "F", "--tier", "LEAD", "--changes", "x")
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/a.py", ok=False)
        self.assertIn("K15", r.stdout + r.stderr)
        self.assertFalse((self.p.circle / "answers" / "N001_codex.json").exists())          # the fake was never started

    def gpt_on(self) -> None:
        p = self.p
        set_cfg(p, gpt="allowed"); git(p, "add", "-A"); git(p, "commit", "-q", "-m", "gpt on")
        p.order("new", "F", "--tier", "CODEX", "--changes", "x"); p.order("take", "N001")

    def test_critical_limit_refuses_before_run_and_force_overrides(self) -> None:
        p = self.p
        self.gpt_on()
        home = p.tmp / "limited_codex"
        sessions = home / "sessions"; sessions.mkdir(parents=True)
        (sessions / "rollout-limit.jsonl").write_text(json.dumps({"payload": {"rate_limits": {
            "primary": {"used_percent": 95}}}}) + "\n", encoding="utf-8", newline="\n")
        args = ("run", "N001", "--task", "t", "--scope", "src/a.py")
        r = self.cx(*args, ok=False, CODEX_HOME=str(home), FORGE_CODEX_FORCE="0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("critical", r.stderr); self.assertIn("95%", r.stderr)
        self.assertIn("route --kind code", r.stderr); self.assertIn("FORGE_CODEX_FORCE=1", r.stderr)
        self.assertIn("--force", r.stderr)
        self.assertFalse((p.circle / "answers" / "N001_codex.json").exists())              # the fake was never started
        r = self.cx(*args, CODEX_HOME=str(home), FORGE_CODEX_FORCE="1")
        self.assertIn("T1 PROPOSED", r.stdout)

    def test_force_flag_overrides_critical_codex_limit(self) -> None:
        self.gpt_on()
        sessions = Path(self.env["CODEX_HOME"]) / "sessions"; sessions.mkdir(parents=True)
        (sessions / "rollout-limit.jsonl").write_text(json.dumps({"payload": {"rate_limits": {
            "primary": {"used_percent": 95}}}}) + "\n", encoding="utf-8", newline="\n")
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/a.py", "--force", FORGE_CODEX_FORCE="0")
        self.assertIn("T1 PROPOSED", r.stdout)

    def test_codex_proposes_read_only_and_the_lead_applies(self) -> None:
        p = self.p
        self.gpt_on()
        (p.dir / "src").mkdir(); (p.dir / "src" / "a.py").write_text("value = 1\n", encoding="utf-8")
        r = self.cx("run", "N001", "--task", "add value", "--scope", "src/a.py,src/new.py,src/__init__.py", "--model", "fake-1")
        self.assertIn("T1 PROPOSED", r.stdout); self.assertIn("codex_exec.py apply N001 T1", r.stdout)
        self.assertEqual((p.dir / "src" / "a.py").read_text(encoding="utf-8"), "value = 1\n")   # nothing written before `apply`
        self.assertFalse((p.dir / "src" / "new.py").exists())
        patch = (p.circle / "answers" / "N001_T1.patch").read_text(encoding="utf-8")
        self.assertIn("-value = 1", patch); self.assertIn("+value = 42  # sandbox read-only", patch); self.assertIn("+++ b/src/new.py", patch)
        self.assertIn("no staged proposal", self.cx("apply", "N001", "T9", ok=False).stderr)  # only what Codex proposed
        self.cx("apply", "N001", "T1", "--answer")
        self.assertEqual((p.dir / "src" / "a.py").read_text(encoding="utf-8"), "value = 42  # sandbox read-only\n")
        self.assertTrue((p.dir / "src" / "new.py").is_file())
        self.assertEqual((p.dir / "src" / "__init__.py").read_text(encoding="utf-8"), "")    # a new EMPTY file is a change too (live run)
        ans = json.loads((p.circle / "answers" / "N001.json").read_text(encoding="utf-8"))
        self.assertEqual(ans["executor"], "codex:fake-1"); self.assertEqual(ans["tier"], "CODEX")
        self.assertEqual(ans["files_changed"], ["src/__init__.py", "src/a.py", "src/new.py"])
        self.assertIn("already applied", self.cx("apply", "N001", "T1", ok=False).stderr)    # never twice
        self.assertIn('"sandbox": "read-only"', (p.state / "cache" / "gpt_usage.jsonl").read_text(encoding="utf-8"))

    def test_crlf_kept_links_refused_partial_answer(self) -> None:
        p = self.p
        self.gpt_on()
        (p.dir / "src").mkdir(); (p.dir / "src" / "w.py").write_bytes(b"value = 1\r\n")
        self.cx("run", "N001", "--task", "t", "--scope", "src/w.py")
        self.assertNotIn("\r", (p.circle / "answers" / "N001_T1.patch").read_text(encoding="utf-8"))
        self.cx("apply", "N001", "T1")
        self.assertEqual((p.dir / "src" / "w.py").read_bytes(), b"value = 42  # sandbox read-only\r\n")  # CRLF file stays CRLF
        link = p.dir / "src" / "link"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(p.circle)], capture_output=True) if os.name == "nt" else None
        if made is None or made.returncode != 0:
            try:
                os.symlink(p.circle, link, target_is_directory=True)
            except OSError:
                self.skipTest("this account can create neither a junction nor a symlink")
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/link/evil.md", "--tid", "T2", ok=False)
        self.assertIn("resolves into Forge state", r.stdout)                                 # src/link/... really lands in the circle
        self.assertFalse((p.state / "staging" / "codex" / "N001" / "T2" / "src" / "link").exists())
        plan = p.circle / "answers" / "N001_plan.json"
        plan.write_text(json.dumps({"order": "N001", "tasks": [{"id": "T5", "task": "a", "scope": ["src/x.py"]},
                                                                {"id": "T6", "task": "b", "scope": ["src/y.py"]}]}), encoding="utf-8")
        self.cx("wave", "N001")
        self.cx("apply", "N001", "T5", "--answer")
        ans = json.loads((p.circle / "answers" / "N001.json").read_text(encoding="utf-8"))
        self.assertEqual(ans["status"], "partial"); self.assertIn("T6", ans["summary"])  # half a plan is not a done order
        self.cx("apply", "N001", "T6", "--answer")
        self.assertEqual(json.loads((p.circle / "answers" / "N001.json").read_text(encoding="utf-8"))["status"], "done")

    def test_apply_refuses_a_file_changed_since_the_proposal(self) -> None:
        p = self.p
        self.gpt_on()
        (p.dir / "src").mkdir(); (p.dir / "src" / "a.py").write_text("value = 1\n", encoding="utf-8")
        self.cx("run", "N001", "--task", "t", "--scope", "src/a.py")
        (p.dir / "src" / "a.py").write_text("value = 2  # someone else\n", encoding="utf-8")
        self.assertIn("changed since Codex proposed", self.cx("apply", "N001", "T1", ok=False).stderr)
        self.assertEqual((p.dir / "src" / "a.py").read_text(encoding="utf-8"), "value = 2  # someone else\n")  # not overwritten

    def test_out_of_scope_rejected_breaches_and_failures_visible(self) -> None:
        p = self.p
        self.gpt_on()
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/a.py", FAKE_CODEX_MODE="stray")
        self.assertIn("rejected: stray.txt (outside the task scope)", r.stdout)
        self.assertIn("rejected: forge.json (Forge state)", r.stdout)
        self.assertFalse((p.state / "staging" / "codex" / "N001" / "T1" / "stray.txt").exists())   # rejected = never staged
        self.assertNotIn("stray.txt", (p.circle / "answers" / "N001_T1.patch").read_text(encoding="utf-8"))
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/w.py", "--tid", "T4", ok=False, FAKE_CODEX_MODE="write")
        self.assertIn("K34d", r.stdout); self.assertIn("breach.txt", r.stdout)                # a write during a read-only run is flagged
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/b.py", "--tid", "T2", ok=False, FAKE_CODEX_MODE="fail")
        self.assertIn("FAILED", r.stdout); self.assertIn("limit", r.stdout)
        self.assertIn("give T2 to the `executor` agent", r.stdout)  # 1.1.0: a limit hit routes by K37 - Claude has the room
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/c.py", "--tid", "T3", ok=False, FAKE_CODEX_MODE="blocked")
        self.assertIn("BLOCKED", r.stdout); self.assertIn("which module?", r.stdout)

    def test_waves_wait_for_applied_dependencies_and_fix_resumes(self) -> None:
        p = self.p
        self.gpt_on()
        plan = p.circle / "answers" / "N001_plan.json"
        plan.write_text(json.dumps({"order": "N001", "tasks": [
            {"id": "T1", "task": "a", "scope": ["src/a.py"], "depends": []},
            {"id": "T2", "task": "b", "scope": ["src/a.py"], "depends": []}]}), encoding="utf-8")
        self.assertIn("share files", self.cx("wave", "N001", ok=False).stderr)               # two proposals on one file: refused
        plan.write_text(json.dumps({"order": "N001", "tasks": [
            {"id": "T1", "task": "a", "scope": ["src/a.py"], "depends": []},
            {"id": "T2", "task": "b", "scope": ["src/b.py"], "depends": []},
            {"id": "T3", "task": "c", "scope": ["src/c.py"], "depends": ["T1", "T2"]}]}), encoding="utf-8")
        r = self.cx("wave", "N001", "--slots", "2")
        self.assertEqual(r.stdout.count(" PROPOSED "), 2)                                     # T3 waits: its inputs are not applied
        self.assertIn("apply or fix the proposals first", self.cx("wave", "N001").stdout)
        self.cx("fix", "N001", "T1", "--feedback", "use 43")
        self.assertIn("value = 43", (p.circle / "answers" / "N001_T1.patch").read_text(encoding="utf-8"))  # the thread was resumed
        self.cx("apply", "N001", "--all")
        self.assertEqual((p.dir / "src" / "a.py").read_text(encoding="utf-8"), "value = 43  # sandbox read-only\n")
        self.assertIn(" PROPOSED ", self.cx("wave", "N001").stdout)                            # now T3 runs
        self.cx("apply", "N001", "T3")
        self.assertIn("3/3 applied", self.cx("status", "N001").stdout)


class CodeCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()

    def tearDown(self) -> None:
        self.p.cleanup()

    def check(self, *args: str) -> subprocess.CompletedProcess:
        return run([str(GATES / "check_code.py"), *args], cwd=self.p.dir, ok=False)

    def test_red_on_broken_code_green_on_good_and_honest_skips(self) -> None:
        d = self.p.dir
        (d / "good.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
        (d / "bad.py").write_text("def f(x)\n    return x\n", encoding="utf-8")
        (d / "bad.json").write_text("{nope", encoding="utf-8")
        r = self.check("--files", "good.py", "--no-tests")
        self.assertEqual(r.returncode, 0, r.stdout); self.assertIn("OK   python syntax", r.stdout)
        self.assertIn("SKIP tests", r.stdout)
        r = self.check("--files", "bad.py", "--no-tests")
        self.assertEqual(r.returncode, 1); self.assertIn("FAIL python syntax", r.stdout)    # negative control
        r = self.check("--files", "bad.json")
        self.assertEqual(r.returncode, 1)
        (d / "a.ts").write_text("export const x: number = 1\n", encoding="utf-8")
        for only in ("a.ts", "README.md"):                                                   # no tsc/eslint config, not code
            r = self.check("--files", only, "--no-tests")
            self.assertEqual(r.returncode, 1, only); self.assertIn("FAIL not checked", r.stdout)   # checked nothing = not green
        (d / "build").mkdir(); (d / "build" / "x.py").write_text("x = 1\n", encoding="utf-8")
        r = self.check("--files", "good.py,build/x.py,README.md", "--no-tests")
        self.assertIn("build/x.py", r.stdout); self.assertIn("README.md", r.stdout)       # every file asked for is accounted for
        r = self.check("--files", "typo.py", "--no-tests")
        self.assertEqual(r.returncode, 1); self.assertIn("not found: typo.py", r.stdout)     # never a silent green (N024 reviews)
        git(self.p, "init", "-q")
        (self.p.circle / "answers" / "N009.json").write_text("{nope", encoding="utf-8")      # Forge state: the gates' job
        r = self.check("--no-tests", "--json")
        files = json.loads(r.stdout.strip().splitlines()[-1])["files"]
        self.assertIn("good.py", files); self.assertFalse(any("circle/" in f for f in files))
        set_cfg(self.p, code={"test": f'"{PY}" -c "import sys; sys.exit(3)"'})
        self.p.order("new", "F", "--changes", "x")
        r = self.check("--files", "good.py", "--order", "N001")
        self.assertEqual(r.returncode, 1); self.assertIn("FAIL tests", r.stdout)           # the project's failing test command
        data = json.loads((self.p.circle / "answers" / "N001_checks.json").read_text(encoding="utf-8"))
        self.assertFalse(data["ok"])
        set_cfg(self.p, code={"test": f'"{PY}" -c "print(1)"'})
        self.assertEqual(self.check("--files", "good.py").returncode, 0)


class Setup(unittest.TestCase):
    def test_doctor_changes_nothing_and_names_the_fixes(self) -> None:
        import shutil
        import tempfile
        home = Path(tempfile.mkdtemp(prefix="forge_home_"))
        try:
            env = {"USERPROFILE": str(home), "HOME": str(home), "APPDATA": str(home / "Roaming"),
                   "LOCALAPPDATA": str(home / "Local"), "FORGE_CODEX": "", "CLAUDE_CODE_EFFORT_LEVEL": "",
                   "PATH": os.pathsep.join([str(Path(PY).parent), os.environ.get("SystemRoot", "C:/Windows") + "/System32"])}  # this PC's tools must not leak in
            before = sorted(x.relative_to(home).as_posix() for x in home.rglob("*"))
            r = run([str(TOOLS / "setup.py"), "doctor"], cwd=home, env=env)
            self.assertEqual(sorted(x.relative_to(home).as_posix() for x in home.rglob("*")), before)  # read only
            self.assertIn("MISS serena", r.stdout); self.assertIn("fix:", r.stdout)       # an empty home is missing things...
            self.assertIn("serena-agent", r.stdout); self.assertIn("context7", r.stdout)    # ...and the exact commands are named
            self.assertIn("setup.py install", r.stdout)
        finally:
            shutil.rmtree(home, ignore_errors=True)


class CodeProject(unittest.TestCase):
    def test_init_code_and_brief_line(self) -> None:
        p = Project()
        try:
            self.assertNotIn("code", json.loads((p.dir / "forge.json").read_text(encoding="utf-8")))  # not a code project by default
            self.assertNotIn("code:", p.step("--brief").stdout)
            set_cfg(p, code={"test": "python -m pytest -q"})
            self.assertIn("code: read with Serena", p.step("--brief").stdout)
        finally:
            p.cleanup()
        tmp = Project().tmp
        try:
            run([str(TOOLS / "init_project.py"), str(tmp / "c"), "--name", "c", "--plan", str(tmp / "plan.json"), "--mode", "cheap",
                 "--request", "я хочу код", "--code", "python -m pytest -q"], cwd=tmp)
            cfg = json.loads((tmp / "c" / "forge.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["code"]["test"], "python -m pytest -q")
            self.assertIn(".serena/cache/", (tmp / "c" / ".gitignore").read_text(encoding="utf-8"))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
