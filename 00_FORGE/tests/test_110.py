"""1.1.0 tests: fixes found by the 2026-10-13 review (frontmatter, silent hooks outside a project, Codex runs that were cut off,
fallback by limits, an empty code check) - each with a negative control."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import GATES, PLUGIN, PY, TOOLS, Project, run  # noqa: E402

sys.path.insert(0, str(TOOLS))
import codex_exec  # noqa: E402

PLAIN_BAD_START = tuple("-?:,[]{}#&*!|>'\"%@`")


def frontmatter(text: str) -> dict[str, str]:
    """The `key: value` lines between the first two `---` lines (raises when there is no frontmatter)."""
    if not text.startswith("---\n"):
        raise ValueError("no frontmatter")
    block = text[4:text.index("\n---", 4)]
    out = {}
    for line in block.splitlines():
        if line.strip() and not line.startswith((" ", "\t")):
            key, sep, value = line.partition(":")
            if not sep:
                raise ValueError(f"not a key: value line: {line[:60]}")
            out[key.strip()] = value.strip()
    return out


def yaml_problem(text: str) -> str:
    """'' when the frontmatter is valid YAML. Uses PyYAML when it is installed, else the plain-scalar rules that broke 1.0.1
    (a `: ` or ` #` inside an unquoted value, an indicator character first)."""
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            data = yaml.safe_load(text[4:text.index("\n---", 4)])
        except yaml.YAMLError as e:  # type: ignore[attr-defined]
            return str(e).splitlines()[0]
        return "" if isinstance(data, dict) else "frontmatter is not a mapping"
    for key, value in frontmatter(text).items():
        if value[:1] in ("'", '"'):
            continue
        if ": " in value or " #" in value or value.startswith(PLAIN_BAD_START) or value.endswith(":"):
            return f"{key}: unquoted value breaks YAML"
    return ""


def frontmatter_files() -> list[Path]:
    return sorted((PLUGIN / "skills").glob("*/SKILL.md")) + sorted((PLUGIN / "agents").glob("*.md"))


class Frontmatter(unittest.TestCase):
    def test_every_skill_and_agent_frontmatter_is_valid_yaml(self) -> None:
        bad = {p.relative_to(PLUGIN).as_posix(): why for p in frontmatter_files()
               if (why := yaml_problem(p.read_text(encoding="utf-8")))}
        self.assertEqual(bad, {})

    def test_the_check_catches_the_101_description(self) -> None:  # negative control: forge-code 1.0.1 did not parse
        old = ("---\nname: forge-code\ndescription: Code work - Not for a question about code (default lane: answer it) and "
               "not for a one-shot artifact.\n---\n")
        self.assertTrue(yaml_problem(old))
        self.assertFalse(yaml_problem("---\nname: a\ndescription: Use when the owner says «готово».\n---\n"))

    def test_descriptions_say_when_not_how(self) -> None:
        """C1: a description says when to load the skill; rule ids and tool names in it make the model follow the summary."""
        for p in frontmatter_files():
            fm = frontmatter(p.read_text(encoding="utf-8"))
            desc = fm.get("description", "")
            self.assertTrue(desc, p.name)
            self.assertLessEqual(len(desc), 1024, p.name)
            if p.parent.parent.name == "skills":
                self.assertTrue(desc.startswith("Use "), p.parent.name)
                rx = r"\.py\b" if p.parent.name == "forge-rules" else r"\bK\d+\b|\.py\b"  # forge-rules: a rule id IS its trigger
                self.assertIsNone(re.search(rx, desc), p.parent.name)


class SilentOutsideAProject(unittest.TestCase):
    def test_session_hooks_exit_0_and_print_nothing_outside_forge(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="forge_no_project_"))
        for flag in ("--brief", "--stop-check"):
            r = run([str(TOOLS / "step.py"), flag], cwd=tmp, ok=False)
            self.assertEqual((r.returncode, r.stdout.strip(), r.stderr.strip()), (0, "", ""), flag)
        # negative control: a command outside a project still says why. 1.1.1: run a copy of the plugin, because find_root also
        # looks above the plugin's own files and the dev workspace (C:/ai/agi) has a forge.json there - an installed plugin does not
        copy = Path(tempfile.mkdtemp(prefix="forge_plugin_copy_")) / "plugin"
        self.addCleanup(shutil.rmtree, copy.parent, ignore_errors=True)
        shutil.copytree(PLUGIN, copy, ignore=shutil.ignore_patterns("tests", "evals", "__pycache__"))
        self.assertFalse(any((d / "forge.json").is_file() for d in copy.parents), "a forge.json above the temp folder")
        r = run([str(copy / "tools" / "step.py")], cwd=tmp, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("forge.json not found", r.stderr)

    def test_hooks_still_speak_inside_a_project(self) -> None:
        p = Project()
        try:
            self.assertIn("[Forge]", run([str(TOOLS / "step.py"), "--brief"], cwd=p.dir).stdout)
        finally:
            p.cleanup()


class EmptyCodeCheck(unittest.TestCase):
    def test_nothing_to_check_is_not_green(self) -> None:
        p = Project()
        try:
            r = run([str(GATES / "check_code.py")], cwd=p.dir, ok=False)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("nothing checked", r.stdout)
            (p.dir / "a.py").write_text("x = 1\n", encoding="utf-8")  # negative control: a real file is checked and green
            r = run([str(GATES / "check_code.py"), "--files", "a.py"], cwd=p.dir, ok=False)
            self.assertEqual(r.returncode, 0, r.stdout)
        finally:
            p.cleanup()


class CodexRunsCutOff(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="forge_plan_"))
        self.plan = self.tmp / "N001_plan.json"

    def write(self, **t1) -> None:
        self.plan.write_text(json.dumps({"order": "N001", "tasks": [{"id": "T1", "task": "a", "scope": ["a.py"], **t1},
                                                                    {"id": "T2", "task": "b", "scope": ["b.py"], "status": "pending"}]}),
                             encoding="utf-8")

    def status(self, tid: str) -> str:
        return next(t["status"] for t in json.loads(self.plan.read_text(encoding="utf-8"))["tasks"] if t["id"] == tid)

    def test_running_past_the_deadline_becomes_failed(self) -> None:
        past = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
        self.write(status="running", deadline=past, pid=os.getpid())
        self.assertEqual(codex_exec.reap_stale(self.plan), ["T1"])
        self.assertEqual(self.status("T1"), "failed")
        self.assertEqual(self.status("T2"), "pending")

    def test_a_live_run_inside_its_deadline_is_left_alone(self) -> None:  # negative control
        future = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
        self.write(status="running", deadline=future, pid=os.getpid())
        self.assertEqual(codex_exec.reap_stale(self.plan), [])
        self.assertEqual(self.status("T1"), "running")

    def test_a_dead_process_is_cut_off_before_its_deadline(self) -> None:
        future = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
        dead = 2 ** 22 + 12345  # far above any pid this box hands out
        if codex_exec.pid_alive(dead):
            self.skipTest("cannot prove a pid dead on this OS")
        self.write(status="running", deadline=future, pid=dead)
        self.assertEqual(codex_exec.reap_stale(self.plan), ["T1"])

    def test_second_failure_goes_to_hold_not_to_another_executor(self) -> None:
        self.assertIn("HOLD", codex_exec.next_step(None, "N001", "T1", 2))
        self.assertNotIn("HOLD", codex_exec.next_step(None, "N001", "T1", 1))
        self.assertIn("executor", codex_exec.next_step(None, "N001", "T1", 1, limit_hit=True))


class CodexPacket(unittest.TestCase):
    """B3: the project's code rules ride in Codex's prompt; the answer adds what the diff does not show; long prompts use stdin."""

    def setUp(self) -> None:
        from test_100 import FAKE, git, set_cfg
        self.p = Project()
        home = self.p.tmp / "codex_home"; home.mkdir()
        self.out = self.p.tmp / "prompt.txt"
        self.env = {"FORGE_CODEX": FAKE, "FORGE_GPT_POLICY": str(self.p.tmp / "policy.json"), "FAKE_CODEX_MODE": "ok",
                    "CODEX_HOME": str(home), "FAKE_CODEX_PROMPT_OUT": str(self.out)}
        (self.p.tmp / "policy.json").write_text(json.dumps({"deny": []}), encoding="utf-8")
        (self.p.tmp / "outside.md").write_text("SECRET-OUTSIDE\n", encoding="utf-8")
        (self.p.dir / ".serena" / "memories").mkdir(parents=True)
        (self.p.dir / ".serena" / "memories" / "core.md").write_text("RULE-CORE: errors are raised, never swallowed\n", encoding="utf-8")
        (self.p.dir / "RULES.md").write_text("RULE-FILE: snake_case only\n", encoding="utf-8")
        set_cfg(self.p, gpt="allowed", code={"test": "", "rules_files": ["RULES.md", "../outside.md"]})
        for a in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t"), ("add", "-A"),
                  ("commit", "-q", "-m", "init")):
            git(self.p, *a)
        self.p.order("new", "F", "--tier", "CODEX", "--changes", "x"); self.p.order("take", "N001")

    def tearDown(self) -> None:
        self.p.cleanup()

    def cx(self, *args: str) -> subprocess.CompletedProcess:
        return run([str(TOOLS / "codex_exec.py"), *args], cwd=self.p.dir, ok=False, env=self.env)

    def test_rules_in_the_prompt_and_nothing_from_outside(self) -> None:
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/a.py")
        prompt = self.out.read_text(encoding="utf-8")
        self.assertIn("RULE-CORE", prompt); self.assertIn("RULE-FILE", prompt)
        self.assertNotIn("SECRET-OUTSIDE", prompt)  # negative control: a path outside the project is never read
        self.assertIn("Never get green by weakening a check", prompt)
        self.assertIn("decision: kept the module layout", r.stdout)

    def test_a_long_prompt_goes_through_stdin(self) -> None:
        (self.p.dir / "RULES.md").write_text("RULE-LONG " * 3000, encoding="utf-8")  # 30k characters > the argv cap
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/a.py")
        self.assertIn("PROPOSED", r.stdout, r.stdout + r.stderr)
        self.assertIn("RULE-LONG", self.out.read_text(encoding="utf-8"))


class Clarifications(unittest.TestCase):
    def test_open_markers_block_take_until_resolved(self) -> None:
        p = Project()
        try:
            p.order("new", "Отчёт", "--tier", "HEAVY", "--changes", "x",
                    "--dod", "таблица за [NEEDS CLARIFICATION: какой период?];итог по [NEEDS CLARIFICATION: каким каналам?]")
            r = p.order("take", "N001", ok=False)
            self.assertNotEqual(r.returncode, 0); self.assertIn("какой период?", r.stdout + r.stderr)
            p.order("amend", "N001", "--why", "ответ владельца", "--resolve", "2025 год")
            self.assertIn("каким каналам?", p.order("take", "N001", ok=False).stderr)  # one marker left
            p.order("amend", "N001", "--why", "ответ владельца", "--resolve", "все каналы")
            p.order("take", "N001")  # all markers resolved: the order runs
            text = next((p.circle / "orders").glob("N001_*.md")).read_text(encoding="utf-8")
            self.assertIn("[clarified", text); self.assertNotIn("NEEDS CLARIFICATION", text)
        finally:
            p.cleanup()


class SecondScanner(unittest.TestCase):
    """E5: an optional SARIF scanner can only raise the vet verdict; its failure is never a pass."""

    def vet(self, scanner: str | None) -> subprocess.CompletedProcess:
        tmp = Path(tempfile.mkdtemp(prefix="forge_vet_"))
        (tmp / "s").mkdir()
        (tmp / "s" / "SKILL.md").write_text("---\nname: s\ndescription: Use when testing.\n---\nText only.\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FORGE_SKILL_SCANNER"}
        if scanner is not None:
            env["FORGE_SKILL_SCANNER"] = scanner
        return subprocess.run([PY, str(TOOLS / "skills_map.py"), "vet", str(tmp / "s")], capture_output=True, text=True,
                              encoding="utf-8", env=env)

    def test_findings_raise_the_verdict_and_a_failure_is_not_a_pass(self) -> None:
        self.assertIn("verdict: LOW-RISK", self.vet(None).stdout)  # negative control: text-only skill, no scanner
        fake = Path(tempfile.mkdtemp(prefix="forge_scanner_")) / "scan.py"
        fake.write_text("import json,sys\nprint(json.dumps({'runs':[{'results':[{'ruleId':'PI-1','level':'error',"
                        "'message':{'text':'prompt injection'},'locations':[{'physicalLocation':{'artifactLocation':{'uri':'SKILL.md'}}}]}]}]}))\n",
                        encoding="utf-8")
        out = self.vet(f'"{PY}" "{fake}" {{path}}').stdout
        self.assertIn("verdict: REVIEW", out); self.assertIn("PI-1", out)
        out = self.vet(f'"{PY}" -c "print(1/0)" {{path}}').stdout
        self.assertIn("verdict: REVIEW", out); self.assertIn("FAILED", out)


class LessonGuards(unittest.TestCase):
    """D3: a repeating lesson becomes a guard the hooks enforce - with the owner's yes, never on its own."""

    def setUp(self) -> None:
        self.p = Project()

    def tearDown(self) -> None:
        self.p.cleanup()

    def g(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        return run([str(TOOLS / "guards.py"), *args], cwd=self.p.dir, ok=ok)

    def pre(self, command: str, cwd: Path | None = None) -> dict:
        data = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or self.p.dir)}
        r = subprocess.run([PY, str(GATES / "gpt_guard.py"), "--hook"], input=json.dumps(data), capture_output=True, text=True,
                           encoding="utf-8", cwd=cwd or self.p.dir)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def test_block_and_warn_on_commands(self) -> None:
        self.g("add", "--on", "bash", "--pattern", r"git\s+add\s+(-A|--all|\.)(\s|$)", "--action", "block",
               "--message", "add the order's files by name (K14)", "--lesson", "L003", "--by", "owner, test")
        self.g("add", "--on", "bash", "--pattern", r"\brm\s+-rf\b", "--action", "warn", "--message", "deletion: owner files are K15",
               "--lesson", "L004", "--by", "owner, test")
        out = self.pre("git add -A")["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny"); self.assertIn("G001", out["permissionDecisionReason"])
        out = self.pre("rm -rf build")["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", out); self.assertIn("lesson L004", out["additionalContext"])
        self.assertEqual(self.pre("git add src/a.py"), {})  # negative control: a precise add passes
        other = Path(tempfile.mkdtemp(prefix="forge_no_project_"))
        self.assertEqual(self.pre("git add -A", cwd=other), {})  # outside the project its guards do not apply

    def test_write_guard_reaches_the_lead(self) -> None:
        self.g("add", "--on", "write", "--pattern", r"Patreon", "--action", "warn", "--message", "the owner left Patreon",
               "--lesson", "L007", "--by", "owner, test")
        f = self.p.dir / "note.md"
        f.write_text("Купить через Patreon\n", encoding="utf-8", newline="\n")  # 1.1.1: LF on Windows too, as the Write tool does
        data = {"tool_name": "Write", "tool_input": {"file_path": str(f)}}
        r = subprocess.run([PY, str(GATES / "check_file.py"), "--hook"], input=json.dumps(data), capture_output=True, text=True,
                           encoding="utf-8", cwd=self.p.dir)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("lesson L007", r.stdout)  # 1.2.0: warn = exit 0 + additionalContext
        self.g("add", "--on", "write", "--pattern", r"Boosty", "--action", "block", "--message", "the owner left Boosty",
               "--lesson", "L008", "--by", "owner, test")
        f.write_text("Купить через Boosty\n", encoding="utf-8", newline="\n")
        r = subprocess.run([PY, str(GATES / "check_file.py"), "--hook"], input=json.dumps(data), capture_output=True, text=True,
                           encoding="utf-8", cwd=self.p.dir)
        self.assertEqual(r.returncode, 2); self.assertIn("lesson L008", r.stderr)  # a block guard still stops the write
        f.write_text("Купить на сайте\n", encoding="utf-8", newline="\n")
        r = subprocess.run([PY, str(GATES / "check_file.py"), "--hook"], input=json.dumps(data), capture_output=True, text=True,
                           encoding="utf-8", cwd=self.p.dir)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_propose_names_repeating_lessons_only(self) -> None:
        les = self.p.circle / "lessons"
        for i, what in enumerate(["git add all staged other agents files", "git add all staged other agents files again",
                                  "a different failure entirely here"], 1):
            (les / f"2026-10-1{i}_L00{i}.json").write_text(json.dumps({"id": f"L00{i}", "what_failed": what,
                                                                       "fix": "use `git add -A` never"}), encoding="utf-8")
        out = self.g("propose").stdout
        self.assertIn("L001, L002", out); self.assertNotIn("L003", out)
        self.assertIn("guards.py add --on bash", out)
        self.assertEqual(self.g("list").stdout.strip().splitlines()[-1].split()[0], "0")  # proposing adds nothing


class SwarmWorkflow(unittest.TestCase):
    """F2: workflows/swarm-run.js keeps dependency order and this PC's slots, redoes a red file once, then one review."""

    def test_simulated_run(self) -> None:
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        src = (PLUGIN / "workflows" / "swarm-run.js").read_text(encoding="utf-8")
        self.assertTrue(src.startswith("export const meta = {"))
        self.assertIsNone(re.search(r"Date\.now\(|Math\.random\(|new Date\(\)", src))  # would break resume
        tmp = Path(tempfile.mkdtemp(prefix="forge_wf_"))
        i = src.index("\n}\n") + 3  # the workflow body runs inside an async function: wrap it the same way
        (tmp / "swarm-check.mjs").write_text(src[:i] + "\nexport async function body(args, agent, phase, log) {\n" + src[i:] + "\n}\n",
                                            encoding="utf-8")
        shutil.copy(Path(__file__).with_name("swarm_run_sim.mjs"), tmp / "swarm_run_sim.mjs")
        r = subprocess.run([node, "swarm_run_sim.mjs"], cwd=tmp, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertLessEqual(out["peak"], 2)
        self.assertLess(out["calls"].index("a.md"), out["calls"].index("b.md"))
        self.assertIn("redo 2: b.md", out["calls"])
        self.assertEqual(out["review"], "APPROVE")


def hook(p: Project, flag: str, data: dict) -> subprocess.CompletedProcess:
    """step.py as Claude Code runs it: the hook's JSON on stdin."""
    return subprocess.run([PY, str(TOOLS / "step.py"), flag, "--hook"], cwd=p.dir, input=json.dumps(data), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


class AfterCompaction(unittest.TestCase):
    def test_compact_start_brings_back_the_request_and_the_handoff(self) -> None:
        p = Project()
        try:
            p.order("new", "Таблица", "--tier", "HEAVY", "--changes", "x", "--request", "сделай таблицу без Patreon",
                    "--request-source", "chat")
            p.order("take", "N001")
            (p.state / "handoff.md").write_text("N001 Execute: осталась колонка цен\n", encoding="utf-8")
            r = hook(p, "--brief", {"hook_event_name": "SessionStart", "source": "compact"})
            self.assertIn("[after compaction]", r.stdout)
            self.assertIn("сделай таблицу без Patreon", r.stdout)
            self.assertIn("осталась колонка цен", r.stdout)
            self.assertLessEqual(len(r.stdout), 10_000)
            r = hook(p, "--brief", {"hook_event_name": "SessionStart", "source": "startup"})  # negative control
            self.assertNotIn("[after compaction]", r.stdout)
        finally:
            p.cleanup()


class AutonomousRun(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()
        self.p.order("new", "A", "--tier", "HEAVY", "--changes", "a")
        self.p.order("new", "B", "--tier", "HEAVY", "--changes", "b")

    def tearDown(self) -> None:
        self.p.cleanup()

    def auto(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        return run([str(TOOLS / "autorun.py"), *args], cwd=self.p.dir, ok=ok)

    def stop(self, **extra) -> dict:
        out = hook(self.p, "--stop-check", {"hook_event_name": "Stop", "stop_hook_active": False, **extra}).stdout.strip()
        return json.loads(out) if out else {}

    def test_no_run_never_blocks(self) -> None:  # negative control: the owner's dialogue is never held
        self.assertNotIn("decision", self.stop())

    def test_run_blocks_once_per_state_and_ends_when_nothing_is_left(self) -> None:
        self.auto("start", "--words", "работай без меня", "--orders", "N001,N002")
        first = self.stop()
        self.assertEqual(first.get("decision"), "block")
        self.assertIn("работай без меня", first["reason"])
        second = self.stop()  # nothing moved since the nudge: the stop goes through, the owner is told
        self.assertNotIn("decision", second)
        self.assertIn("paused", second.get("systemMessage", ""))
        self.p.order("take", "N001")  # the work moved: one more nudge
        self.assertEqual(self.stop().get("decision"), "block")
        for oid in ("N001", "N002"):
            self.p.order("hold", oid, "--why", "test")
        done = self.stop()
        self.assertNotIn("decision", done)
        self.assertIn("autonomous run done", done.get("systemMessage", ""))
        self.assertIn("done", self.auto("status").stdout)

    def test_background_work_and_owner_states_are_not_blocked(self) -> None:
        self.auto("start", "--words", "решай сам")
        self.assertNotIn("decision", self.stop(background_tasks=[{"id": "b1", "status": "running"}]))
        self.auto("stop", "--why", "стоп")
        self.assertNotIn("decision", self.stop())

    def test_questions_wait_for_the_owner(self) -> None:
        self.auto("start", "--words", "решай сам")
        self.auto("ask", "Какой период брать?", "--guess", "2025")
        self.assertIn("questions for the owner: 1", run([str(TOOLS / "step.py"), "--brief"], cwd=self.p.dir).stdout)
        self.auto("answer", "1", "2024")
        self.assertNotIn("questions for the owner", run([str(TOOLS / "step.py"), "--brief"], cwd=self.p.dir).stdout)


class WindowsIO(unittest.TestCase):
    """1.1.1: hooks read Claude Code's raw UTF-8 JSON and print UTF-8 whatever the Windows locale is (cp1251 on the owner's
    machine; the other tests send ASCII-escaped JSON and miss it), and the scanner command keeps Windows paths."""

    @staticmethod
    def raw(script: Path, payload: dict, cwd: Path, *args: str, extra: dict | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        return subprocess.run([PY, str(script), *args], cwd=cwd, input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              capture_output=True, env={**env, **(extra or {})})

    def test_k15_deny_names_a_non_ascii_folder_in_utf8(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="forge_k15_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        folder = tmp / "Проект_é"  # é is outside cp1251: printing it in the locale would raise, and the guard fails open
        folder.mkdir()
        pol = tmp / "policy.json"
        call = {"tool_name": "Bash", "tool_input": {"command": "codex exec 'fix it'"}, "cwd": str(folder)}
        pol.write_text(json.dumps({"deny": [str(folder)]}, ensure_ascii=False), encoding="utf-8")
        r = self.raw(GATES / "gpt_guard.py", call, folder, "--hook", extra={"FORGE_GPT_POLICY": str(pol)})
        out = r.stdout.decode("utf-8")  # raises when the deny went out in the locale's code page
        self.assertIn('"permissionDecision": "deny"', out); self.assertIn("Проект_é", out)
        pol.write_text(json.dumps({"deny": []}), encoding="utf-8")  # negative control: not denied, not a Forge project
        r = self.raw(GATES / "gpt_guard.py", call, folder, "--hook", extra={"FORGE_GPT_POLICY": str(pol)})
        self.assertEqual(r.stdout, b"", r.stderr.decode("utf-8", "replace"))

    def test_russian_knob_fires_from_raw_utf8(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="forge_knobs_"))
        r = self.raw(TOOLS / "prompt_knobs.py", {"prompt": "работай дальше без меня"}, tmp)
        out = r.stdout.decode("utf-8")  # raises when the hook printed in the locale's code page
        self.assertIn("autonomous", out); self.assertIn("«Запустить»", out)
        r = self.raw(TOOLS / "prompt_knobs.py", {"prompt": "не работай без меня, я рядом"}, tmp)  # negative control: a negation
        self.assertEqual(r.stdout, b"")

    def test_check_file_finds_a_cyrillic_path(self) -> None:
        p = Project()
        try:
            f = p.dir / "Заметка_é.md"  # é is outside cp1251 - the message naming the file must still go out as UTF-8
            data = {"tool_name": "Write", "tool_input": {"file_path": str(f)}}
            f.write_bytes("Купить на сайте\r\n".encode("utf-8"))  # CR line endings: said only when the hook finds the file
            r = self.raw(GATES / "check_file.py", data, p.dir, "--hook")
            err = r.stderr.decode("utf-8")  # raises when the message went out in the locale's code page
            self.assertEqual(r.returncode, 2, err); self.assertIn("CR line endings", err); self.assertIn("Заметка_é.md", err)
            f.write_bytes("Купить на сайте\n".encode("utf-8"))  # negative control: the same file with LF passes
            r = self.raw(GATES / "check_file.py", data, p.dir, "--hook")
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        finally:
            p.cleanup()

    def test_scanner_argv_strips_one_pair_of_quotes_on_windows_only(self) -> None:
        import skills_map
        f = Path("F")
        self.assertEqual(skills_map.scanner_argv(r'"C:\a b\py.exe" "C:\x\scan.py" {path}', f, nt=True),
                         [r"C:\a b\py.exe", r"C:\x\scan.py", "F"])
        self.assertEqual(skills_map.scanner_argv('--tag=a"b"c {path}', f, nt=True), ['--tag=a"b"c', "F"])  # inner quotes stay
        self.assertEqual(skills_map.scanner_argv("""'"x"' {path}""", f, nt=False), ['"x"', "F"])  # POSIX: shlex's result as is


if __name__ == "__main__":
    unittest.main()
