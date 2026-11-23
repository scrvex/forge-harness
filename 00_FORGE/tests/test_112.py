"""1.1.2 tests (N028, the owner: «не ломай кодекс во время работы»): Forge's Codex runs never silently break the owner's Codex
desktop app. The folder rights of %LOCALAPPDATA%\\Packages are mocked by FORGE_PACKAGES_SDDL (any OS); FORGE_HOME keeps the
block, the log and the incidents in a temp folder. Every guard is tested both ways: the case it must catch and the case it
must leave alone."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import PY, TOOLS, Project, run  # noqa: E402
from test_100 import FAKE, git, set_cfg  # noqa: E402

sys.path.insert(0, str(TOOLS))
import codex_safety  # noqa: E402
import gpt_bridge  # noqa: E402
import setup as forge_setup  # noqa: E402  (tools/setup.py)

USER = "S-1-5-21-3656497070-88922669-3169851793-1001"
HEALTHY = f"D:PAI(A;OIIO;FA;;;{USER})(A;CI;FA;;;{USER})(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)"  # the owner's folder after the fix
BROKEN = f"D:P(A;OIIO;FA;;;{USER})(A;CI;FA;;;{USER})"  # 2026-10-15: inheritance off, only the user's ACE
ENV_KEYS = ("FORGE_HOME", "FORGE_PACKAGES_SDDL", "CODEX_HOME", "LOCALAPPDATA", "FORGE_GPT_POLICY")


def fix_for(local: Path) -> str:
    return f'icacls "{local / "Packages"}" /grant "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F"'


class Env:
    """A temp FORGE_HOME, CODEX_HOME (with a sandbox log), LOCALAPPDATA and a stand-in ACL file, restored afterwards."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="forge_n028_"))
        self.saved = {k: os.environ.get(k) for k in ENV_KEYS}
        self.rollback = os.environ.pop("FORGE_GPT_WINDOWS_SANDBOX", None)  # the owner's rollback must not steer the tests
        self.sddl = self.tmp / "packages.sddl"
        self.sddl.write_text(HEALTHY, encoding="utf-8")
        self.home = self.tmp / "forge"
        self.local = self.tmp / "Local"
        sandbox = self.tmp / "codex" / ".sandbox"
        sandbox.mkdir(parents=True)
        (sandbox / "sandbox.2026-10-15.log").write_text("granting read ACE to C:\\Users\\x\\AppData for sandbox users\n",
                                                         encoding="utf-8", newline="\n")
        (self.tmp / "policy.json").write_text(json.dumps({"deny": []}), encoding="utf-8")
        os.environ.update(FORGE_HOME=str(self.home), FORGE_PACKAGES_SDDL=str(self.sddl), CODEX_HOME=str(self.tmp / "codex"),
                          LOCALAPPDATA=str(self.local), FORGE_GPT_POLICY=str(self.tmp / "policy.json"))

    def tearDown(self) -> None:
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.rollback is not None:
            os.environ["FORGE_GPT_WINDOWS_SANDBOX"] = self.rollback
        shutil.rmtree(self.tmp, ignore_errors=True)

    def phases(self) -> list[str]:
        f = self.home / "codex_acl.jsonl"
        return [json.loads(x)["phase"] for x in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []


class Check(Env, unittest.TestCase):
    def state(self, sddl: str) -> dict:
        self.sddl.write_text(sddl, encoding="utf-8")
        return codex_safety.check()

    def test_sids_decide_never_names(self) -> None:
        r = self.state(HEALTHY)
        self.assertEqual((r["state"], r["missing"], r["protected"]), ("ok", [], True))
        self.assertEqual(r["fix"], fix_for(self.local))                                     # the exact owner's command
        self.assertEqual(self.state("D:AI(A;OICIID;FA;;;S-1-5-18)(A;OICIID;FA;;;S-1-5-32-544)")["state"], "ok")  # full SIDs
        r = self.state(BROKEN)                                                              # the 2026-10-15 state
        self.assertEqual((r["state"], r["missing"]), ("broken", ["SYSTEM", "Administrators"]))
        r = self.state(f"D:P(D;OICI;FA;;;SY)(A;OICIIO;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{USER})")
        self.assertEqual(r["missing"], ["SYSTEM"])                                          # a deny or inherit-only ACE is no right here
        r = self.state(f"D:(A;OICI;FA;;;{USER})(A;OICI;FA;;;SY)(A;OICI;FA;;;S-1-5-32-545)")
        self.assertEqual(r["missing"], ["Administrators"])                                  # Users (545) is not Administrators (544)
        r = self.state("D:AI(XA;;FA;;;WD;(Member_of {SID(BA)}))(A;;FA;;;SY)(A;;FA;;;BA)")
        self.assertEqual(r["state"], "ok")                                                  # a conditional ACE does not derail parsing
        self.assertEqual(self.state("D:(A;CIOI;FA;;;SY)(A;OICI;FA;;;BA)")["state"], "ok")   # flags are pairs: CI+OI is not IO

    def test_reader_error_fails_open_and_other_systems_are_skipped(self) -> None:
        os.environ["FORGE_PACKAGES_SDDL"] = str(self.tmp / "missing.sddl")
        r = codex_safety.check()
        self.assertEqual(r["state"], "unknown"); self.assertIn("cannot read", r["note"])
        self.assertEqual(codex_safety.before_run("probe")["state"], "unknown")               # no block on our own failure
        self.assertEqual(self.phases(), ["before"])
        os.environ.pop("FORGE_PACKAGES_SDDL")
        with mock.patch.object(codex_safety, "on_windows", return_value=False):
            self.assertEqual(codex_safety.check()["state"], "skipped")                       # macOS/Linux: nothing to check
        if os.name == "nt":
            self.assertEqual(codex_safety.check()["state"], "skipped")                       # this temp LOCALAPPDATA has no Packages

    @staticmethod
    def acl_writers(src: str) -> list[str]:
        apis = ("SetNamedSecurityInfo", "SetSecurityInfo", "SetFileSecurity", "SetEntriesInAcl", "Set-Acl", "LookupAccountSid",
                "NTAccount")
        return [a for a in apis if a in src] + re.findall(r"subprocess\.\w+\([^)]*icacls", src)

    def test_never_changes_an_acl(self) -> None:
        for bad in ('advapi.SetNamedSecurityInfoW(p, 1, 4, None, None, acl, None)', 'subprocess.run(["icacls", p, "/grant"])'):
            self.assertTrue(self.acl_writers(bad), bad)                                     # the scan catches a writer...
        self.assertEqual(self.acl_writers((TOOLS / "codex_safety.py").read_text(encoding="utf-8")), [])  # ...there is none
        self.sddl.write_text(BROKEN, encoding="utf-8")
        piece = forge_setup.packages_acl_piece()
        self.assertFalse(piece["ok"]); self.assertEqual(piece["install"], [])               # setup.py install never runs it
        self.assertIn(fix_for(self.local), piece["manual"])

    @unittest.skipUnless(os.name == "nt", "the Windows API reader")
    def test_real_reader_on_this_pc(self) -> None:
        local = Path(self.saved["LOCALAPPDATA"] or Path.home() / "AppData" / "Local")
        sddl = codex_safety.read_sddl(local)                                                # read only
        self.assertTrue(sddl.startswith("D:"), sddl[:40])
        self.assertTrue(codex_safety.parse_dacl(sddl)[1])


class Guard(Env, unittest.TestCase):
    def test_preflight_refuses_in_russian_with_the_fix(self) -> None:
        self.assertEqual(codex_safety.before_run("codex_exec run N001 T1")["state"], "ok")
        self.sddl.write_text(BROKEN, encoding="utf-8")
        with self.assertRaises(codex_safety.CodexBlocked) as cm:
            codex_safety.before_run("codex_exec run N001 T1")
        msg = str(cm.exception)
        for part in ("Codex не запущен", "нет прав для SYSTEM и Администраторов", "PowerShell", fix_for(self.local),
                     "только владелец"):
            self.assertIn(part, msg)
        self.assertFalse(codex_safety.block_file().exists())                               # refusing is not blaming a run

    def test_transition_is_named_blocked_recorded_and_lifted_after_the_fix(self) -> None:
        p = Project()
        try:
            label = "codex_exec run N001 T1"
            before = codex_safety.before_run(label)
            self.sddl.write_text(BROKEN, encoding="utf-8")                                    # what the sandbox did meanwhile
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                alert = codex_safety.after_run(before, label, {"order": "N001", "windows_sandbox": "elevated",
                                                               "tasks": [{"id": "T1", "thread": "th-1"}]},
                                               root=p.dir, evidence=["01_PROJECT_CORE/circle/answers/N001_codex.json"])
            self.assertTrue(alert)
            text = err.getvalue()
            for part in ("ВНИМАНИЕ", label, "SYSTEM и Администраторов нет", "Причина не доказана", "windows.sandbox=elevated",
                         fix_for(self.local), alert["incident"]):
                self.assertIn(part, text)
            inc = json.loads(Path(alert["incident"]).read_text(encoding="utf-8"))
            self.assertEqual((inc["before"]["sddl"], inc["after"]["sddl"], inc["details"]["order"]), (HEALTHY, BROKEN, "N001"))
            self.assertIn("granting read ACE", inc["sandbox_logs"][0]["tail"])               # copied: Codex rotates its logs
            lesson = json.loads(Path(alert["lesson"]).read_text(encoding="utf-8"))
            self.assertEqual((lesson["applies_to"], lesson["source"], lesson["order"], lesson["confidence"]),
                             ("kernel", "gate", "N001", "medium"))                         # a coincidence in time, not a proof
            for part in (alert["incident"], "N001_codex.json", ".sandbox"):
                self.assertIn(part, lesson["evidence"])
            self.assertIn("windows.sandbox=elevated", lesson["root_cause"]); self.assertIn("not a proof", lesson["root_cause"])
            self.assertIn(lesson["id"], (p.docs / "KERNEL_PROPOSALS.md").read_text(encoding="utf-8"))  # a proposal to Forge
            with self.assertRaises(codex_safety.CodexBlocked) as cm:                         # no further Codex run...
                codex_safety.before_run("codex_exec run N001 T2")
            self.assertIn(label, str(cm.exception)); self.assertIn(alert["incident"], str(cm.exception))
            self.sddl.write_text(HEALTHY, encoding="utf-8")                                   # ...until the owner fixes it
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(codex_safety.before_run("codex_exec run N001 T2")["state"], "ok")
            self.assertIn("блок Codex снят", err.getvalue())
            self.assertFalse(codex_safety.block_file().exists()); self.assertTrue(Path(alert["incident"]).is_file())
            self.assertEqual(self.phases(), ["before", "after", "alert", "refused", "before", "lifted"])
        finally:
            p.cleanup()

    def test_overlapping_runs_are_named_and_no_project_means_no_lesson(self) -> None:
        self.home.mkdir(parents=True)
        other = {"ts": datetime.now().isoformat(timespec="seconds"), "phase": "before", "run": "gpt_bridge audit:N007", "pid": 1,
                 "state": "ok"}  # another Forge process started a Codex run and has not finished
        (self.home / "codex_acl.jsonl").write_text(json.dumps(other) + "\n", encoding="utf-8")
        before = codex_safety.before_run("gpt_bridge advise")
        self.sddl.write_text(BROKEN, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            alert = codex_safety.after_run(before, "gpt_bridge advise", {"windows_sandbox": "elevated"}, root=None)
        self.assertIsNone(alert["lesson"])
        self.assertIn("не записан — запуск вне проекта Forge", err.getvalue())
        self.assertIn("gpt_bridge audit:N007 (pid 1", err.getvalue())                       # it ran in the same window
        inc = json.loads(Path(alert["incident"]).read_text(encoding="utf-8"))
        self.assertEqual(len(inc["details"]["overlapping"]), 1)

    def test_no_alert_without_a_proven_transition(self) -> None:
        ok = codex_safety.before_run("a")
        self.assertIsNone(codex_safety.after_run(ok, "a", {}))                               # ok -> ok: silent
        self.sddl.write_text(BROKEN, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(codex_safety.after_run({"state": "unknown"}, "b", {}))         # unknown -> broken: not this run's
        self.assertIn("проверить не удалось", err.getvalue()); self.assertNotIn("ВНИМАНИЕ", err.getvalue())
        self.assertFalse(codex_safety.block_file().exists()); self.assertFalse((self.home / "codex_acl").exists())
        with self.assertRaises(codex_safety.CodexBlocked):
            codex_safety.before_run("c")                                                     # but the next run is refused

    def test_block_stays_while_the_fix_cannot_be_confirmed(self) -> None:
        self.home.mkdir(parents=True)
        codex_safety.block_file().write_text(json.dumps({"run": "gpt_bridge audit:N007", "when": "2026-10-16T10:00:00",
                                                         "incident": "x.json", "fix": fix_for(self.local)}), encoding="utf-8")
        os.environ["FORGE_PACKAGES_SDDL"] = str(self.tmp / "missing.sddl")                  # unreadable now
        with self.assertRaises(codex_safety.CodexBlocked) as cm:
            codex_safety.before_run("d")
        for part in ("gpt_bridge audit:N007", "удалите файл", str(codex_safety.block_file()), fix_for(self.local)):
            self.assertIn(part, str(cm.exception))


class CodexExecGuard(Env, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.p = Project()
        self.env = {"FORGE_CODEX": FAKE, "FAKE_CODEX_MODE": "ok", **{k: os.environ[k] for k in ENV_KEYS}}
        git(self.p, "init", "-q"); git(self.p, "config", "user.email", "t@example.com"); git(self.p, "config", "user.name", "t")
        set_cfg(self.p, gpt="allowed"); git(self.p, "add", "-A"); git(self.p, "commit", "-q", "-m", "init")
        self.p.order("new", "F", "--tier", "CODEX", "--changes", "x"); self.p.order("take", "N001")

    def tearDown(self) -> None:
        self.p.cleanup()
        super().tearDown()

    def cx(self, *args: str, ok: bool = True, **env: str) -> subprocess.CompletedProcess:
        return run([str(TOOLS / "codex_exec.py"), *args], cwd=self.p.dir, ok=ok, env={**self.env, **env})

    def test_broken_rights_refuse_before_codex_starts(self) -> None:
        self.sddl.write_text(BROKEN, encoding="utf-8")
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/a.py", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Codex не запущен", r.stderr); self.assertIn(fix_for(self.local), r.stderr)
        self.assertFalse((self.p.circle / "answers" / "N001_codex.json").exists())          # the fake was never started

    def test_a_run_that_breaks_the_rights_is_named_blocked_and_kept(self) -> None:
        answers = self.p.circle / "answers"
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/a.py", ok=False, FAKE_CODEX_ACL=BROKEN)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ВНИМАНИЕ", r.stderr); self.assertIn("codex_exec run N001 T1", r.stderr)
        self.assertIn("ВНИМАНИЕ: во время этого запуска Codex пропали права", r.stdout)       # the last word of the report too
        self.assertNotIn("K34d", r.stdout)                                                   # the guard's own lesson is not Codex's write
        self.assertTrue((answers / "N001_T1.patch").is_file())                               # the proposal itself is not lost
        task = json.loads((answers / "N001_codex.json").read_text(encoding="utf-8"))["runs"][-1]["tasks"][0]
        self.assertTrue(task["acl_alert"])
        lessons = [json.loads(f.read_text(encoding="utf-8")) for f in (self.p.circle / "lessons").glob("*.json")]
        self.assertTrue(any(x["applies_to"] == "kernel" and x["order"] == "N001" and "N001_codex.json" in x["evidence"]
                            for x in lessons))
        r = self.cx("fix", "N001", "T1", "--feedback", "again", ok=False)                    # blocked until fixed
        self.assertIn("Codex не запущен", r.stderr); self.assertIn("codex_exec run N001 T1", r.stderr)
        self.sddl.write_text(HEALTHY, encoding="utf-8")                                       # the owner ran the fix
        r = self.cx("fix", "N001", "T1", "--feedback", "again")
        self.assertIn("блок Codex снят", r.stderr); self.assertFalse(codex_safety.block_file().exists())

    def test_healthy_rights_change_nothing(self) -> None:
        r = self.cx("run", "N001", "--task", "t", "--scope", "src/a.py")
        self.assertNotIn("ВНИМАНИЕ", r.stdout + r.stderr)
        self.assertEqual(self.phases(), ["before", "after"])                                 # one reading each side of the run

    def test_a_wave_names_all_its_tasks(self) -> None:
        plan = {"order": "N001", "tasks": [{"id": "T1", "task": "a", "scope": ["src/a.py"], "depends": [], "status": "pending"},
                                           {"id": "T2", "task": "b", "scope": ["src/b.py"], "depends": [], "status": "pending"}]}
        (self.p.circle / "answers" / "N001_plan.json").write_text(json.dumps(plan), encoding="utf-8", newline="\n")
        r = self.cx("wave", "N001", ok=False, FAKE_CODEX_ACL=BROKEN)
        self.assertEqual(r.returncode, 1); self.assertIn("codex_exec run N001 T1,T2", r.stderr)
        statuses = [t["status"] for t in json.loads((self.p.circle / "answers" / "N001_plan.json").read_text(encoding="utf-8"))["tasks"]]
        self.assertEqual(statuses, ["proposed", "proposed"])                                 # the proposals stay for the Lead

    def test_an_interrupted_run_is_still_checked(self) -> None:
        import codex_exec
        from forge_common import load_cfg

        def boom(*a, **kw):  # the sandbox changes the rights, then the run dies
            self.sddl.write_text(BROKEN, encoding="utf-8")
            raise RuntimeError("interrupted")

        err = io.StringIO()
        with mock.patch.object(codex_exec, "run_task", side_effect=boom), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            codex_exec.propose(self.p.dir, load_cfg(self.p.dir), "N001", [{"id": "T1", "task": "t", "scope": ["src/a.py"]}],
                               slots=1, effort="low", model=None, timeout=60, plan_path=self.p.circle / "answers" / "none.json")
        self.assertIn("ВНИМАНИЕ", err.getvalue()); self.assertTrue(codex_safety.block_file().is_file())


class BridgeGuard(Env, unittest.TestCase):
    def test_bridge_call_is_bracketed(self) -> None:
        p = Project()
        real_run, calls = subprocess.run, []

        def fake_run(cmd, **kw):  # the Codex CLI: answers, and its "sandbox" rewrites the folder rights
            if cmd[0] != "codex":
                return real_run(cmd, **kw)  # lesson.py and anything else really runs
            calls.append(cmd)
            Path(cmd[cmd.index("-o") + 1]).write_text('{"verdict": "APPROVE"}', encoding="utf-8")
            self.sddl.write_text(BROKEN, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"type": "thread.started", "thread_id": "th-b"}\n', stderr="")

        wd = gpt_bridge.new_workdir("n028test")
        try:
            err = io.StringIO()
            with mock.patch.object(gpt_bridge, "find_codex", return_value="codex"), \
                    mock.patch.object(gpt_bridge.subprocess, "run", side_effect=fake_run), contextlib.redirect_stderr(err):
                res = gpt_bridge.run("audit this", wd, tag="audit:N001", log_near=p.dir)
                self.assertTrue(res["ok"]); self.assertTrue(res["acl_alert"])
                self.assertIn("gpt_bridge audit:N001", err.getvalue())
                if os.name == "nt":
                    self.assertIn('windows.sandbox="unelevated"', calls[0])                  # N029: read-only audits run unelevated
                with self.assertRaises(gpt_bridge.PolicyError):
                    gpt_bridge.run("again", wd, tag="audit:N001", log_near=p.dir)
            self.assertEqual(len(calls), 1)                                                  # the second call never started Codex
            self.assertIn("Codex не запущен", err.getvalue())
            lessons = [json.loads(f.read_text(encoding="utf-8")) for f in (p.circle / "lessons").glob("*.json")]
            self.assertTrue(any(x["order"] == "N001" and "gpt_usage.jsonl" in x["evidence"] for x in lessons))
        finally:
            shutil.rmtree(wd, ignore_errors=True)
            p.cleanup()

    def test_interrupted_bridge_call_is_still_checked(self) -> None:
        def dies(cmd, **kw):  # the "sandbox" changes the rights, then the CLI cannot be waited for
            self.sddl.write_text(BROKEN, encoding="utf-8")
            raise OSError("the Codex process vanished")

        wd = gpt_bridge.new_workdir("n028test")
        try:
            with mock.patch.object(gpt_bridge, "find_codex", return_value="codex"), \
                    mock.patch.object(gpt_bridge, "usage_log", return_value=self.tmp / "gpt_usage.jsonl"), \
                    mock.patch.object(gpt_bridge.subprocess, "run", side_effect=dies), contextlib.redirect_stderr(io.StringIO()) as err, \
                    self.assertRaises(OSError):
                gpt_bridge.run("audit this", wd, tag="advise")
            self.assertIn("ВНИМАНИЕ", err.getvalue()); self.assertTrue(codex_safety.block_file().is_file())
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class SandboxModes(unittest.TestCase):
    """N029 (owner's «делай» after the N028 measurement): only the measured path - gpt_bridge read-only runs - is unelevated."""

    def test_read_only_bridge_runs_unelevated_the_rest_unchanged(self) -> None:
        import codex_exec
        saved = os.environ.pop("FORGE_GPT_WINDOWS_SANDBOX", None)
        env = os.environ
        err = io.StringIO()
        try:
            with mock.patch.object(gpt_bridge.os, "name", "nt"), contextlib.redirect_stderr(err):  # string lookups only: any OS
                modes = (gpt_bridge.windows_sandbox("read-only"), gpt_bridge.windows_sandbox("workspace-write"),
                         codex_exec.windows_sandbox({}), codex_exec.windows_sandbox({"code": {"codex_windows_sandbox": ""}}))
                overrides = []
                for value in ("elevated", " Unelevated ", '"elevated"', "none", "", "bogus"):   # the owner's rollback words
                    env["FORGE_GPT_WINDOWS_SANDBOX"] = value
                    overrides.append(gpt_bridge.windows_sandbox("read-only"))
            env.pop("FORGE_GPT_WINDOWS_SANDBOX")
            with mock.patch.object(gpt_bridge.os, "name", "posix"):
                elsewhere = [gpt_bridge.windows_sandbox("read-only")]
                env["FORGE_GPT_WINDOWS_SANDBOX"] = "elevated"                                   # no override reaches non-Windows
                elsewhere.append(gpt_bridge.windows_sandbox("read-only"))
        finally:
            env.pop("FORGE_GPT_WINDOWS_SANDBOX", None)
            if saved is not None:
                env["FORGE_GPT_WINDOWS_SANDBOX"] = saved
        self.assertEqual(modes, ("unelevated", "elevated", "elevated", ""))                  # images and code tasks keep elevated
        self.assertEqual(overrides, ["elevated", "unelevated", "elevated", "", "", "unelevated"])  # a typo falls back...
        self.assertIn("'bogus' is not elevated, unelevated or none", err.getvalue())         # ...and says so
        self.assertEqual(elsewhere, ["", ""])


class VerifyGptTree(Env, unittest.TestCase):
    def test_the_guards_own_lesson_is_not_a_codex_write(self) -> None:
        import verify_gpt
        p, cwd, workdirs = Project(), Path.cwd(), []
        try:
            git(p, "init", "-q"); git(p, "config", "user.email", "t@example.com"); git(p, "config", "user.name", "t")
            set_cfg(p, gpt="allowed")
            (p.dir / "src").mkdir(exist_ok=True); (p.dir / "src" / "a.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
            p.order("new", "F", "--tier", "LEAD", "--changes", "x"); git(p, "add", "-A"); git(p, "commit", "-q", "-m", "init")

            def audit(alert: str, n: int) -> list[str]:
                def fake_run(prompt, wd, **kw):  # the rights guard wrote its lesson; src/other_n.py is a real change
                    workdirs.append(wd)
                    (p.circle / "lessons" / f"2026-10-15_L90{n}.json").write_text("{}\n", encoding="utf-8", newline="\n")
                    (p.docs / "KERNEL_PROPOSALS.md").write_text(f"- L90{n}\n", encoding="utf-8", newline="\n")
                    (p.dir / "src" / f"other_{n}.py").write_text("y = 2\n", encoding="utf-8", newline="\n")
                    return {"ok": True, "parsed": {"verdict": "APPROVE", "dod": [], "findings": [], "usefulness": ""}, "seconds": 1.0,
                            "model": "m", "effort": "high", "usage": {}, "error": "", **({"acl_alert": alert} if alert else {})}
                with mock.patch.object(verify_gpt.gpt_bridge, "run", side_effect=fake_run):
                    return verify_gpt.audit_order("N001", files=["src/a.py"])["_meta"]["tree_changed_during_run"]

            os.chdir(p.dir)
            self.assertEqual(audit("incident.json", 1), ["src/other_1.py"])                 # the guard's files left out...
            moved = audit("", 2)                                                             # ...only when the guard wrote them
            self.assertIn("01_PROJECT_CORE/circle/lessons/2026-10-15_L902.json", moved); self.assertIn("src/other_2.py", moved)
        finally:
            os.chdir(cwd)
            for wd in workdirs:
                shutil.rmtree(wd, ignore_errors=True)
            p.cleanup()


class Doctor(Env, unittest.TestCase):
    def doctor(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: os.environ[k] for k in ENV_KEYS}
        user = self.tmp / "user"  # an empty profile: this PC's Claude, plugins and settings stay out (as in test_100)
        env.update(USERPROFILE=str(user), HOME=str(user), APPDATA=str(user / "Roaming"), FORGE_CODEX="", CLAUDE_CODE_EFFORT_LEVEL="",
                   PATH=os.pathsep.join([str(Path(PY).parent), os.environ.get("SystemRoot", "C:/Windows") + "/System32"]))
        return run([str(TOOLS / "setup.py"), *args], cwd=self.tmp, env=env)

    def test_doctor_reports_and_install_never_repairs(self) -> None:
        self.assertIn("OK   packages acl", self.doctor("doctor").stdout)
        self.sddl.write_text(BROKEN, encoding="utf-8")
        out = self.doctor("doctor").stdout
        self.assertIn("MISS packages acl", out); self.assertIn("by hand: in PowerShell", out)
        hand = next(x for x in out.splitlines() if "by hand: in PowerShell" in x)
        self.assertTrue(hand.endswith(fix_for(self.local)))                                  # the command ends the line: copy-paste safe
        out = self.doctor("install", "--only", "packages acl").stdout
        self.assertIn("SKIP packages acl: by hand", out)                                     # printed for the owner, never run
        self.assertEqual(self.sddl.read_text(encoding="utf-8"), BROKEN)
        os.environ["FORGE_PACKAGES_SDDL"] = str(self.tmp / "missing.sddl")
        self.assertIn("??   packages acl", self.doctor("doctor").stdout)                     # unreadable: neither OK nor MISS


if __name__ == "__main__":
    unittest.main()
