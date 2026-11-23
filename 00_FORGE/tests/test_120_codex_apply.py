"""1.2.0 (N031, from ambatupipe "acceptance of the return"): `codex_exec.py apply` refuses a patch without test changes when tests
are required, runs the task's declared check (or the project's `code.test` when tests are required) on the patched result before
anything is written, and `--skip-checks` is journaled. Every check is tested both ways: the case it must catch and the case it
must leave alone. No network, no real Codex; the machine reading of `wave` is replaced (FORGE_MACHINE_SLOTS), so no test depends
on the load of the computer it runs on."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from test_100 import FAKE, git, set_cfg  # noqa: E402
from test_kernel import PY, TOOLS, Project, run  # noqa: E402

import codex_exec  # noqa: E402
import machine  # noqa: E402

SRC, TEST = "src/a.py", "tests/test_a.py"
CHECK_OK = 'import pathlib, sys; sys.exit(0 if "42" in pathlib.Path("src/a.py").read_text(encoding="utf-8") else 1)\n'
CHECK_BAD = "import sys; sys.exit(3)\n"
CHECK_DEPS = ("import pathlib, sys\n"
              "ok = pathlib.Path('node_modules/x/marker.txt').is_file() and pathlib.Path('.venv/marker.txt').is_file()\n"
              "sys.exit(0 if ok else 1)\n")
BUMP = "import os, pathlib\npathlib.Path(os.environ['REAL_SRC']).write_text('value = 7  # owner\\n', encoding='utf-8')\n"
SLEEPER = ("import os, subprocess, sys, time\n"
           "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
           "open(os.environ['SLEEPER_PIDFILE'], 'w').write(str(child.pid))\n"
           "time.sleep(120)\n")
WAIVER = "**Waiver (owner's words, K19):**"


class Helpers(unittest.TestCase):
    def test_is_test_path(self) -> None:
        for yes in ("tests/test_a.py", "src/test_x.py", "a/b_test.py", "web/app.test.ts", "web/app.spec.js", "pkg/__tests__/x.js",
                    "test/helper.py", "spec/models/user_spec.rb", "src/FooTest.java", "src/TestFoo.java", "Foo.Tests/BarTests.cs",
                    "app/foo_test.go", "spec/user.rb"):
            self.assertTrue(codex_exec.is_test_path(yes), yes)
        for no in ("src/a.py", "src/contest.py", "docs/testing.md", "src/latest.py", "docs/api_spec.md", "openapi-spec.yaml",
                   "docs/spec/overview.md", "src/Latest.java", "src/Contest.java", "src/FooTestimony.java"):
            self.assertFalse(codex_exec.is_test_path(no), no)

    def test_scope_holds_tests(self) -> None:
        for yes in ([SRC, TEST], ["tests"], ["tests/"], ["web/__tests__"], ["spec"], ["src/FooTest.java"]):
            self.assertTrue(codex_exec.scope_holds_tests(yes), yes)
        for no in ([SRC], ["docs/readme.md", "CHANGELOG.md"], ["src/"], []):
            self.assertFalse(codex_exec.scope_holds_tests(no), no)
        self.assertTrue(codex_exec.scope_holds_tests(["src/"], folders=True))                   # a new src/x.test.ts may be added there
        self.assertFalse(codex_exec.scope_holds_tests([SRC, "docs/readme.md"], folders=True))   # two files, none of them a test

    def test_declared_checks(self) -> None:
        self.assertEqual(codex_exec.declared_checks({"check": " a "}), ["a"])
        self.assertEqual(codex_exec.declared_checks({"checks": ["a", "", "b"]}), ["a", "b"])
        self.assertEqual(codex_exec.declared_checks({"check": ["a", "b"], "checks": "c"}), ["a", "b", "c"])
        self.assertEqual(codex_exec.declared_checks({"task": "x"}), [])
        for bad in ({"check": 5}, {"checks": [1]}, {"check": [["a"]]}, {"checks": {"a": 1}}):  # never a check that silently does not run
            with self.assertRaises(SystemExit) as cm:
                codex_exec.declared_checks({"id": "T9", **bad})
            self.assertIn("T9", str(cm.exception))

    def test_tests_required(self) -> None:
        md = "## DoD\n- the feature works\n"
        self.assertEqual(codex_exec.tests_required({}, md), "")
        self.assertTrue(codex_exec.tests_required({"tests": True}, md))
        self.assertTrue(codex_exec.tests_required({"tests": [TEST]}, md))
        self.assertTrue(codex_exec.tests_required({}, "## DoD\n- unit tests pass\n"))
        self.assertTrue(codex_exec.tests_required({}, "## DoD\n- unit tests pass\n", [SRC, TEST]))

    def test_explicit_false_switches_the_dod_rule_off(self) -> None:
        dod = "## DoD\n- all tests green\n"
        self.assertEqual(codex_exec.tests_required({"tests": False}, dod, [SRC, TEST]), "")
        self.assertEqual(codex_exec.tests_required({"tests": False}, dod), "")
        self.assertTrue(codex_exec.tests_required({"tests": True}, "## DoD\n- docs only, no tests needed\n"))  # the task says it
        for words in ("- docs only, no tests needed", "- без тестов, только README", "- existing tests stay green"):
            self.assertEqual(codex_exec.tests_required({"tests": False}, f"## DoD\n{words}\n", [SRC, TEST]), "", words)

    def test_dod_rule_needs_a_test_path_in_scope(self) -> None:
        dod = "## DoD\n- all tests green\n"
        self.assertEqual(codex_exec.tests_required({}, dod, ["docs/readme.md", "CHANGELOG.md"]), "")   # docs: nothing to add tests to
        self.assertTrue(codex_exec.tests_required({}, dod, [SRC, TEST]))
        self.assertIn("no test path", codex_exec.tests_note({}, dod, ["docs/readme.md"]))
        self.assertEqual(codex_exec.tests_note({}, dod, [SRC, TEST]), "")                               # it is required, no warning
        self.assertEqual(codex_exec.tests_note({"tests": False}, dod, ["docs/readme.md"]), "")          # the task said so itself
        self.assertEqual(codex_exec.tests_note({}, "## DoD\n- it works\n", ["docs/readme.md"]), "")

    def test_waiver_is_read_by_its_rule_word(self) -> None:
        waived = f"{WAIVER} tests: без тестов\n\n## DoD\n- tests pass\n"
        self.assertEqual(codex_exec.tests_required({}, waived), "")
        # a waiver of `review` that merely mentions "tests:" in the owner's words lifts nothing (N024)
        other = f"- {WAIVER} review: не надо ревью, tests: потом сам\n\n## DoD\n- tests pass\n"
        self.assertTrue(codex_exec.tests_required({}, other))
        self.assertFalse(codex_exec.tests_waived(other))
        self.assertTrue(codex_exec.tests_waived(f"## Amendments\n  - {WAIVER} tests: потом\n"))

    def test_dod_lines_added_by_amend_count(self) -> None:
        md = "## DoD\n- feature works\n\n## Amendments\n- **2026-10-17 10:00** - why\n  - [ ] unit tests cover the parser\n"
        self.assertTrue(codex_exec.tests_required({}, md))
        slow = "## DoD\n- feature works\n\n## Amendments\n- **2026-10-17 10:00** - the tests were slow, reordered\n"
        self.assertEqual(codex_exec.tests_required({}, slow), "")                                       # only `- [ ]` lines are DoD

    def test_tests_paths(self) -> None:
        self.assertEqual(codex_exec.declared_test_paths({"tests": [TEST, "web\\a.test.ts"]}), [TEST, "web/a.test.ts"])
        self.assertEqual(codex_exec.declared_test_paths({"tests": TEST}), [TEST])
        for none in ({"tests": True}, {"tests": False}, {}, {"tests": ""}):
            self.assertEqual(codex_exec.declared_test_paths(none), [])

    def test_plan_problems(self) -> None:
        ok = [{"id": "T1", "scope": [SRC, TEST], "tests": True}, {"id": "T2", "scope": ["docs/a.md"], "tests": False},
              {"id": "T3", "scope": ["docs/a.md"]}, {"id": "T4", "scope": ["src/"], "tests": True},
              {"id": "T5", "scope": [SRC, TEST], "tests": [TEST], "check": "x"}]
        self.assertEqual(codex_exec.plan_problems(ok), [])
        bad = codex_exec.plan_problems([{"id": "T1", "scope": [SRC], "tests": True}, {"id": "T2", "scope": [SRC], "tests": [TEST]},
                                        {"id": "T3", "scope": [SRC], "check": 5}, {"id": "T4", "scope": [SRC], "tests": True, "status": "applied"}])
        self.assertEqual(len(bad), 3, bad)
        self.assertIn("T1", bad[0]); self.assertIn("T2", bad[1]); self.assertIn("T3", bad[2])         # an applied task is not judged again

    def test_journal_line(self) -> None:
        self.assertEqual(codex_exec.one_line(" owner said\r\nship it \n\n CI runs elsewhere "), "owner said / ship it / CI runs elsewhere")


class Capacity(unittest.TestCase):
    OK = {"battery": False, "cpu_ratio": 0.1, "mem_free_pct": 60.0}

    def test_an_idle_desktop_keeps_the_requested_slots(self) -> None:
        for want in (2, 3, 4):
            self.assertEqual(codex_exec.wave_slots(want, machine.verdict(self.OK, want)), (want, ""))

    def test_the_laptop_cap_names_the_laptop_never_an_invented_reason(self) -> None:
        n, why = codex_exec.wave_slots(3, machine.verdict({**self.OK, "battery": True}, 3))
        self.assertEqual(n, 2); self.assertIn("laptop without active cooling", why); self.assertNotIn("no free slots", why)
        n, why = codex_exec.wave_slots(3, {"slots": 2, "verdict": "ok", "reasons": [], "laptop": False})   # nothing to name: say nothing
        self.assertEqual(n, 2); self.assertNotIn("no free slots", why); self.assertIn("3 -> 2", why)

    def test_the_wave_asks_the_reading_for_the_requested_slots(self) -> None:
        seen = []

        def fake_load(profile_slots=None):
            seen.append(profile_slots)
            return machine.verdict(self.OK, profile_slots)
        with mock.patch.dict(os.environ), mock.patch.object(machine, "load", side_effect=fake_load):
            os.environ.pop("FORGE_MACHINE_SLOTS", None)
            self.assertEqual(codex_exec.wave_capacity(4), (4, ""))
        self.assertEqual(seen, [4])

    def test_a_failing_probe_never_blocks_the_wave(self) -> None:
        with mock.patch.dict(os.environ), mock.patch.object(machine, "load", side_effect=RuntimeError("probe broke")):
            os.environ.pop("FORGE_MACHINE_SLOTS", None)
            n, why = codex_exec.wave_capacity(3)
        self.assertEqual(n, 3); self.assertIn("unavailable", why); self.assertIn("probe broke", why)

    def test_forced_slots_replace_the_reading(self) -> None:
        with mock.patch.dict(os.environ, {"FORGE_MACHINE_SLOTS": "1"}), mock.patch.object(machine, "load", side_effect=AssertionError):
            n, why = codex_exec.wave_capacity(3)
            self.assertEqual(n, 1); self.assertIn("FORGE_MACHINE_SLOTS=1", why)
        with mock.patch.dict(os.environ, {"FORGE_MACHINE_SLOTS": "0"}):
            self.assertEqual(codex_exec.wave_capacity(3)[0], 0)
        with mock.patch.dict(os.environ, {"FORGE_MACHINE_SLOTS": "9"}):
            self.assertEqual(codex_exec.wave_capacity(3), (3, ""))


class Apply(unittest.TestCase):
    def setUp(self) -> None:
        self.p = p = Project()
        home = p.tmp / "codex_home"; home.mkdir()
        self.prompt_file = p.tmp / "prompt.txt"
        self.env = {"FORGE_CODEX": FAKE, "FORGE_GPT_POLICY": str(p.tmp / "policy.json"), "FAKE_CODEX_MODE": "ok", "CODEX_HOME": str(home),
                    "FORGE_MACHINE_SLOTS": "3", "FAKE_CODEX_PROMPT_OUT": str(self.prompt_file)}
        (p.tmp / "policy.json").write_text(json.dumps({"deny": []}), encoding="utf-8")
        git(p, "init", "-q"); git(p, "config", "user.email", "t@example.com"); git(p, "config", "user.name", "t")
        set_cfg(p, gpt="allowed")
        (p.dir / "src").mkdir(); (p.dir / SRC).write_text("value = 1\n", encoding="utf-8")
        for name, text in (("check_ok.py", CHECK_OK), ("check_bad.py", CHECK_BAD), ("check_deps.py", CHECK_DEPS), ("bump.py", BUMP),
                           ("sleeper.py", SLEEPER)):
            (p.dir / name).write_text(text, encoding="utf-8")
        git(p, "add", "-A"); git(p, "commit", "-q", "-m", "init")
        p.order("new", "F", "--tier", "CODEX", "--changes", "x"); p.order("take", "N001")
        self.plan = p.circle / "answers" / "N001_plan.json"
        self.evidence = p.circle / "answers" / "N001_codex.json"
        self.ok, self.bad = f'"{PY}" check_ok.py', f'"{PY}" check_bad.py'

    def tearDown(self) -> None:
        self.p.cleanup()

    def cx(self, *args: str, ok: bool = True, env: dict | None = None):
        return run([str(TOOLS / "codex_exec.py"), *args], cwd=self.p.dir, ok=ok, env={**self.env, **(env or {})})

    def write_plan(self, scope: list[str], **fields) -> None:
        self.plan.write_text(json.dumps({"order": "N001", "tasks": [{"id": "T1", "task": "t", "scope": scope, **fields}]}), encoding="utf-8")

    def propose(self, scope: list[str], **fields) -> None:
        self.write_plan(scope, **fields)
        self.assertIn("T1 PROPOSED", self.cx("wave", "N001").stdout)

    def omit(self, path: str) -> None:
        """Codex answered without this file: drop it from the recorded proposal (the fake proposes every scope file)."""
        data = json.loads(self.evidence.read_text(encoding="utf-8"))
        del data["runs"][-1]["tasks"][0]["proposed"][path]
        self.evidence.write_text(json.dumps(data), encoding="utf-8")

    def set_dod(self, text: str) -> None:
        f = next((self.p.circle / "orders").glob("N001_*.md"))
        md = f.read_text(encoding="utf-8")
        md = re.sub(r"^## DoD[^\n]*\n.*?(?=^## |\Z)", f"## DoD\n{text}\n\n", md, count=1, flags=re.M | re.S)
        f.write_text(md, encoding="utf-8", newline="\n")

    def src(self) -> str:
        return (self.p.dir / SRC).read_text(encoding="utf-8")

    def status(self) -> str:
        return json.loads(self.plan.read_text(encoding="utf-8"))["tasks"][0]["status"]

    def journal(self) -> str:
        return "".join(x.read_text(encoding="utf-8") for x in (self.p.circle / "journal").glob("*.md"))

    def test_plain_task_applies_as_before(self) -> None:
        self.set_dod("- the value changes")
        self.propose([SRC])
        self.cx("apply", "N001", "T1")
        self.assertIn("42", self.src())

    # --- tests are required ---------------------------------------------------------------------------------------------
    def test_tests_field_refuses_a_patch_without_test_files(self) -> None:
        self.propose([SRC, TEST], tests=True, check=self.ok)
        self.omit(TEST)
        r = self.cx("apply", "N001", "T1", ok=False)
        self.assertIn("changes no test file", r.stderr); self.assertIn("--skip-checks", r.stderr)
        self.assertEqual(self.src(), "value = 1\n")                                           # nothing was written
        self.assertEqual(self.status(), "proposed")

    def test_tests_field_accepts_a_patch_with_a_test_file(self) -> None:
        self.propose([SRC, TEST], tests=True, check=self.ok)
        self.cx("apply", "N001", "T1")
        self.assertIn("42", self.src()); self.assertTrue((self.p.dir / TEST).is_file())

    def test_required_tests_must_also_run_not_only_exist(self) -> None:
        self.propose([SRC, TEST], tests=True)                                                 # no `check`, no code.test
        r = self.cx("apply", "N001", "T1", ok=False)
        self.assertIn("nothing would run them", r.stderr); self.assertIn("code.test", r.stderr); self.assertIn("--skip-checks", r.stderr)
        self.assertEqual(self.src(), "value = 1\n")

    def test_required_tests_run_the_project_test_command(self) -> None:
        set_cfg(self.p, code={"test": self.bad})
        self.propose([SRC, TEST], tests=True)
        r = self.cx("apply", "N001", "T1", ok=False)
        self.assertIn("declared check failed", r.stderr); self.assertIn("exit 3", r.stderr)
        self.assertEqual(self.src(), "value = 1\n"); self.assertFalse((self.p.dir / TEST).exists())     # the tree is untouched
        set_cfg(self.p, code={"test": self.ok})                                               # a green project command lets it through
        self.cx("apply", "N001", "T1")
        self.assertIn("42", self.src())
        ev = json.loads(self.evidence.read_text(encoding="utf-8"))
        hit = next(t for run_ in ev["runs"] for t in run_["tasks"] if t.get("applied"))
        self.assertEqual(hit["acceptance"], {"tests_required": True, "checks": [self.ok]})

    def test_declared_check_wins_over_the_project_test_command(self) -> None:
        set_cfg(self.p, code={"test": self.bad})
        self.propose([SRC, TEST], tests=True, check=self.ok)
        self.cx("apply", "N001", "T1")
        self.assertIn("42", self.src())

    def test_dod_mentioning_tests_requires_them_when_the_scope_can_hold_them(self) -> None:
        self.set_dod("- the value changes and the tests pass")
        self.propose([SRC, TEST], check=self.ok)
        self.omit(TEST)
        self.assertIn("DoD mentions tests", self.cx("apply", "N001", "T1", ok=False).stderr)
        self.assertEqual(self.src(), "value = 1\n")

    def test_dod_mentioning_tests_does_not_refuse_a_docs_only_task(self) -> None:
        self.set_dod("- the docs are updated and all tests stay green")
        self.propose(["docs/readme.md"])
        r = self.cx("apply", "N001", "T1")                                                    # nothing to refuse: it cannot hold a test
        self.assertIn("no test path", r.stderr); self.assertIn("warning", r.stderr)
        self.assertTrue((self.p.dir / "docs" / "readme.md").is_file())

    def test_explicit_tests_false_disables_the_rule(self) -> None:
        self.set_dod("- the value changes and the tests pass")
        self.propose([SRC, TEST], tests=False)
        self.omit(TEST)
        self.cx("apply", "N001", "T1")                                                        # no tests required, no check to run
        self.assertIn("42", self.src())

    def test_tests_paths_must_be_among_the_changed_files(self) -> None:
        self.propose([SRC, TEST], tests=[TEST], check=self.ok)
        self.omit(TEST)
        r = self.cx("apply", "N001", "T1", ok=False)
        self.assertIn("does not change tests/test_a.py", r.stderr)
        self.assertEqual(self.src(), "value = 1\n")

    def test_tests_paths_accept_the_named_file(self) -> None:
        self.propose([SRC, TEST], tests=[TEST], check=self.ok)
        self.cx("apply", "N001", "T1")
        self.assertTrue((self.p.dir / TEST).is_file())

    def test_amended_dod_lines_require_tests(self) -> None:
        self.set_dod("- the value changes")
        self.p.order("amend", "N001", "--why", "owner asked", "--dod", "unit tests cover the change")
        self.propose([SRC, TEST], check=self.ok)
        self.omit(TEST)
        self.assertIn("DoD mentions tests", self.cx("apply", "N001", "T1", ok=False).stderr)

    # --- the wave refuses a plan no result can satisfy -------------------------------------------------------------------
    def test_wave_refuses_tests_the_scope_cannot_hold(self) -> None:
        self.write_plan([SRC], tests=True)
        before = self.plan.read_text(encoding="utf-8")
        r = self.cx("wave", "N001", ok=False)
        self.assertIn("wave refused", r.stderr); self.assertIn("T1", r.stderr); self.assertIn("no test file or folder", r.stderr)
        self.assertFalse(self.evidence.exists()); self.assertEqual(self.plan.read_text(encoding="utf-8"), before)   # Codex never ran
        self.write_plan([SRC], tests=False)                                                   # the same scope with `tests: false` is fine
        self.assertIn("T1 PROPOSED", self.cx("wave", "N001").stdout)

    def test_wave_refuses_a_malformed_check(self) -> None:
        self.write_plan([SRC], check=5)
        r = self.cx("wave", "N001", ok=False)
        self.assertIn("wave refused", r.stderr); self.assertIn("`check`", r.stderr)
        self.assertFalse(self.evidence.exists())

    def test_wave_refuses_when_the_machine_has_no_slot(self) -> None:
        self.write_plan([SRC])
        before = self.plan.read_text(encoding="utf-8")
        r = self.cx("wave", "N001", ok=False, env={"FORGE_MACHINE_SLOTS": "0"})
        self.assertIn("wave refused", r.stderr); self.assertIn("FORGE_MACHINE_SLOTS=0", r.stderr)
        self.assertFalse(self.evidence.exists()); self.assertEqual(self.plan.read_text(encoding="utf-8"), before)

    def test_wave_slots_must_be_positive(self) -> None:
        self.write_plan([SRC])
        self.assertEqual(self.cx("wave", "N001", "--slots", "0", ok=False).returncode, 2)
        self.assertFalse(self.evidence.exists())

    def test_prompt_tells_the_executor_what_apply_will_demand(self) -> None:
        self.propose([SRC, TEST], tests=True, check=self.ok)
        prompt = self.prompt_file.read_text(encoding="utf-8")
        self.assertIn("must add or change tests", prompt); self.assertIn("Before applying, the Lead runs", prompt)
        self.assertIn("check_ok.py", prompt)
        self.propose([SRC])                                                                   # a plain task: nothing extra
        prompt = self.prompt_file.read_text(encoding="utf-8")
        self.assertNotIn("must add or change tests", prompt); self.assertNotIn("Before applying, the Lead runs", prompt)

    # --- declared checks ------------------------------------------------------------------------------------------------
    def test_failing_check_refuses_before_writing(self) -> None:
        self.propose([SRC], check=self.bad)
        r = self.cx("apply", "N001", "T1", ok=False)
        self.assertIn("declared check failed", r.stderr); self.assertIn("exit 3", r.stderr)
        self.assertEqual(self.src(), "value = 1\n")

    def test_passing_check_sees_the_patched_result_and_applies(self) -> None:
        # check_ok.py needs the PATCHED src/a.py (value = 42); the working tree still has value = 1 while it runs
        self.propose([SRC], checks=[self.ok])
        self.assertEqual(self.src(), "value = 1\n")
        self.cx("apply", "N001", "T1")
        self.assertIn("42", self.src())
        ev = json.loads(self.evidence.read_text(encoding="utf-8"))
        hit = next(t for run_ in ev["runs"] for t in run_["tasks"] if t.get("applied"))
        self.assertEqual(hit["acceptance"]["checks"], [self.ok])

    def test_a_check_given_as_a_list_runs(self) -> None:
        self.propose([SRC], check=[self.bad])
        self.assertIn("declared check failed", self.cx("apply", "N001", "T1", ok=False).stderr)

    def test_the_check_sees_the_project_environment(self) -> None:
        for d in ("node_modules/x", ".venv"):
            (self.p.dir / d).mkdir(parents=True)
        (self.p.dir / "node_modules" / "x" / "marker.txt").write_text("dep\n", encoding="utf-8")
        (self.p.dir / ".venv" / "marker.txt").write_text("env\n", encoding="utf-8")
        self.propose([SRC], check=f'"{PY}" check_deps.py')
        self.cx("apply", "N001", "T1")                                                        # dependency folders are linked into the copy
        self.assertIn("42", self.src())
        self.assertTrue((self.p.dir / "node_modules" / "x" / "marker.txt").is_file())         # and the originals survive the clean-up
        self.assertTrue((self.p.dir / ".venv" / "marker.txt").is_file())

    def test_a_check_that_hangs_is_killed_with_everything_it_started(self) -> None:
        pidfile = self.p.tmp / "child.pid"
        self.propose([SRC], check=f'"{PY}" sleeper.py')
        t0 = time.time()
        r = self.cx("apply", "N001", "T1", ok=False, env={"FORGE_CHECK_TIMEOUT": "3", "SLEEPER_PIDFILE": str(pidfile)})
        self.assertLess(time.time() - t0, 60)                                                 # it returned instead of waiting for the test
        self.assertIn("timed out after 3s", r.stderr)
        self.assertEqual(self.src(), "value = 1\n")
        self.assertTrue(pidfile.is_file(), "the check never started its child")
        pid = int(pidfile.read_text(encoding="utf-8"))
        for _ in range(100):
            if not codex_exec.pid_alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(codex_exec.pid_alive(pid), "the grandchild of the check is still running")

    def test_files_changed_while_the_check_ran_are_not_overwritten(self) -> None:
        real = self.p.dir / SRC
        self.propose([SRC], check=f'"{PY}" bump.py')                                          # the check "edits" the real file meanwhile
        r = self.cx("apply", "N001", "T1", ok=False, env={"REAL_SRC": str(real)})
        self.assertIn("changed since Codex proposed", r.stderr)
        self.assertEqual(self.src(), "value = 7  # owner\n")                                  # the other change is kept

    def test_hash_check_still_comes_first(self) -> None:
        self.propose([SRC, TEST], tests=True, check=self.ok)
        (self.p.dir / SRC).write_text("value = 2  # someone else\n", encoding="utf-8")
        self.assertIn("changed since Codex proposed", self.cx("apply", "N001", "T1", ok=False).stderr)

    # --- --skip-checks --------------------------------------------------------------------------------------------------
    def test_skip_checks_overrides_and_journals_the_reason(self) -> None:
        self.propose([SRC, TEST], tests=True, check=self.bad)
        self.cx("apply", "N001", "T1", "--skip-checks", "  ", ok=False)                      # a blank reason is no reason
        self.assertEqual(self.src(), "value = 1\n")
        self.cx("apply", "N001", "T1", "--skip-checks", "owner said ship it, CI runs elsewhere")
        self.assertIn("42", self.src())
        self.assertIn("[N001] T1: apply checks skipped (--skip-checks): owner said ship it, CI runs elsewhere", self.journal())
        ev = json.loads(self.evidence.read_text(encoding="utf-8"))
        self.assertTrue(any(t.get("checks_skipped") for run_ in ev["runs"] for t in run_["tasks"]))

    def test_a_many_line_reason_stays_one_journal_line(self) -> None:
        self.propose([SRC], check=self.bad)
        reason = "owner said ship it\nCI runs elsewhere"
        self.cx("apply", "N001", "T1", "--skip-checks", reason)
        self.assertIn("(--skip-checks): owner said ship it / CI runs elsewhere\n", self.journal())
        ev = json.loads(self.evidence.read_text(encoding="utf-8"))
        self.assertTrue(any(t.get("checks_skipped") == reason for run_ in ev["runs"] for t in run_["tasks"]))   # the evidence keeps the words

    def test_skip_checks_without_anything_to_skip_leaves_no_journal_line(self) -> None:
        self.set_dod("- the value changes")
        self.propose([SRC])
        self.cx("apply", "N001", "T1", "--skip-checks", "just because")
        self.assertNotIn("checks skipped", self.journal())


if __name__ == "__main__":
    unittest.main()
