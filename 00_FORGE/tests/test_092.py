"""0.9.2 tests (K34 evidence discipline, R-28 upgrade, replay). Every check here is tested both ways: the case it must
catch and the case it must leave alone (the negative control the deck review asked for)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import GATES, TOOLS, Project, run  # noqa: E402

sys.path.insert(0, str(TOOLS))
import skills_map  # noqa: E402

RU = "# Отчёт\n\nЭтот документ написан на русском языке для владельца проекта.\n"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, capture_output=True, check=True)


class K34Orders(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()

    def tearDown(self) -> None:
        self.p.cleanup()

    def order_text(self, oid: str) -> str:
        return next((self.p.circle / "orders").glob(f"{oid}_*.md")).read_text(encoding="utf-8")

    def test_request_verbatim_or_hypothesis_and_decision(self) -> None:
        p = self.p
        p.order("new", "With request", "--changes", "x", "--request", "сделай таблицу расходов\nпо месяцам",
                "--request-source", "owner, chat 2026-10-08", "--decision", "a;b;c;d;nothing else")
        t = self.order_text("N001")
        self.assertIn("> сделай таблицу расходов\n> по месяцам", t)
        self.assertIn("— source: owner, chat 2026-10-08", t)
        self.assertIn("5. Deliberately not touched: nothing else", t)
        self.assertNotIn("hypothesis", t.split("## Goal")[0])
        p.order("new", "Without request", "--changes", "y")
        t2 = self.order_text("N002")
        self.assertIn("a **hypothesis** until the owner confirms it at DoD", t2)  # must flag the missing source
        self.assertIn("1. What changes, and why this is the smallest change: (fill in)", t2)

    def test_answer_refuses_missing_paths_L010(self) -> None:
        p = self.p
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        r = p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", "02_Äëÿ/garbled.md", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("L010", r.stdout + r.stderr)
        self.assertFalse((p.circle / "answers" / "N001.json").exists())  # nothing recorded
        good = p.read / "N001_a.md"; good.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", good.relative_to(p.dir).as_posix())
        self.assertTrue((p.circle / "answers" / "N001.json").exists())

    def test_blocked_protocol(self) -> None:
        p = self.p
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        r = p.order("answer", "N001", "--executor", "ollama:x", "--status", "blocked", "--summary", "s", "--question", "q?", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("--guess", r.stdout + r.stderr)  # a question without a guess is refused
        p.order("answer", "N001", "--executor", "ollama:x", "--status", "blocked", "--summary", "s",
                "--question", "which month?", "--ambiguous", "the order says 'last month'", "--guess", "September")
        ans = json.loads((p.circle / "answers" / "N001.json").read_text(encoding="utf-8"))
        self.assertEqual(ans["blocked"]["my_guess"], "September")
        self.assertIn("| N001 |", (p.circle / "orders" / "00_INDEX.md").read_text(encoding="utf-8"))
        self.assertRegex((p.circle / "orders" / "00_INDEX.md").read_text(encoding="utf-8"), r"\| N001 \|.*\| BLOCKED \|")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        r = p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("blocked", r.stdout + r.stderr)
        self.assertEqual(p.red(), [])  # BLOCKED keeps STATE/INDEX consistent

    def test_report_not_done(self) -> None:
        p = self.p
        p.order("new", "A", "--changes", "x")
        p.order("report", "N001", "--verdict", "CHANGES", "--text", "t", "--not-done", "charts (no data yet); English copy")
        rep = (p.circle / "reports" / "N001.md").read_text(encoding="utf-8")
        self.assertIn("- charts (no data yet)\n- English copy", rep)
        p.order("report", "N001", "--verdict", "CHANGES", "--text", "t")
        self.assertIn("- (not recorded)", (p.circle / "reports" / "N001.md").read_text(encoding="utf-8"))

    def test_declared_txt_deliverable_is_language_checked(self) -> None:
        p = self.p
        out = p.read / "letter.txt"
        p.order("new", "Letter", "--tier", "LOCAL", "--changes", "x", "--deliverable", out.relative_to(p.dir).as_posix())
        p.order("take", "N001")
        out.write_text("Dear owner, this letter is written in English and it is long enough to be checked. " * 8, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", out.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        r = p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("letter.txt: deliverable is not in the user's language", r.stdout + r.stderr)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_scope_snapshot_lists_undeclared_changes(self) -> None:
        p = self.p
        git(p.dir, "init", "-q"); git(p.dir, "add", "-A"); git(p.dir, "commit", "-qm", "init")
        stale = p.read / "already_dirty.md"; stale.write_text(RU, encoding="utf-8", newline="\n")  # dirty before take
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        snap = json.loads((p.circle / "answers" / "N001_scope.json").read_text(encoding="utf-8"))
        self.assertIn(stale.relative_to(p.dir).as_posix(), snap["base"])
        mine = p.read / "N001_a.md"; mine.write_text(RU, encoding="utf-8", newline="\n")
        other = p.read / "другая_сессия.md"; other.write_text(RU, encoding="utf-8", newline="\n")  # Cyrillic path on purpose
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", mine.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        r = p.order("close", "N001")  # a warning, never a block
        out = r.stdout + r.stderr
        self.assertIn("K34 scope: 1 path(s)", out); self.assertIn("другая_сессия.md", out)
        self.assertNotIn("N001_a.md", out.split("K34 scope")[1].splitlines()[0])  # the declared file is not listed
        self.assertNotIn("already_dirty.md", out)                                  # nor what was dirty before take
        snap = json.loads((p.circle / "answers" / "N001_scope.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["undeclared"], [other.relative_to(p.dir).as_posix()])

    def test_stop_runbook(self) -> None:
        p = self.p
        ctx = p.docs / "CONTEXT.md"; ctx.write_text("# CONTEXT\n", encoding="utf-8", newline="\n")
        old = time.time() - 3 * 3600
        os.utime(ctx, (old, old))
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        d = p.read / "N001_a.md"; d.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", d.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude"); p.order("close", "N001")
        r = p.step("--stop-check")
        self.assertEqual(r.returncode, 0)  # never blocks
        msg = json.loads(r.stdout)["systemMessage"]  # plain Stop stdout reaches only the debug log - JSON is shown to the user
        self.assertIn("CONTEXT.md is older than the close of N001", msg)
        self.assertEqual(p.step("--stop-check").stdout.strip(), "")  # same situation -> shown once, not after every turn
        self.assertIn("CONTEXT.md is older", p.step("--stop-check", "--plain").stdout)
        new = time.time() + 120
        os.utime(ctx, (new, new))
        self.assertNotIn("CONTEXT.md is older", p.step("--stop-check").stdout)  # fresh CONTEXT -> no such step

    def test_close_refuses_a_deleted_declared_file(self) -> None:
        p = self.p  # N013 rung 3 #1: a result deleted after `answer` must not close silently
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        d = p.read / "N001_a.md"; d.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", d.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        d.unlink()
        r = p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("no longer exist", r.stdout + r.stderr)
        d.write_text(RU, encoding="utf-8", newline="\n")
        p.order("close", "N001")

    def test_request_without_source_is_a_hypothesis(self) -> None:
        self.p.order("new", "A", "--changes", "x", "--request", "сделай отчёт")  # rung 3 #4: words without a source
        self.assertIn("not given — a **hypothesis** until the owner confirms these words", self.order_text("N001"))

    def test_blocked_answer_syncs_the_dashboard(self) -> None:
        p = self.p  # rung 3 #7: project.json follows the index when `answer --status blocked` moves the order
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        p.order("answer", "N001", "--executor", "ollama:x", "--status", "blocked", "--summary", "s",
                "--question", "q", "--ambiguous", "a", "--guess", "g")
        pj = (p.state / "project.json").read_text(encoding="utf-8")
        self.assertIn('"todo"', pj); self.assertNotIn('"doing"', pj)

    def test_stop_runbook_same_minute(self) -> None:
        p = self.p  # rung 3 #5: the journal keeps minutes - a close in CONTEXT's own minute still counts
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        d = p.read / "N001_a.md"; d.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", d.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude"); p.order("close", "N001")
        from datetime import datetime
        j = next((p.circle / "journal").glob("*.md"))
        hm = re.findall(r"^- (\d\d:\d\d) \[N001\] closed", j.read_text(encoding="utf-8"), re.M)[-1]
        ts = datetime.strptime(f"{j.stem} {hm}", "%Y-%m-%d %H:%M").timestamp()
        ctx = p.docs / "CONTEXT.md"; ctx.write_text("# CONTEXT\n", encoding="utf-8", newline="\n")
        os.utime(ctx, (ts + 59, ts + 59))  # CONTEXT written 59 s into the minute of the close
        self.assertIn("CONTEXT.md is older than the close of N001", p.step("--stop-check", "--plain").stdout)
        os.utime(ctx, (ts + 61, ts + 61))  # the next minute: CONTEXT is newer
        self.assertNotIn("CONTEXT.md is older", p.step("--stop-check", "--plain").stdout)

    def test_scope_record_unwritable_never_blocks_close(self) -> None:
        import stat
        p = self.p  # rung 3 #3: the scope record is a convenience - a locked file must not stop the close
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x")
        (p.circle / "answers" / "N001_scope.json").write_text('{"order": "N001", "skipped": false, "base": {}}\n', encoding="utf-8")
        p.order("take", "N001")
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        git(p.dir, "init", "-q"); git(p.dir, "add", "-A"); git(p.dir, "commit", "-qm", "init")
        snap = p.circle / "answers" / "N001_scope.json"
        snap.write_text('{"order": "N001", "skipped": false, "base": {}}\n', encoding="utf-8", newline="\n")
        d = p.read / "N001_a.md"; d.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", d.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        os.chmod(snap, stat.S_IREAD)
        try:
            p.order("close", "N001")  # must not raise although the record cannot be rewritten
        finally:
            os.chmod(snap, stat.S_IWRITE | stat.S_IREAD)

    def test_stop_without_git_and_next_day(self) -> None:
        p = self.p
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        r = run([str(TOOLS / "step.py"), "--stop-check"], cwd=p.dir, env={"PATH": ""})  # r2 #1: no git -> no crash
        self.assertEqual(r.returncode, 0); self.assertIn("order N001 is TAKEN", json.loads(r.stdout)["systemMessage"])
        self.assertEqual(p.step("--stop-check").stdout.strip(), "")  # same day, same situation: silent
        cache = p.state / "cache" / "stop_runbook.txt"
        cache.write_text("2000-01-01\n" + cache.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8", newline="\n")
        self.assertIn("order N001 is TAKEN", json.loads(p.step("--stop-check").stdout)["systemMessage"])  # r2 #2: a new day

    def test_local_model_block_helper(self) -> None:
        p = self.p  # K34e for Ollama: the shared helper every local_exec mode calls
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, r'{TOOLS}'); import local_exec; from pathlib import Path; "
                            "sys.exit(local_exec.block('N001', Path('.'), 'which month? | last month | September'))"],
                           cwd=p.dir, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1, r.stderr)
        ans = json.loads((p.circle / "answers" / "N001.json").read_text(encoding="utf-8"))
        self.assertEqual(ans["blocked"], {"question": "which month?", "what_is_ambiguous": "last month", "my_guess": "September"})
        self.assertRegex((p.circle / "orders" / "00_INDEX.md").read_text(encoding="utf-8"), r"\| N001 \|.*\| BLOCKED \|")

    def test_blocked_needs_all_three_fields(self) -> None:
        p = self.p
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        r = p.order("answer", "N001", "--executor", "ollama:x", "--status", "blocked", "--summary", "s",
                    "--question", "q?", "--guess", "g", ok=False)  # no --ambiguous
        self.assertNotEqual(r.returncode, 0); self.assertIn("--ambiguous", r.stdout + r.stderr)

    def test_take_without_git_is_fail_open(self) -> None:
        p = self.p
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x")
        p.order("take", "N001", env={"PATH": ""})  # git cannot be found: take still works (N013 verifier2 #1)
        self.assertFalse((p.circle / "answers" / "N001_scope.json").exists())

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_scope_in_a_nested_repository(self) -> None:
        p = self.p  # the repository is the temp folder ABOVE the project: paths must stay project-relative
        git(p.tmp, "init", "-q"); git(p.tmp, "add", "-A"); git(p.tmp, "commit", "-qm", "init")
        outside = p.tmp / "outside.md"; outside.write_text("x\n", encoding="utf-8")
        p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        mine = p.read / "N001_a.md"; mine.write_text(RU, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", mine.relative_to(p.dir).as_posix())
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        out = p.order("close", "N001").stdout
        self.assertNotIn("K34 scope", out)  # the declared file matches; a file outside the project is not this project's
        snap = json.loads((p.circle / "answers" / "N001_scope.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["undeclared"], [])
        self.assertFalse(any(k.startswith("proj/") for k in snap["base"]))


class Upgrade(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()

    def tearDown(self) -> None:
        self.p.cleanup()

    def test_init_has_swarm_folders_and_modes(self) -> None:
        p = self.p
        for d in ("circle/plans", "state/staging", "state/swarm"):
            self.assertTrue((p.dir / "01_PROJECT_CORE" / d).is_dir(), d)
        self.assertTrue((p.dir / "01_PROJECT_CORE" / "config" / "swarm_modes.json").is_file())
        claude = (p.dir / "CLAUDE.md").read_text(encoding="utf-8")
        for h in ("## Swarm and the machine", "## Skills map", "## Evidence discipline"):
            self.assertIn(h, claude)  # R-46

    def test_upgrade_dry_run_apply_idempotent(self) -> None:
        p = self.p
        fj = p.dir / "forge.json"; cfg = json.loads(fj.read_text(encoding="utf-8")); cur = cfg["kernel_version"]
        cfg["kernel_version"] = "0.7.1"; fj.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        cm = p.dir / "CLAUDE.md"; c = cm.read_text(encoding="utf-8")
        c = re.sub(r"(?ms)^## Evidence discipline.*?(?=^## )", "", c).replace(f"(Forge plugin v{cur};", "(Forge plugin v0.7.1;")
        cm.write_text(c, encoding="utf-8", newline="\n")
        shutil.rmtree(p.dir / "01_PROJECT_CORE" / "circle" / "plans")
        (p.dir / "01_PROJECT_CORE" / "config" / "swarm_modes.json").unlink()
        before = {f: f.read_bytes() for f in (fj, cm)}
        r = run([str(TOOLS / "upgrade.py"), str(p.dir), "--dry-run"], cwd=p.dir)
        self.assertIn("kernel_version 0.7.1 -> " + cur, r.stdout); self.assertIn("Evidence discipline", r.stdout)
        self.assertEqual({f: f.read_bytes() for f in (fj, cm)}, before)  # dry run writes nothing
        self.assertFalse((p.dir / "01_PROJECT_CORE" / "circle" / "plans").exists())
        run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        cfg2 = json.loads(fj.read_text(encoding="utf-8"))
        self.assertEqual(cfg2["kernel_version"], cur); self.assertEqual(cfg2["upgrades"][-1]["from"], "0.7.1")
        c2 = cm.read_text(encoding="utf-8")
        self.assertIn("## Evidence discipline", c2); self.assertIn(f"(Forge plugin v{cur};", c2)
        self.assertLess(c2.index("## Evidence discipline"), c2.index("## Session routine"))
        self.assertTrue((p.dir / "01_PROJECT_CORE" / "circle" / "plans").is_dir())
        self.assertEqual(p.red(), [])
        r3 = run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        self.assertIn("nothing to do", r3.stdout)  # idempotent
        self.assertEqual(len(json.loads(fj.read_text(encoding="utf-8"))["upgrades"]), 1)

    def old_plugin_sections(self) -> tuple[bytes, list[tuple[str, str]]]:
        import upgrade
        p = self.p
        fj = p.dir / "forge.json"; cfg = json.loads(fj.read_text(encoding="utf-8")); cur = cfg["kernel_version"]
        cm = p.dir / "CLAUDE.md"; c = cm.read_text(encoding="utf-8")
        rendered = upgrade.render_template(cfg, p.dir, cur, time.strftime("%Y-%m-%d"))
        expected = [(h, body) for h, body in upgrade.sections(rendered) if upgrade.key(h) in upgrade.PLUGIN_OWNED]
        old = {"two model families": "## Two model families (K29–K31, Forge 0.8) — old decisions\nold family rules\n\n",
               "skills": "## Skills (Forge 0.8) — old list\nold skill rules\n\n"}
        for h, body in upgrade.sections(c):
            if upgrade.key(h) in old:
                c = c.replace(body, old[upgrade.key(h)], 1)
        c = c.replace("## Project rules (beyond the constitution)\n",
                      "## Project rules (beyond the constitution)\nKeep this hand-edited rule.\n", 1)
        c = c.replace(f"(Forge plugin v{cur};", "(Forge plugin v0.8;")
        cfg["kernel_version"] = "0.8"
        fj.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        cm.write_text(c, encoding="utf-8", newline="\n")
        return cm.read_bytes(), expected

    def test_upgrade_replaces_plugin_owned_sections_with_backup(self) -> None:
        import upgrade
        p = self.p
        before, expected = self.old_plugin_sections()
        r = run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        after = (p.dir / "CLAUDE.md").read_text(encoding="utf-8")
        actual = dict(upgrade.sections(after))
        for h, body in expected:
            self.assertEqual(actual[h], body)
            self.assertIn(f"replaced section: {h[3:]} (backup: ", r.stdout)
        self.assertNotIn("old family rules", after); self.assertNotIn("old skill rules", after)
        kept_before = {upgrade.key(h): body for h, body in upgrade.sections(before.decode("utf-8"))
                       if upgrade.key(h) not in upgrade.PLUGIN_OWNED}
        kept_after = {upgrade.key(h): body for h, body in upgrade.sections(after)
                      if upgrade.key(h) not in upgrade.PLUGIN_OWNED}
        self.assertEqual(kept_after, kept_before)
        self.assertIn("Keep this hand-edited rule.", after)
        backups = list((p.state / "upgrade_backup").glob("CLAUDE.md.*.md"))
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0].name, r"^CLAUDE\.md\.0\.8-\d{8}-\d{4}\.md$")
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertIn(f"(backup: {os.path.realpath(backups[0])})", r.stdout)  # macOS: /var is a symlink to /private/var

    def test_upgrade_plugin_owned_dry_run_writes_nothing(self) -> None:
        p = self.p
        before, expected = self.old_plugin_sections()
        snapshot = {f.relative_to(p.dir): f.read_bytes() for f in p.dir.rglob("*") if f.is_file()}
        r = run([str(TOOLS / "upgrade.py"), str(p.dir), "--dry-run"], cwd=p.dir)
        for h, _ in expected:
            self.assertIn(f"would replace section: {h[3:]}", r.stdout)
        self.assertNotIn("replaced section:", r.stdout)
        self.assertEqual((p.dir / "CLAUDE.md").read_bytes(), before)
        self.assertEqual({f.relative_to(p.dir): f.read_bytes() for f in p.dir.rglob("*") if f.is_file()}, snapshot)
        self.assertFalse((p.state / "upgrade_backup").exists())

    def test_upgrade_plugin_owned_idempotent(self) -> None:
        p = self.p
        self.old_plugin_sections()
        run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        before = (p.dir / "CLAUDE.md").read_bytes()
        backups = {f: f.read_bytes() for f in (p.state / "upgrade_backup").iterdir()}
        r = run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        self.assertIn("nothing to do", r.stdout)
        self.assertNotIn("replaced section:", r.stdout)
        self.assertEqual((p.dir / "CLAUDE.md").read_bytes(), before)
        self.assertEqual({f: f.read_bytes() for f in (p.state / "upgrade_backup").iterdir()}, backups)

    def test_upgrade_backup_custom_state_and_collision(self) -> None:
        import datetime
        import upgrade
        from unittest.mock import patch
        p = self.p
        before, _ = self.old_plugin_sections()
        fj = p.dir / "forge.json"; cfg = json.loads(fj.read_text(encoding="utf-8"))
        cfg["layout"]["state"] = "work/state"
        fj.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        state = upgrade.project_layout(p.dir)["state"]
        folder = state / "upgrade_backup"
        backups = list(folder.glob("CLAUDE.md.*.md"))
        self.assertEqual(len(backups), 1); self.assertEqual(backups[0].read_bytes(), before)
        self.assertFalse((p.state / "upgrade_backup").exists())
        stamp = datetime.datetime(2026, 10, 12, 12, 34)
        existing = folder / "CLAUDE.md.0.8-20261012-1234.md"
        existing.write_bytes(b"earlier backup\n")
        original = (p.dir / "CLAUDE.md").read_bytes()
        with patch.object(upgrade.datetime, "datetime") as clock:
            clock.now.return_value = stamp
            backup = upgrade.backup_claude(p.dir, state, "0.8")
        self.assertEqual(existing.read_bytes(), b"earlier backup\n")
        self.assertEqual(backup.name, "CLAUDE.md.0.8-20261012-1234-1.md")
        self.assertEqual(backup.read_bytes(), original)

    def test_upgrade_keeps_hand_made_plugin_sections(self) -> None:
        p = self.p
        cm = p.dir / "CLAUDE.md"
        cur = json.loads((p.dir / "forge.json").read_text(encoding="utf-8"))["kernel_version"]
        hand = ("# CLAUDE.md — project `x` (Forge plugin v0.8; user language: ru)\n\n"
                "## Skills (Forge 0.8)\nold skill rules\n\n"
                "## Two model families (K29–K31, Forge 0.8)\nold family rules\n\n"
                "## My rules\nkeep these\n")
        cm.write_text(hand, encoding="utf-8", newline="\n")
        r = run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        self.assertIn("a hand-made file - sections not inserted", r.stdout)
        self.assertNotIn("replaced section:", r.stdout)
        self.assertEqual(cm.read_text(encoding="utf-8"), hand.replace("v0.8;", f"v{cur};"))
        self.assertFalse((p.state / "upgrade_backup").exists())

    def test_upgrade_numbered_and_hand_made_claude_md(self) -> None:
        p = self.p
        cm = p.dir / "CLAUDE.md"; c = cm.read_text(encoding="utf-8")
        c = re.sub(r"(?ms)^## Evidence discipline.*?(?=^## )", "", c)
        n = iter(range(1, 100))
        cm.write_text(re.sub(r"(?m)^## ", lambda _: f"## {next(n)}. ", c), encoding="utf-8", newline="\n")  # numbered headings
        r = run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        self.assertIn("CLAUDE.md + sections: Evidence discipline", r.stdout)  # only the missing one, numbers ignored
        c2 = cm.read_text(encoding="utf-8")
        self.assertEqual(c2.count("## Skills map"), 0); self.assertEqual(c2.count("Skills map (K32"), 1)  # no duplicate
        self.assertLess(c2.index("## Evidence discipline"), c2.index(". Session routine"))
        hand = ("# CLAUDE.md — project `x` (Forge plugin v0.9.1; user language: ru)\n\n## 1. Role\na\n\n## 2. Strategy\nb\n\n"
                "## 10. Session routine\nc\n")
        cm.write_text(hand, encoding="utf-8", newline="\n")  # invest-like: template title, own headings
        r = run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        self.assertIn("a hand-made file - sections not inserted", r.stdout)
        self.assertEqual(cm.read_text(encoding="utf-8"), hand.replace("v0.9.1;", f"v{json.loads((p.dir / 'forge.json').read_text(encoding='utf-8'))['kernel_version']};"))

    def test_upgrade_anchor_only_at_line_start(self) -> None:
        p = self.p  # r2 #4: "### Session routine notes" earlier in the file must not become the insertion point
        cm = p.dir / "CLAUDE.md"; c = cm.read_text(encoding="utf-8")
        c = re.sub(r"(?ms)^## Evidence discipline.*?(?=^## )", "", c)
        c = c.replace("## Mode (K25)", "### Session routine notes\nkeep it short\n\n## Mode (K25)", 1)
        cm.write_text(c, encoding="utf-8", newline="\n")
        run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        c2 = cm.read_text(encoding="utf-8")
        self.assertLess(c2.index("### Session routine notes"), c2.index("## Evidence discipline"))
        self.assertLess(c2.index("## Evidence discipline"), c2.index("\n## Session routine"))

    def test_upgrade_follows_a_custom_layout(self) -> None:
        p = self.p  # rung 3 #6: folders come from the project's layout, not from <tech>/
        fj = p.dir / "forge.json"; cfg = json.loads(fj.read_text(encoding="utf-8"))
        cfg["layout"]["circle"] = "work/circle"; cfg["layout"]["state"] = "work/state"  # r3 #4: state drives exclude + .gitignore
        fj.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        r = run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        self.assertIn("work/circle/plans", r.stdout)
        self.assertTrue((p.dir / "work" / "circle" / "orders").is_dir())
        self.assertTrue((p.dir / "work" / "state" / "staging").is_dir())
        self.assertNotIn("01_PROJECT_CORE/circle/plans", r.stdout)
        self.assertIn("work/state/cache/", (p.dir / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertIn("work/state/cache", json.loads(fj.read_text(encoding="utf-8"))["exclude"])

    def test_upgrade_leaves_a_non_template_claude_md(self) -> None:
        p = self.p
        cm = p.dir / "CLAUDE.md"; cm.write_text("# CLAUDE.md — operating instructions (workspace)\n\n## Mine\nx\n", encoding="utf-8", newline="\n")
        r = run([str(TOOLS / "upgrade.py"), str(p.dir)], cwd=p.dir)
        self.assertIn("not made from the project template", r.stdout)
        self.assertEqual(cm.read_text(encoding="utf-8"), "# CLAUDE.md — operating instructions (workspace)\n\n## Mine\nx\n")


class ReplayAndChecks(unittest.TestCase):
    def test_replay_reports_missing_files_only(self) -> None:
        p = Project()
        try:
            p.order("new", "A", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
            keep = p.read / "N001_keep.md"; gone = p.read / "N001_gone.md"
            for f in (keep, gone):
                f.write_text(RU, encoding="utf-8", newline="\n")
            p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files",
                    ",".join(f.relative_to(p.dir).as_posix() for f in (keep, gone)))
            p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude"); p.order("close", "N001")
            gone.unlink()
            res = json.loads(run([str(TOOLS / "replay.py"), "--json"], cwd=p.dir).stdout)
            self.assertEqual(res[0]["findings"], [f"missing now: {gone.relative_to(p.dir).as_posix()}"])  # keep is not reported
            self.assertEqual(p.red(), [])  # read-only
        finally:
            p.cleanup()

    def test_check_html_seo(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="forge_seo_"))
        try:
            head = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            good = tmp / "good.html"
            good.write_text(head + "<title>" + "Карта скиллов Forge: что делает плагин" + "</title>"
                            + '<meta name="description" content="' + "Описание страницы для поиска. " * 3 + '">'
                            + "</head><body><h1>Карта</h1></body></html>", encoding="utf-8")
            bad = tmp / "bad.html"
            bad.write_text(head + "<title>Карта</title></head><body><h1>a</h1><h1>b</h1></body></html>", encoding="utf-8")
            self.assertEqual(run([str(GATES / "check_html.py"), str(good), "--seo"], cwd=tmp, ok=False).returncode, 0)
            r = run([str(GATES / "check_html.py"), str(bad), "--seo"], cwd=tmp, ok=False)
            self.assertEqual(r.returncode, 1)
            for x in ("title is 5 characters", "no meta description", "2 <h1> elements"):
                self.assertIn(x, r.stdout)
            self.assertEqual(run([str(GATES / "check_html.py"), str(bad)], cwd=tmp, ok=False).returncode, 0)  # --seo is opt-in
            apos = tmp / "apos.html"  # an apostrophe inside a double-quoted value is part of the value
            apos.write_text(head + "<title>Карта скиллов Forge: что делает плагин</title>"
                            + "<meta name=\"description\" content=\"Don't guess: Forge checks the produced result, not the intention, with evidence.\">"
                            + "</head><body><h1>x</h1></body></html>", encoding="utf-8")
            r = run([str(GATES / "check_html.py"), str(apos), "--seo"], cwd=tmp, ok=False)
            self.assertEqual(r.returncode, 0, r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_audit_flags_incomplete_evidence(self) -> None:
        p = Project()
        try:
            tests = p.state / "skill_tests"; tests.mkdir(parents=True)
            (tests / "code.md").write_text("task: tiny script\nRESULT: PASS\n", encoding="utf-8")
            (tests / "analytics.md").write_text("Skill: dataviz loaded\nEXPECTED: p < 0.05\nRESULT: PASS\n", encoding="utf-8")
            r = skills_map.audit(skills_map.load(), p.tmp / "nohome", p.dir, {"dataviz": "x", "code-review": "x", "simplify": "x"})
            self.assertEqual(r["domains"]["code"]["evidence_gaps"], ["Skill", "EXPECTED"])
            self.assertEqual(r["domains"]["analytics"]["evidence_gaps"], [])
            self.assertIn("incomplete: no Skill / EXPECTED line", skills_map.audit_md(r))
        finally:
            p.cleanup()


if __name__ == "__main__":
    unittest.main()
