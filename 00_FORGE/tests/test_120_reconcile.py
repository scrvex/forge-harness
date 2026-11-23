"""1.2.0 tests (N031, ambatupipe "pick-up after a break"): reconcile.py compares the declared state with git facts, read-only.
Every check has a negative control (the consistent case stays silent): a line fires in one case and is silent in its twin."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import TOOLS, Project, run  # noqa: E402
from test_100 import git  # noqa: E402

sys.path.insert(0, str(TOOLS))
import machine as machine_mod  # noqa: E402
import reconcile as rec  # noqa: E402
import step as step_mod  # noqa: E402

RECONCILE = str(TOOLS / "reconcile.py")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class Reconcile(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()
        self.p.order("new", "Работа", "--tier", "HEAVY", "--changes", "x")
        write(self.p.dir / "src" / "a.txt", "a\n")
        git(self.p, "init", "-q"); git(self.p, "config", "user.email", "t@example.com"); git(self.p, "config", "user.name", "t")
        self.commit("init", ago=600)  # dated 10 minutes back: the take below is later than it without a sleep

    def tearDown(self) -> None:
        self.p.cleanup()

    # --- helpers ---------------------------------------------------------------------------------------------------
    def commit(self, msg: str, *paths: str, ago: int = 0, p: Project | None = None) -> None:
        p = p or self.p
        when = f"{int(time.time()) - ago} +0000"
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        for args in (["add", *(paths or ("-A",))], ["commit", "-q", "-m", msg]):
            r = subprocess.run(["git", *args], cwd=p.dir, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def rec(self, *a: str):
        return run([RECONCILE, *a], cwd=self.p.dir, ok=False)

    def lines(self, *a: str) -> str:
        return self.rec(*a).stdout

    def answers(self, name: str, data: dict) -> None:
        write(self.p.circle / "answers" / name, json.dumps(data))

    def plan(self, status: str, scope: list[str], **extra) -> None:
        self.answers("N001_plan.json", {"order": "N001", "tasks": [{"id": "T1", "scope": scope, "status": status}], **extra})

    def codex(self, *tasks_per_run: list[dict]) -> None:
        self.answers("N001_codex.json", {"order": "N001", "runs": [{"kind": "proposal", "tasks": t} for t in tasks_per_run]})

    def edit(self, rel: str, text: str = "changed\n") -> None:
        write(self.p.dir / rel, text)

    def set_state(self, **kv: str) -> None:
        fields = {"STATE": "READY", "UPDATED": "2026-10-17 10:00", "CURRENT_ORDER": "-", "NOTE": "-", **kv}
        write(self.p.circle / "STATE.md", "".join(f"{k}: {v}\n" for k, v in fields.items()))

    def ok(self, *a: str) -> None:
        r = self.rec(*a)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def flagged(self, needle: str, *a: str) -> None:
        r = self.rec(*a)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(needle, r.stdout)

    # --- the declared state ----------------------------------------------------------------------------------------
    def test_open_order_without_activity_is_consistent(self) -> None:
        r = self.rec()
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "reconcile: consistent"))

    def test_taken_without_activity(self) -> None:
        self.p.order("take", "N001")
        r = self.rec("N001")
        self.assertEqual(r.returncode, 1)
        self.assertIn("N001 is TAKEN", r.stdout); self.assertIn("no activity", r.stdout)
        self.edit("src/a.txt", "b\n")  # negative control: a changed file is activity
        self.ok("N001")

    def test_state_against_index(self) -> None:
        self.ok()
        self.set_state(STATE="RUNNING")
        self.assertIn("STATE is RUNNING but no order is TAKEN", self.lines())
        self.set_state(STATE="READY")
        self.ok()
        self.p.order("take", "N001")
        self.set_state(STATE="READY", CURRENT_ORDER="N001")
        self.assertIn("STATE is READY but N001 is TAKEN", self.lines())
        self.set_state(STATE="RUNNING", CURRENT_ORDER="N001")  # negative control: the honest state
        self.edit("src/a.txt", "w\n")
        self.ok()

    def test_current_order_must_be_in_the_index(self) -> None:
        self.set_state(STATE="READY", CURRENT_ORDER="N099")
        self.assertIn("CURRENT_ORDER N099 is not in the orders index", self.lines())
        self.set_state(STATE="READY", CURRENT_ORDER="N001")
        self.ok()

    def test_order_file_missing(self) -> None:
        f = next((self.p.circle / "orders").glob("N001*.md"))
        keep = f.read_text(encoding="utf-8")
        f.unlink()
        self.flagged("N001 is OPEN in the index but its order file", "N001")
        self.assertIn("is missing", self.lines("N001"))
        write(f, keep)  # negative control
        self.ok("N001")

    def test_unknown_and_several_taken_orders(self) -> None:
        self.assertEqual(self.rec("N099").returncode, 1)
        self.assertIn("N099: not an open order", self.lines("N099"))
        self.p.order("new", "Черновик", "--tier", "LOCAL", "--changes", "y")
        self.p.order("take", "N001"); self.p.order("take", "N002")  # LOCAL runs alongside the Lead's order (R26)
        out = self.lines()
        self.assertIn("N001 is TAKEN", out); self.assertIn("N002 is TAKEN", out)
        self.edit("src/a.txt", "w\n")
        out = self.lines()  # activity is the whole project's: both orders see it, both stay silent
        self.assertNotIn("no activity", out)

    # --- applied tasks against git ----------------------------------------------------------------------------------
    def test_task_done_without_changes_then_with_changes(self) -> None:
        self.p.order("take", "N001")
        self.plan("applied", ["src/a.txt"])
        r = self.rec("N001")
        self.assertEqual(r.returncode, 1)
        self.assertIn("task T1 is marked applied but its files have no changes", r.stdout)
        self.edit("src/a.txt")
        self.ok("N001")
        self.commit("work", "src/a.txt")  # the work committed after the take counts too
        self.ok("N001")

    def test_plan_done_status_is_not_a_claim_of_written_files(self) -> None:
        # codex_exec leaves a plan task `done` when Codex answered done with no file to write; only `applied` claims written files
        self.p.order("take", "N001")
        self.plan("done", ["src/a.txt"])
        self.ok("N001")

    def test_codex_record_applied_is_a_separate_key_not_a_status(self) -> None:
        self.p.order("take", "N001")
        prop = {"src/a.txt": "abc"}
        self.codex([{"id": "T9", "scope": ["src/a.txt"], "status": "done", "proposed": prop}])  # a patch waiting for `apply`
        self.ok("N001")
        self.codex([{"id": "T9", "scope": ["src/a.txt"], "status": "done", "proposed": prop, "applied": "2026-10-17T10:00:00"}])
        self.assertIn("task T9 is marked applied but its files have no changes", self.lines("N001"))
        self.edit("src/a.txt")
        self.ok("N001")

    def test_codex_latest_run_wins_and_applied_stays(self) -> None:
        self.p.order("take", "N001")
        prop = {"src/a.txt": "abc"}
        self.codex([{"id": "T1", "scope": ["src/a.txt"], "status": "blocked"}],
                   [{"id": "T1", "scope": ["src/a.txt"], "status": "done", "proposed": prop}])
        self.ok("N001")  # the first run's `blocked` is stale, the second is an unapplied proposal
        self.codex([{"id": "T1", "scope": ["src/a.txt"], "status": "done", "proposed": prop, "applied": "2026-10-17T10:00:00"}],
                   [{"id": "T1", "scope": ["src/a.txt"], "status": "failed"}])  # a later failed `fix` does not unwrite the file
        self.assertIn("task T1 is marked applied", self.lines("N001"))

    def test_plan_wins_over_codex_record_of_the_same_task(self) -> None:
        self.p.order("take", "N001")
        self.plan("proposed", ["src/a.txt"])
        self.codex([{"id": "T1", "scope": ["src/a.txt"], "status": "done", "proposed": {"src/a.txt": "abc"}, "applied": "2026-10-17T10:00:00"}])
        self.ok("N001")

    def test_plan_root_prefixes_the_scope(self) -> None:
        self.edit("sub/a.txt", "a\n")
        self.commit("sub", "sub", ago=300)
        self.p.order("take", "N001")
        self.plan("applied", ["a.txt"], root="sub")
        self.assertIn("(sub/a.txt)", self.lines("N001"))
        self.edit("sub/a.txt")
        self.ok("N001")  # was two false lines: "no changes (a.txt)" and "no task declares sub/a.txt"

    def test_path_spellings_and_null_json(self) -> None:
        self.p.order("take", "N001")
        self.plan("applied", [".\\src\\a.txt"])
        self.assertIn("no changes since take (src/a.txt)", self.lines("N001"))
        self.edit("src/a.txt")
        self.ok("N001")
        for body in ({"tasks": None}, {"tasks": "T1"}, {"tasks": [None, 5, {"id": "T2", "scope": None, "status": "applied"}]}):
            self.answers("N001_plan.json", body)
            self.answers("N001_codex.json", {"runs": None})
            r = self.rec("N001")
            self.assertNotIn("Traceback", r.stderr, body)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.answers("N001_codex.json", {"runs": [None, {"tasks": None}, {"tasks": [None, {"id": None}]}]})
        self.ok("N001")

    def test_case_insensitive_paths_on_windows_only(self) -> None:
        old = rec.CASEFOLD
        try:
            rec.CASEFOLD = True
            self.assertTrue(rec._covers(["Src/A.txt"], "src/a.txt"))
            self.assertTrue(rec._covers(["SRC"], "src/a.txt"))
            rec.CASEFOLD = False
            self.assertFalse(rec._covers(["Src/A.txt"], "src/a.txt"))
        finally:
            rec.CASEFOLD = old
        self.assertEqual(rec._norm(["./a/", ".", "", "b\\c", " d "]), ["a", "", "b/c", "d"])
        self.assertEqual(rec._norm(None), []); self.assertEqual(rec._norm("x/y"), ["x/y"])

    def test_scope_inside_the_orders_own_records_is_not_checked(self) -> None:
        self.p.order("take", "N001")
        own = self.p.circle.relative_to(self.p.dir).as_posix()
        self.plan("applied", [f"{own}/notes.md"])  # own records never count as work: no claim can be made
        self.ok("N001")

    # --- files nobody declared --------------------------------------------------------------------------------------
    def test_undeclared_change_only_once_the_answer_exists(self) -> None:
        self.p.order("take", "N001")
        self.plan("proposed", ["src/a.txt"])
        self.edit("src/a.txt", "x\n")
        self.edit("docs/x.md", "doc\n")  # the Lead's own edit outside the tasks' scope - ordinary work, not a mismatch
        self.ok("N001")
        self.answers("N001.json", {"order": "N001", "files_changed": ["src/a.txt"]})  # the answer forgot the document
        self.flagged("do not declare: docs/x.md", "N001")
        self.assertNotIn("src/a.txt", self.lines("N001"))
        self.answers("N001.json", {"order": "N001", "files_changed": ["src/a.txt", "docs/x.md"]})
        self.ok("N001")

    def test_task_scope_counts_as_declared_after_the_answer(self) -> None:
        self.p.order("take", "N001")
        self.plan("proposed", ["src/a.txt"])
        self.edit("src/a.txt", "x\n")
        self.answers("N001.json", {"order": "N001", "files_changed": ["README.md"]})
        self.ok("N001")  # src/a.txt is declared by the task, README.md was not changed
        self.edit("src/stray.txt", "s\n")
        self.flagged("do not declare: src/stray.txt", "N001")

    def test_answer_without_files_does_not_open_the_check(self) -> None:
        self.p.order("take", "N001")
        self.edit("docs/x.md", "doc\n")
        self.answers("N001.json", {"order": "N001", "status": "done"})
        self.ok("N001")

    # --- hold -> reopen -> take again ------------------------------------------------------------------------------
    def test_blocked_executor_reopen_and_take_again_is_not_a_mismatch(self) -> None:
        self.p.order("take", "N001")
        self.plan("applied", ["src/a.txt"])
        self.edit("src/a.txt")  # cycle 1: written, not committed
        self.ok("N001")
        self.p.order("hold", "N001", "--why", "executor blocked")
        self.p.order("reopen", "N001")
        self.ok("N001")  # OPEN with the first cycle's applied tasks: was "taken without order.py take?"
        (self.p.circle / "answers" / "N001_scope.json").unlink()
        self.ok("N001")  # without the snapshot the journal still says the order was taken
        self.p.order("take", "N001")  # the new snapshot has cycle 1's file in its base
        self.ok("N001")  # was "its files have no changes since take"

    def test_open_with_applied_tasks_and_never_taken(self) -> None:
        self.plan("applied", ["src/a.txt"])
        self.assertIn("N001 is OPEN but 1 plan task(s) are marked applied", self.lines())
        self.assertEqual(self.rec("N099").returncode, 1)

    def test_committed_work_of_the_first_cycle_survives_a_retake(self) -> None:
        self.p.order("take", "N001")
        self.plan("applied", ["src/a.txt"])
        self.edit("src/a.txt")
        self.commit("cycle 1", "src/a.txt")
        self.p.order("hold", "N001", "--why", "break"); self.p.order("reopen", "N001"); self.p.order("take", "N001")
        self.ok("N001")

    # --- what "since take" means ------------------------------------------------------------------------------------
    def test_rewriting_the_snapshot_does_not_move_the_take(self) -> None:
        # order.py close rewrites N_scope.json at every attempt (checked/changed/undeclared); git pull resets its mtime
        self.p.order("take", "N001")
        self.plan("applied", ["src/a.txt"])
        f = self.p.circle / "answers" / "N001_scope.json"
        self.edit("src/a.txt"); self.commit("work", "src/a.txt")
        self.ok("N001")
        snap = json.loads(f.read_text(encoding="utf-8"))
        write(f, json.dumps({**snap, "checked": "2099-01-01 00:00", "changed": 1, "undeclared": []}))
        os.utime(f, (time.time() + 3600, time.time() + 3600))
        self.ok("N001")  # was "its files have no changes since take" (mtime after the commit)
        write(f, json.dumps(snap)); os.utime(f, (time.time() + 3600, time.time() + 3600))  # a pull: mtime = pull time, no `checked`
        self.ok("N001")
        # negative control: nothing was done - the rewritten snapshot does not hide it
        git(self.p, "reset", "-q", "HEAD~1"); git(self.p, "checkout", "--", "src/a.txt")  # the commit and the edit are gone; the take's records stay
        write(f, json.dumps({**snap, "checked": "2099-01-01 00:00"})); os.utime(f, (time.time() + 3600, time.time() + 3600))
        self.assertIn("no changes since take", self.lines("N001"))

    def test_commit_just_before_the_take_is_not_work_after_it(self) -> None:
        self.edit("src/a.txt", "before\n")
        self.commit("just before")  # the same minute as the take below
        time.sleep(1.1)
        self.p.order("take", "N001")
        self.assertIn("no activity", self.lines("N001"))
        self.edit("src/a.txt", "after\n"); self.commit("after")
        self.ok("N001")

    def test_taken_epoch_order_of_trust(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="forge_epoch_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        f = tmp / "N001_scope.json"
        f.write_text("{}", encoding="utf-8")
        minute = int(time.mktime(time.strptime("2026-10-17 10:00", "%Y-%m-%d %H:%M")))
        self.assertEqual(rec.taken_epoch({"taken_ts": 1760000000, "taken": "2026-10-17 10:00"}, f), 1760000000)
        self.assertIsNone(rec.taken_epoch({"taken": "garbage"}, tmp / "none.json"))
        os.utime(f, (minute + 30, minute + 30))
        self.assertEqual(rec.taken_epoch({"taken": "2026-10-17 10:00"}, f), minute + 30)  # the second of the take
        self.assertEqual(rec.taken_epoch({"taken": "2026-10-17 10:00", "checked": "x"}, f), minute)  # a rewritten snapshot: the minute
        os.utime(f, (minute + 3600, minute + 3600))
        self.assertEqual(rec.taken_epoch({"taken": "2026-10-17 10:00"}, f), minute)  # pulled later: the minute

    # --- when no claim can be made ----------------------------------------------------------------------------------
    def test_skipped_snapshot_makes_no_file_claim_and_no_git_status(self) -> None:
        self.p.order("take", "N001")
        self.plan("applied", ["src/a.txt"])
        self.assertIn("no changes since take", self.lines("N001"))  # negative control with a usable snapshot
        f = self.p.circle / "answers" / "N001_scope.json"
        snap = json.loads(f.read_text(encoding="utf-8"))
        write(f, json.dumps({**snap, "skipped": True, "base": {}}))
        self.ok("N001")
        calls: list[Path] = []
        real = rec.git_tree
        rec.git_tree = lambda root: calls.append(root) or real(root)
        self.addCleanup(setattr, rec, "git_tree", real)
        cfg = rec.load_cfg(self.p.dir)
        self.assertEqual(rec.reconcile(self.p.dir, cfg), [])
        self.assertEqual(calls, [])  # `git status` over the project is not run for a snapshot that cannot be used
        write(f, json.dumps(snap))
        self.assertTrue(rec.reconcile(self.p.dir, cfg))
        self.assertEqual(len(calls), 1)

    def test_not_a_git_project_and_a_repository_without_commits(self) -> None:
        def project(*, init_git: bool) -> Project:
            q = Project()
            self.addCleanup(q.cleanup)
            q.order("new", "Работа", "--tier", "HEAVY", "--changes", "x")
            if init_git:
                git(q, "init", "-q")
            q.order("take", "N001")
            write(q.circle / "answers" / "N001_plan.json", json.dumps({"tasks": [{"id": "T1", "scope": ["a.txt"], "status": "applied"}]}))
            return q
        plain, nocommit = project(init_git=False), project(init_git=True)
        self.assertFalse((plain.circle / "answers" / "N001_scope.json").exists())
        self.assertTrue((nocommit.circle / "answers" / "N001_scope.json").exists())
        for q in (plain, nocommit):  # no baseline / no commit list: "its files have no changes" cannot be claimed
            r = run([RECONCILE, "N001"], cwd=q.dir, ok=False)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "reconcile: consistent"), r.stdout)
        write(nocommit.dir / "c.txt", "w\n")  # negative control: what IS visible is still checked
        write(nocommit.circle / "answers" / "N001.json", json.dumps({"files_changed": ["b.txt"]}))
        r = run([RECONCILE, "N001"], cwd=nocommit.dir, ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("do not declare: c.txt", r.stdout)

    def test_git_runs_without_optional_locks(self) -> None:
        # "read-only" includes the index: `git status` refreshes .git/index and takes index.lock unless told not to
        seen: dict[str, str | None] = {}
        real_tree, real_run = rec.git_tree, rec.subprocess.run

        def tree(root):
            seen["status"] = os.environ.get("GIT_OPTIONAL_LOCKS")
            return real_tree(root)

        def spy(cmd, *a, **k):
            if "log" in cmd:
                seen["log"] = (k.get("env") or {}).get("GIT_OPTIONAL_LOCKS")
            return real_run(cmd, *a, **k)
        rec.git_tree = tree
        self.addCleanup(setattr, rec, "git_tree", real_tree)
        self.p.order("take", "N001")
        self.plan("applied", ["src/a.txt"])
        with unittest.mock.patch.object(rec.subprocess, "run", spy):
            rec.reconcile(self.p.dir, rec.load_cfg(self.p.dir))
        self.assertEqual(seen, {"status": "0", "log": "0"})

    def test_slow_git_status_does_not_hang_the_brief(self) -> None:
        self.p.order("take", "N001")
        self.plan("applied", ["src/a.txt"])
        gate = threading.Event()
        self.addCleanup(gate.set)
        real, old = rec.git_tree, rec.TREE_DEADLINE
        rec.git_tree = lambda root: gate.wait(30) and real(root)
        rec.TREE_DEADLINE = 0.3
        self.addCleanup(lambda: (setattr(rec, "git_tree", real), setattr(rec, "TREE_DEADLINE", old)))
        env_before = os.environ.get("GIT_OPTIONAL_LOCKS")
        t0 = time.time()
        self.assertEqual(rec.reconcile(self.p.dir, rec.load_cfg(self.p.dir)), [])  # no tree in time: no file-level claim
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(os.environ.get("GIT_OPTIONAL_LOCKS"), env_before)  # the lock-free setting is not left behind

    # --- read-only ---------------------------------------------------------------------------------------------------
    def snapshot_of_project(self) -> dict[str, str]:
        out = {}
        for f in self.p.dir.rglob("*"):
            if f.is_file() and ".git" not in f.relative_to(self.p.dir).parts:
                out[f.relative_to(self.p.dir).as_posix()] = hashlib.sha1(f.read_bytes()).hexdigest()
        out[".git/index"] = hashlib.sha1((self.p.dir / ".git" / "index").read_bytes()).hexdigest()  # git status must not refresh it
        return out

    def test_read_only(self) -> None:
        self.p.order("take", "N001")
        self.plan("applied", ["src/a.txt"])
        self.edit("docs/x.md", "doc\n")
        before = self.snapshot_of_project()
        self.rec(); self.rec("N001")
        self.assertEqual(before, self.snapshot_of_project())

    # --- the brief ---------------------------------------------------------------------------------------------------
    def test_brief_line_only_with_mismatches(self) -> None:
        self.assertNotIn("reconcile:", self.p.step("--brief").stdout)
        self.p.order("take", "N001")
        out = self.p.step("--brief").stdout
        # the mismatch itself, not a bare count (one line: no "1 mismatches")
        self.assertRegex(out, r"reconcile: N001 is TAKEN since .* no activity \(no changed files, no plan, no answer\) - all: python .*reconcile\.py")
        self.assertNotIn("more)", out); self.assertNotIn("mismatches", out)
        self.edit("src/a.txt", "w\n")
        self.assertNotIn("reconcile:", self.p.step("--brief").stdout)

    def test_brief_counts_the_other_mismatches(self) -> None:
        self.p.order("take", "N001")
        self.set_state(STATE="RUNNING", CURRENT_ORDER="N099")
        out = self.p.step("--brief").stdout
        self.assertRegex(out, r"reconcile: CURRENT_ORDER N099 is not in the orders index \(\+1 more\) - all: python .*reconcile\.py")


class BriefMachineLoad(unittest.TestCase):
    """step.py brief: the live machine load is cached and runs under a deadline (a killed hook loses the whole brief)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="forge_load_"))
        old_tmp, old_load, old_cache, old_dl = tempfile.tempdir, machine_mod.load, step_mod.LOAD_CACHE_S, step_mod.LOAD_DEADLINE_S
        tempfile.tempdir = str(self.tmp)
        self.calls = 0
        self.gate = threading.Event()

        def restore() -> None:
            self.gate.set()
            tempfile.tempdir = old_tmp
            machine_mod.load, step_mod.LOAD_CACHE_S, step_mod.LOAD_DEADLINE_S = old_load, old_cache, old_dl
            shutil.rmtree(self.tmp, ignore_errors=True)
        self.addCleanup(restore)

    def fake(self, verdict: str = "hot"):
        def load(*_a, **_k):
            self.calls += 1
            return {"verdict": verdict, "reasons": ["CPU load 2.00 per core"], "slots": 1, "laptop": True, "sample": {}}
        return load

    def test_cached_between_two_session_starts(self) -> None:
        machine_mod.load = self.fake()
        a = step_mod.live_load(); b = step_mod.live_load()
        self.assertEqual((a["verdict"], b["verdict"], self.calls), ("hot", "hot", 1))
        step_mod.LOAD_CACHE_S = 0  # negative control: an expired cache is measured again
        step_mod.live_load()
        self.assertEqual(self.calls, 2)

    def test_unreadable_cache_is_measured_again(self) -> None:
        machine_mod.load = self.fake("ok")
        write(self.tmp / "forge_machine_load.json", "{not json")
        self.assertEqual(step_mod.live_load()["verdict"], "ok")
        write(self.tmp / "forge_machine_load.json", json.dumps({"t": time.time(), "v": "str"}))
        self.assertEqual(step_mod.live_load()["verdict"], "ok")
        self.assertEqual(self.calls, 2)

    def test_slow_probe_gives_no_line_instead_of_a_killed_hook(self) -> None:
        machine_mod.load = lambda *_a, **_k: self.gate.wait(30) and self.fake()()
        step_mod.LOAD_DEADLINE_S = 0.3
        t0 = time.time()
        self.assertIsNone(step_mod.live_load())
        self.assertLess(time.time() - t0, 5)
        self.assertFalse((self.tmp / "forge_machine_load.json").exists())  # nothing half-measured is cached

    def test_a_failing_probe_gives_no_line(self) -> None:
        def boom(*_a, **_k):
            raise RuntimeError("no PowerShell")
        machine_mod.load = boom
        self.assertIsNone(step_mod.live_load())
        self.assertFalse((self.tmp / "forge_machine_load.json").exists())


if __name__ == "__main__":
    unittest.main()
