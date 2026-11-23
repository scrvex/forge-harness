"""Plugin tests (stdlib unittest, no dependencies).   python -m unittest discover -s 00_FORGE/tests -v

Each test creates a fresh project in a temp dir with init_project.py (from a minimal plan) and drives it through the real
scripts (subprocess), so what is tested is what the Lead and the owner actually run. The plugin under test is this folder.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
# 0.9.3 (N020): order.py close reads Claude Code transcripts - tests never read the real ~/.claude, only an empty home
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp(prefix="forge_claude_home_")
# 1.1.2 (N028): Codex runs read the Codex app's folder rights and keep a block and a log in FORGE_HOME - tests use their own
# home and a healthy stand-in ACL, never the owner's ~/.forge or this PC's real folder (test_112 covers the real reader)
os.environ["FORGE_HOME"] = tempfile.mkdtemp(prefix="forge_home_")
os.environ["FORGE_PACKAGES_SDDL"] = os.path.join(os.environ["FORGE_HOME"], "packages.sddl")
Path(os.environ["FORGE_PACKAGES_SDDL"]).write_text("D:PAI(A;OICI;FA;;;S-1-5-21-1-2-3-1001)(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)",
                                                   encoding="utf-8")
TOOLS = PLUGIN / "tools"
GATES = PLUGIN / "gates"
PY = sys.executable
PLAN = {"task_restated": "Тестовый проект: таблица и отчёт.", "domain": "test",
        "folders": [{"path": "Таблицы", "purpose": "таблицы"}, {"path": "Отчёты", "purpose": "отчёты"}],
        "automations": [{"phrase": "отчёт", "what": "собрать отчёт", "tier": "HEAVY"}],
        "first_orders": [], "red_lines": ["не выдумывать цифры"], "benefit": "порядок", "required_from_user": ["данные"]}


def run(args: list[str], cwd: Path, ok: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = None
    if env is not None:
        import os
        full_env = {**os.environ, **env}
    r = subprocess.run([PY, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=full_env)
    if ok and r.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


class Project:
    """A throw-away project made by init_project.py."""

    def __init__(self, plan: dict | None = None, mode: str = "cheap") -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="forge_test_"))
        self.dir = self.tmp / "proj"
        (self.tmp / "plan.json").write_text(json.dumps(plan or PLAN, ensure_ascii=False), encoding="utf-8", newline="\n")
        run([str(TOOLS / "init_project.py"), str(self.dir), "--name", "t", "--plan", str(self.tmp / "plan.json"), "--mode", mode,
             "--request", "я хочу тестовый проект"], cwd=self.tmp)
        lay = json.loads((self.dir / "forge.json").read_text(encoding="utf-8"))["layout"]
        self.circle = self.dir / lay["circle"]; self.docs = self.dir / lay["docs"]; self.tools_dir = self.dir / lay["tools"]
        self.read = self.dir / lay["read"]; self.state = self.dir / lay["state"]

    def order(self, *args: str, ok: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
        return run([str(TOOLS / "order.py"), *args], cwd=self.dir, ok=ok, env=env)

    def evidence(self, oid: str, kind: str, verdict: str) -> None:
        """K31: what a second actor leaves - kind ollama | v2 | gpt."""
        a = self.circle / "answers"
        if kind == "v2":
            (a / f"{oid}_review_v2.md").write_text(f"VERDICT: {verdict}\n\n| DoD | met |\n", encoding="utf-8", newline="\n")
        else:
            name = f"{oid}_review.json" if kind == "ollama" else f"{oid}_review_gpt.json"
            (a / name).write_text(json.dumps({"verdict": verdict, "findings": []}), encoding="utf-8", newline="\n")

    def step(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        return run([str(TOOLS / "step.py"), *args], cwd=self.dir, ok=ok)

    def gates_json(self) -> dict:
        r = run([str(GATES / "run_gates.py"), "--json"], cwd=self.dir, ok=False)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def red(self) -> list[str]:
        return [k for k, v in self.gates_json()["gates"].items() if not v["ok"]]

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class PluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()

    def tearDown(self) -> None:
        self.p.cleanup()

    def test_plugin_manifest_hooks_skills(self) -> None:
        pj = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["name"], "forge")
        self.assertEqual(pj["version"], json.loads((PLUGIN / "kernel.json").read_text(encoding="utf-8"))["kernel_version"])
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        for ev in ("SessionStart", "PostToolUse", "Stop"):
            self.assertIn(ev, hooks)
            self.assertIn("${CLAUDE_PLUGIN_ROOT}", hooks[ev][0]["hooks"][0]["command"])
        for s in ("forge-rules", "forge-new", "forge-route", "forge-direct", "forge-review", "forge-done", "forge-handover", "forge-audit", "forge-gpt"):
            text = (PLUGIN / "skills" / s / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\nname: " + s), s)
            self.assertNotIn("{{", text, f"{s}: placeholder left")
        for a in ("verifier2.md", "executor-fast.md"):
            self.assertTrue((PLUGIN / "agents" / a).is_file(), a)

    def test_fresh_project_is_green(self) -> None:
        self.assertEqual(self.p.red(), [])
        for f in ("forge.json", "CLAUDE.md", "README.md", "01_PROJECT_CORE/circle/STATE.md", "01_PROJECT_CORE/circle/orders/00_INDEX.md",
                  "01_PROJECT_CORE/PLAN.md", "01_PROJECT_CORE/PROJECT_RULES.md", "01_PROJECT_CORE/state/project.json",
                  "02_Для_пользователя/01_Таблицы/README.md", "02_Для_пользователя/02_Отчёты/README.md",
                  "02_Для_пользователя/Инструкция_по_использованию.md", "AGENTS.md"):
            self.assertTrue((self.p.dir / f).is_file(), f)
        self.assertFalse((self.p.dir / "00_FORGE").exists(), "Core 1 is the plugin, never copied (K23)")
        cfg = json.loads((self.p.dir / "forge.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["kernel_version"], json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"])
        for f in ("CLAUDE.md", "02_Для_пользователя/Инструкция_по_использованию.md"):
            self.assertNotIn("{{", (self.p.dir / f).read_text(encoding="utf-8"), f)

    def test_plan_first_orders_and_sources(self) -> None:
        p = Project(plan={**PLAN, "first_orders": [
            {"title": "Table schema", "goal": "схема", "tier": "HEAVY", "dod": ["колонки", "пример"], "quality": ["понятно"], "folder": "Таблицы"},
            {"title": "Checklist", "goal": "чек-лист", "tier": "LOCAL", "dod": ["8 пунктов"], "folder": "Отчёты"}]})
        try:
            rows = (p.circle / "orders" / "00_INDEX.md").read_text(encoding="utf-8")
            self.assertIn("| N001 |", rows); self.assertIn("| N002 |", rows); self.assertIn("| LOCAL |", rows)
            md = next((p.circle / "orders").glob("N001_*.md")).read_text(encoding="utf-8")
            self.assertIn("02_Для_пользователя/01_Таблицы/N001_table_schema.md", md)
            self.assertIn("- понятно", md)                                                    # quality bar in the order
            self.assertIn("«отчёт»", (p.dir / "01_PROJECT_CORE" / "PLAN.md").read_text(encoding="utf-8"))
            self.assertEqual(p.red(), [])
        finally:
            p.cleanup()

    def test_order_lifecycle(self) -> None:
        p = self.p
        p.order("new", "First order", "--tier", "LEAD", "--changes", "docs", "--dod", "a;b")
        self.assertIn("N001", (p.circle / "orders" / "00_INDEX.md").read_text(encoding="utf-8"))
        p.order("take", "N001")
        st = (p.circle / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("STATE: RUNNING", st); self.assertIn("CURRENT_ORDER: N001", st)
        self.assertEqual(p.red(), [])
        self.assertNotEqual(p.order("close", "N001", ok=False).returncode, 0)             # no answer/report yet
        p.order("answer", "N001", "--executor", "lead", "--summary", "done")
        self.assertNotEqual(p.order("close", "N001", ok=False).returncode, 0)             # still no report
        p.order("report", "N001", "--verdict", "CHANGES", "--text", "fix it", "--by", "verifier1:ollama")
        self.assertNotEqual(p.order("close", "N001", ok=False).returncode, 0)             # verdict not APPROVE
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "verifier1:ollama")  # second actor (K8/R24)
        self.assertIn("K31", p.order("close", "N001", ok=False).stderr)                   # claimed, but no review file
        p.evidence("N001", "ollama", "APPROVE")
        p.order("close", "N001")
        st = (p.circle / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("STATE: READY", st); self.assertIn("CURRENT_ORDER: -", st)
        self.assertEqual(p.red(), [])
        journal = list((p.circle / "journal").glob("*.md"))
        self.assertEqual(len(journal), 1); self.assertIn("closed", journal[0].read_text(encoding="utf-8"))

    def test_one_non_local_taken_parallel_local_and_gate_lessons(self) -> None:
        p = self.p
        p.order("new", "Big work", "--tier", "HEAVY", "--changes", "x"); p.order("new", "Draft A", "--tier", "LOCAL", "--changes", "y")
        p.order("take", "N001"); p.order("take", "N002")                                     # R26: LOCAL alongside HEAVY
        self.assertEqual(p.red(), [])
        self.assertIn("CURRENT_ORDER: N001", (p.circle / "STATE.md").read_text(encoding="utf-8"))
        p.order("new", "Big work 2", "--tier", "HEAVY", "--changes", "z")
        self.assertNotEqual(p.order("take", "N003", ok=False).returncode, 0)                  # second non-LOCAL refused
        d = p.read / "N002_draft_a.md"; d.write_text("# PLAN.md\nUpdate the section\n", encoding="utf-8", newline="\n")
        p.order("answer", "N002", "--executor", "ollama:x", "--summary", "s", "--files", "02_Для_пользователя/N002_draft_a.md")
        p.order("report", "N002", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        r = p.order("close", "N002", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("gate_lessons", r.stdout + r.stderr)  # known failure class caught
        d.write_text("# Черновик A\n\nСодержание документа на русском языке для пользователя проекта.\n" * 3, encoding="utf-8", newline="\n")
        p.order("close", "N002")
        self.assertIn("CURRENT_ORDER: N001", (p.circle / "STATE.md").read_text(encoding="utf-8"))
        p.order("hold", "N001", "--why", "pause"); p.order("take", "N003")
        self.assertEqual(p.red(), [])

    def test_language_check_reads_documents_not_raw_outputs(self) -> None:
        """N012: raw tool outputs (.txt) in the user part are data, not documents - only .md must be in the user's language."""
        p = self.p
        p.order("new", "Run", "--tier", "LOCAL", "--changes", "x"); p.order("take", "N001")
        raw = p.read / "qc_output.txt"; raw.write_text("frame contrast bbox need\n" * 40, encoding="utf-8", newline="\n")
        doc = p.read / "N001_run.md"
        doc.write_text("# Report\n\nThis document is written in English for the owner.\n" * 12, encoding="utf-8", newline="\n")
        files = ",".join(f.relative_to(p.dir).as_posix() for f in (raw, doc))
        p.order("answer", "N001", "--executor", "ollama:x", "--summary", "s", "--files", files)
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        r = p.order("close", "N001", ok=False)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0); self.assertIn("N001_run.md: deliverable is not in the user's language", out)  # must fail
        self.assertNotIn("qc_output.txt", out)
        doc.write_text("# Отчёт\n\nЭтот документ написан на русском языке для владельца проекта.\n" * 12, encoding="utf-8", newline="\n")
        p.order("close", "N001")

    def test_gate_index_catches_hand_edits(self) -> None:
        p = self.p
        p.order("new", "A", "--changes", "x")
        idx = p.circle / "orders" / "00_INDEX.md"
        s = idx.read_text(encoding="utf-8")
        idx.write_text(s.replace("| OPEN |", "| DONE |"), encoding="utf-8", newline="\n")   # invalid token
        self.assertIn("gate_index", p.red())
        idx.write_text(s + s.splitlines()[-1] + "\n", encoding="utf-8", newline="\n")       # duplicate id
        self.assertIn("gate_index", p.red())
        idx.write_text(s, encoding="utf-8", newline="\n")
        (p.circle / "orders" / "N002_orphan.md").write_text("# orphan\n", encoding="utf-8", newline="\n")
        self.assertIn("gate_index", p.red())

    def test_gate_state_catches_inconsistency(self) -> None:
        p = self.p
        p.order("new", "A", "--changes", "x")
        st = p.circle / "STATE.md"
        st.write_text("STATE: RUNNING\nUPDATED: -\nCURRENT_ORDER: N001\n", encoding="utf-8", newline="\n")  # RUNNING but nothing TAKEN
        self.assertIn("gate_state", p.red())
        st.write_text("STATE: FLYING\nUPDATED: -\nCURRENT_ORDER: -\n", encoding="utf-8", newline="\n")
        self.assertIn("gate_state", p.red())

    def test_gate_json_encoding_size(self) -> None:
        p = self.p
        bad = p.circle / "answers" / "N001.json"
        bad.write_text('{"order": "N001"}', encoding="utf-8", newline="\n")           # missing keys
        self.assertIn("gate_json", p.red())
        bad.write_text("{not json", encoding="utf-8", newline="\n")
        self.assertIn("gate_json", p.red())
        bad.unlink()
        crlf = p.docs / "crlf.md"
        crlf.write_bytes(b"# x\r\nline\r\n")
        self.assertIn("gate_encoding", p.red())
        crlf.write_bytes(b"\xef\xbb\xbf# bom\n")
        self.assertIn("gate_encoding", p.red())
        crlf.unlink()
        big = p.docs / "big.md"
        big.write_text("x" * (65 * 1024), encoding="utf-8", newline="\n")
        self.assertIn("gate_size", p.red())
        big.unlink()
        data = p.docs / "data.csv"  # N012: a dataset is read by scripts - its own limit (max_kb_data, 2048 KB)
        data.write_text("a,b\n" * (269 * 256), encoding="utf-8", newline="\n")
        self.assertNotIn("gate_size", p.red())
        data.write_text("a,b\n" * (2049 * 256), encoding="utf-8", newline="\n")
        self.assertIn("gate_size", p.red())
        data.unlink()
        self.assertEqual(p.red(), [])

    def test_check_file_hook(self) -> None:
        p = self.p
        bad = p.docs / "x.md"; bad.write_bytes(b"a\r\nb\r\n")
        self.assertEqual(run([str(GATES / "check_file.py"), str(bad)], cwd=p.dir, ok=False).returncode, 1)
        r = subprocess.run([PY, str(GATES / "check_file.py"), "--hook"], cwd=p.dir, input=json.dumps({"tool_input": {"file_path": str(bad)}}),
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2, "hook exit 2 = the Lead must fix the file now")
        bad.write_bytes(b"a\nb\n")
        self.assertEqual(run([str(GATES / "check_file.py"), str(bad)], cwd=p.dir).returncode, 0)

    def test_lessons_evidence_and_kernel_proposal(self) -> None:
        p = self.p
        run([str(TOOLS / "lesson.py"), "--failed", "x", "--cause", "y", "--fix", "z", "--applies", "project", "--source", "gate", "--evidence", "e"], cwd=p.dir)
        run([str(TOOLS / "lesson.py"), "--worked", "x", "--cause", "y", "--fix", "z", "--applies", "kernel", "--source", "review", "--evidence", "e"], cwd=p.dir)
        ids = sorted(f.name[-9:-5] for f in (p.circle / "lessons").glob("*.json"))
        self.assertEqual(ids, ["L001", "L002"])
        self.assertTrue((p.docs / "KERNEL_PROPOSALS.md").is_file())                       # K18/K24: proposal, not an edit
        r = run([str(TOOLS / "lesson.py"), "--failed", "x", "--cause", "y", "--fix", "z", "--applies", "project", "--source", "gate"], cwd=p.dir, ok=False)
        self.assertNotEqual(r.returncode, 0, "evidence is mandatory")
        self.assertEqual(p.red(), [])

    def test_k8_same_actor_refused_and_auto_lesson(self) -> None:
        p = self.p
        p.order("new", "Draft", "--tier", "LOCAL", "--changes", "x", "--dod", "a")
        p.order("take", "N001")
        p.order("answer", "N001", "--executor", "ollama:gemma4:12b", "--summary", "draft")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "fine", "--by", "verifier1:ollama")
        self.assertNotEqual(p.order("close", "N001", ok=False).returncode, 0)          # K8: ollama-by-ollama
        p.order("report", "N001", "--verdict", "CHANGES", "--text", "language is English", "--by", "lead:claude")
        lessons = list((p.circle / "lessons").glob("*.json"))
        self.assertEqual(len(lessons), 1)                                                 # K12: auto-lesson on CHANGES
        self.assertIn('"source": "review"', lessons[0].read_text(encoding="utf-8"))
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok now", "--by", "lead:claude")
        p.order("close", "N001")
        self.assertEqual(p.red(), [])

    def test_lead_artifact_needs_second_actor_and_external_waits_for_owner(self) -> None:
        p = self.p
        p.order("new", "Letter", "--tier", "LEAD", "--changes", "x", "--external")
        p.order("take", "N001")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "written")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        r = p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("verifier2", r.stdout + r.stderr)    # lead-by-lead without verifier (external = important)
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--verified-by", "verifier2")
        r = p.order("close", "N001", ok=False)
        self.assertIn("verifier2", r.stdout + r.stderr)                                   # a name is not evidence (K31)
        p.evidence("N001", "v2", "APPROVE")
        r = p.order("close", "N001", ok=False)
        self.assertIn("K30", r.stdout + r.stderr)                                         # external implies a ChatGPT audit
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--verified-by", "verifier2",
                "--rung3-skip", "test project: ChatGPT not allowed")
        r = p.order("close", "N001", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("AWAITING_OWNER", (p.circle / "STATE.md").read_text(encoding="utf-8"))  # R21
        self.assertIn("--owner-text", r.stdout + r.stderr)                                 # 1.2.0: the hint is the command that works
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "owner", "--owner-text", "ok")
        (p.circle / "STATE.md").write_text("STATE: RUNNING\nUPDATED: -\nCURRENT_ORDER: N001\n", encoding="utf-8", newline="\n")
        p.order("close", "N001")
        self.assertEqual(p.red(), [])

    def test_order_has_context_deliverable_language(self) -> None:
        p = self.p
        p.order("new", "Make checklist", "--tier", "LOCAL", "--changes", "x", "--scope", "01_PROJECT_CORE/PLAN.md", "--quality", "понятно;кратко")
        md = next((p.circle / "orders").glob("N001_*.md")).read_text(encoding="utf-8")
        self.assertIn("## Context", md); self.assertIn("`01_PROJECT_CORE/PLAN.md`", md)
        self.assertIn("/N001_make_checklist.md`", md)
        self.assertIn("Language of the deliverable:** ru", md); self.assertIn("- кратко", md)

    def test_modes(self) -> None:
        p = self.p
        p.order("new", "Draft", "--tier", "LOCAL", "--changes", "x")
        p.step("mode", "expensive")
        self.assertNotEqual(p.order("take", "N001", ok=False).returncode, 0)             # K25: LOCAL refused
        p.step("mode", "cheap")
        p.order("take", "N001")
        cfg = json.loads((p.dir / "forge.json").read_text(encoding="utf-8")); cfg["mode"] = None
        (p.dir / "forge.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        out = json.loads(p.step("--json", "--no-gates").stdout)
        self.assertIn("ask the owner which mode", out["next"])                            # K25: asks before work

    def test_done_one_command_and_project_json_sync(self) -> None:
        p = self.p
        pj = p.state / "project.json"
        p.order("new", "Draft schema", "--tier", "LEAD", "--changes", "schema", "--dod", "a")
        self.assertEqual(json.loads(pj.read_text(encoding="utf-8"))["tasks"][0]["status"], "todo")
        p.order("take", "N001")
        p.evidence("N001", "ollama", "APPROVE")
        p.order("done", "N001", "--text", "принято", "--executor", "lead:claude", "--summary", "schema written", "--by", "verifier1:ollama")
        self.assertIn("| CLOSED |", (p.circle / "orders" / "00_INDEX.md").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(pj.read_text(encoding="utf-8"))["tasks"][0]["status"], "done")
        self.assertEqual(p.red(), [])

    def test_step_brief_rule_end(self) -> None:
        p = self.p
        out = json.loads(p.step("--json").stdout)
        self.assertIn("no open orders", out["next"]); self.assertTrue(out["gates_ok"])
        p.order("new", "A", "--changes", "x")
        self.assertIn("take N001", json.loads(p.step("--json").stdout)["next"])
        brief = p.step("--brief").stdout
        self.assertIn("[Forge]", brief); self.assertRegex(brief, r"plugin \d+\.\d+\.\d+"); self.assertLessEqual(len(brief.splitlines()), 15)
        self.assertIn(PLUGIN.as_posix(), brief, "the brief tells the Lead where the plugin is")
        self.assertIn("K8", p.step("rule", "K8").stdout)
        p.step("end", "--did", "test session")
        self.assertIn("test session", (p.state / "session_log.md").read_text(encoding="utf-8"))


    # ---- 0.8.0: two model families (K29-K31) ------------------------------------------------------------------------

    def test_codex_never_leads(self) -> None:
        p = self.p
        r = p.order("new", "A", "--changes", "x", ok=False, env={"CODEX_THREAD_ID": "t-1"})
        self.assertNotEqual(r.returncode, 0); self.assertIn("K29", r.stdout + r.stderr)   # ChatGPT never leads
        self.assertEqual(p.order("list", env={"CODEX_THREAD_ID": "t-1"}).returncode, 0)    # reading is fine
        self.assertNotIn("N001", (p.circle / "orders" / "00_INDEX.md").read_text(encoding="utf-8"))

    def test_gpt_switch_agents_md_and_image_tier(self) -> None:
        p = self.p
        cfg = json.loads((p.dir / "forge.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["gpt"], "denied")                                              # K15: off until the owner says yes
        agents = (p.dir / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("<!-- forge:agents", agents); self.assertIn("NOT allowed", agents); self.assertNotIn("{{", agents)
        r = p.order("new", "Cover", "--tier", "GPT", "--changes", "img", "--deliverable", "02_Для_пользователя/cover.png", ok=False)
        self.assertIn("K15", r.stdout + r.stderr)
        (p.dir / "AGENTS.md").write_text("# AGENTS.md - Codex is the Lead here\n", encoding="utf-8", newline="\n")  # an imported file
        run([str(TOOLS / "gpt_setup.py"), str(p.dir), "--allow"], cwd=p.dir)
        self.assertEqual(json.loads((p.dir / "forge.json").read_text(encoding="utf-8"))["gpt"], "allowed")
        self.assertIn("second family", (p.dir / "AGENTS.md").read_text(encoding="utf-8"))   # 1.0: executor + reviewer, never the Lead
        self.assertTrue(list(p.state.glob("AGENTS.md.bak-*")), "the replaced AGENTS.md is kept")
        r = p.order("new", "Text", "--tier", "GPT", "--changes", "t", "--deliverable", "02_Для_пользователя/t.md", ok=False)
        self.assertIn("K29", r.stdout + r.stderr)                                          # GPT tier = images only
        p.order("new", "Cover", "--tier", "GPT", "--changes", "img", "--deliverable", "02_Для_пользователя/cover.png")
        p.order("new", "Doc", "--tier", "HEAVY", "--changes", "doc")
        p.order("take", "N002"); p.order("take", "N001")                                      # GPT runs alongside, like LOCAL
        self.assertEqual(p.red(), [])
        p.order("answer", "N001", "--executor", "gpt:codex", "--summary", "image made")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "gpt:codex-subagent")
        r = p.order("close", "N001", ok=False)
        self.assertIn("K8", r.stdout + r.stderr)                                           # GPT-by-GPT is one actor (mesh N035)
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "смотрел картинку", "--by", "lead:claude")
        p.order("close", "N001")
        self.assertEqual(p.red(), [])

    def test_changes_review_blocks_until_rerun_or_overrule(self) -> None:
        p = self.p
        p.order("new", "Doc", "--tier", "LEAD", "--changes", "x"); p.order("take", "N001")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "written")
        p.evidence("N001", "ollama", "CHANGES")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude")
        r = p.order("close", "N001", ok=False)
        self.assertIn("K31", r.stdout + r.stderr)                                          # audit: 9 closes over CHANGES
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--overrule", "V1 cut the file at 6000 chars")
        p.order("close", "N001")
        lessons = [f.read_text(encoding="utf-8") for f in (p.circle / "lessons").glob("*.json")]
        self.assertTrue(any('"source": "overrule"' in x for x in lessons), "an overrule is a lesson (K12)")
        self.assertIn("OVERRULED:", (p.circle / "reports" / "N001.md").read_text(encoding="utf-8"))

    def test_important_order_needs_triaged_chatgpt_audit(self) -> None:
        p = self.p
        p.order("new", "Legal memo", "--tier", "LEAD", "--changes", "x", "--important")
        md = next((p.circle / "orders").glob("N001_*.md")).read_text(encoding="utf-8")
        self.assertIn("**Rung 3:** required", md)
        p.order("take", "N001"); p.order("answer", "N001", "--executor", "lead:claude", "--summary", "written")
        p.evidence("N001", "v2", "APPROVE")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--verified-by", "verifier2")
        self.assertIn("K30", p.order("close", "N001", ok=False).stderr)                    # Claude checked; ChatGPT audit missing
        p.evidence("N001", "gpt", "CHANGES")
        self.assertIn("triage", p.order("close", "N001", ok=False).stderr)                 # findings must be triaged
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--verified-by", "verifier2",
                "--rung3", "accepted 1 (fixed date), rejected 1 (wrong: primary source says otherwise)")
        p.order("close", "N001")
        self.assertIn("RUNG3:", (p.circle / "reports" / "N001.md").read_text(encoding="utf-8"))
        p.order("new", "B", "--changes", "y"); p.order("take", "N002")
        p.order("answer", "N002", "--executor", "lead:claude", "--summary", "s"); p.evidence("N002", "v2", "APPROVE")
        p.order("report", "N002", "--verdict", "APPROVE", "--text", "ok", "--by", "gpt:codex", "--verified-by", "verifier2")
        self.assertIn("K29", p.order("close", "N002", ok=False).stderr)                    # ChatGPT never writes the closing report

    def test_hold_needs_reason_and_owner_fix_is_a_lesson(self) -> None:
        p = self.p
        p.order("new", "A", "--changes", "x"); p.order("take", "N001")
        self.assertIn("reason", p.order("hold", "N001", ok=False).stderr)
        p.order("hold", "N001", "--why", "waiting for data")
        p.order("new", "Fix A", "--changes", "fix", "--fixes", "N001", "--goal", "owner: the slogan is still there")
        lessons = [f.read_text(encoding="utf-8") for f in (p.circle / "lessons").glob("*.json")]
        self.assertTrue(any('"source": "owner"' in x and "N001" in x for x in lessons))
        self.assertEqual(p.red(), [])

    def test_commit_takes_only_the_orders_files(self) -> None:
        p = self.p
        git = lambda *a: subprocess.run(["git", *a], cwd=p.dir, capture_output=True, text=True, encoding="utf-8", errors="replace")  # noqa: E731
        git("init", "-q"); git("config", "user.email", "t@example.com"); git("config", "user.name", "t")
        git("add", "-A"); git("commit", "-q", "-m", "init")
        (p.dir / "STRAY_codex_file.md").write_text("made by another agent\n", encoding="utf-8", newline="\n")
        p.order("new", "Doc", "--tier", "LEAD", "--changes", "doc", "--deliverable", "02_Для_пользователя/doc.md"); p.order("take", "N001")
        (p.read / "doc.md").write_text("# Документ\n\nТекст документа для владельца проекта на русском языке.\n" * 3, encoding="utf-8", newline="\n")
        p.evidence("N001", "v2", "APPROVE")
        r = p.order("done", "N001", "--text", "ok", "--executor", "lead:claude", "--summary", "s", "--files", "02_Для_пользователя/doc.md",
                    "--verified-by", "verifier2", "--commit")
        self.assertIn("not committed", r.stdout)                                            # the stray file is reported, not swept in
        files = git("ls-files").stdout
        self.assertIn("doc.md", files); self.assertIn("N001_review_v2.md", files); self.assertNotIn("STRAY_codex_file.md", files)
        self.assertIn("Co-Authored-By: Claude <noreply@anthropic.com>", git("log", "-1", "--format=%B").stdout)

    def test_gpt_bridge_policy_offline(self) -> None:
        p = self.p
        pol = p.tmp / "policy.json"; pol.write_text(json.dumps({"deny": [str(p.read)]}), encoding="utf-8")
        probe = ("import sys, gpt_bridge as g\n"
                 "for path in sys.argv[1:]:\n"
                 "    try:\n        g.check_paths([path]); print('ALLOWED', path)\n"
                 "    except g.PolicyError as e:\n        print('REFUSED', path)\n")
        env = {"FORGE_GPT_POLICY": str(pol), "PYTHONPATH": str(TOOLS)}
        paths = [str(p.circle), str(p.read / "x.md"), str(p.tmp)]
        out = run(["-c", probe, *paths], cwd=p.dir, env=env).stdout
        self.assertIn("REFUSED " + str(p.circle), out)                                     # project not allowed yet (K15)
        run([str(TOOLS / "gpt_setup.py"), str(p.dir), "--allow"], cwd=p.dir)
        out = run(["-c", probe, *paths], cwd=p.dir, env=env).stdout
        self.assertIn("ALLOWED " + str(p.circle), out)
        self.assertIn("REFUSED " + str(p.read / "x.md"), out)                              # owner's deny list wins
        self.assertIn("REFUSED " + str(p.tmp), out)                                        # outside any Forge project
        nested = run(["-c", "import gpt_bridge as g\ntry:\n    g.run('x', '.')\nexcept g.PolicyError as e:\n    print('REFUSED', e)\n"],
                     cwd=p.dir, env={**env, "CODEX_THREAD_ID": "t"}).stdout
        self.assertIn("K29", nested)                                                        # no ChatGPT-calls-ChatGPT

    def test_md2pdf_never_writes_into_a_md(self) -> None:
        p = self.p
        a = p.docs / "a.md"; b = p.docs / "b.md"
        a.write_text("# A\n", encoding="utf-8", newline="\n"); b.write_text("keep me\n", encoding="utf-8", newline="\n")
        r = run([str(TOOLS / "md2pdf.py"), str(a), str(b)], cwd=p.dir, ok=False)
        self.assertEqual(r.returncode, 2); self.assertEqual(b.read_text(encoding="utf-8"), "keep me\n")   # producer quest N002

    def test_step_end_help_does_not_close_the_session(self) -> None:
        p = self.p
        log = p.state / "session_log.md"
        before = log.read_text(encoding="utf-8") if log.is_file() else ""
        r = p.step("end", "--help")
        self.assertIn("usage", r.stdout)
        self.assertEqual(before, log.read_text(encoding="utf-8") if log.is_file() else "")   # R-42

    # ---- 0.8.0 round 2: holes found by the N009 reviews (verifier2 + ChatGPT) -------------------------------------------

    def test_evidence_must_be_this_orders_fresh_claude_review(self) -> None:
        p = self.p
        p.order("new", "Doc", "--tier", "LEAD", "--changes", "x", "--deliverable", "02_Для_пользователя/d.md"); p.order("take", "N001")
        (p.read / "d.md").write_text("# Документ\n\nТекст документа для владельца проекта на русском языке.\n" * 3, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "s", "--files", "02_Для_пользователя/d.md")
        p.evidence("N001", "gpt", "APPROVE")
        import os, time
        old = time.time() - 3600
        os.utime(p.circle / "answers" / "N001_review_gpt.json", (old, old))                # 1.0: a Codex APPROVE counts - but not a stale one
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude",
                "--verified-by", "01_PROJECT_CORE/circle/answers/N001_review_gpt.json 01_PROJECT_CORE/circle/reports/N001.md", "--rung3", "x")
        self.assertIn("R24", p.order("close", "N001", ok=False).stderr)                     # a stale review file or the own report is not evidence
        (p.circle / "answers" / "N001_review_gpt.json").unlink()                           # (an earlier cycle's audit would block the close itself)
        p.evidence("N001", "v2", "APPROVE")
        os.utime(p.circle / "answers" / "N001_review_v2.md", (old, old))                   # review older than the deliverable
        self.assertIn("Stale", p.order("close", "N001", ok=False).stderr)
        p.evidence("N001", "v2", "APPROVE")
        p.order("close", "N001")

    def test_important_needs_verifier2_and_a_real_audit(self) -> None:
        p = self.p
        p.order("new", "Memo", "--tier", "LEAD", "--changes", "x", "--important"); p.order("take", "N001")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "s")
        p.evidence("N001", "ollama", "APPROVE")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--rung3-skip", "x")
        self.assertIn("verifier2", p.order("close", "N001", ok=False).stderr)               # Ollama does not replace Claude's review
        p.evidence("N001", "v2", "APPROVE")
        (p.circle / "answers" / "N001_review_gpt.json").write_text(json.dumps({"verdict": "", "_meta": {"ok": False}}), encoding="utf-8")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--verified-by", "verifier2", "--rung3", "nothing")
        self.assertIn("no verdict", p.order("close", "N001", ok=False).stderr)              # a failed ChatGPT run is not an audit
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--verified-by", "verifier2",
                "--rung3-skip", "ChatGPT run failed twice (timeout)")
        p.order("close", "N001")

    def test_hold_keeps_parallel_gpt_order_running_and_staged_strays_stay_out(self) -> None:
        p = self.p
        run([str(TOOLS / "gpt_setup.py"), str(p.dir), "--allow"], cwd=p.dir)
        p.order("new", "Doc", "--tier", "HEAVY", "--changes", "d")
        p.order("new", "Img", "--tier", "GPT", "--changes", "i", "--deliverable", "02_Для_пользователя/i.png")
        p.order("take", "N001"); p.order("take", "N002")
        p.order("hold", "N001", "--why", "waiting")
        st = (p.circle / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("STATE: RUNNING", st); self.assertIn("CURRENT_ORDER: N002", st); self.assertEqual(p.red(), [])
        self.assertNotEqual(p.order("amend", "N002", "--why", "x", "--deliverable", "app.py", ok=False).returncode, 0)  # images only
        git = lambda *a: subprocess.run(["git", *a], cwd=p.dir, capture_output=True, text=True, encoding="utf-8", errors="replace")  # noqa: E731
        git("init", "-q"); git("config", "user.email", "t@example.com"); git("config", "user.name", "t")
        git("add", "-A"); git("commit", "-q", "-m", "init")
        (p.dir / "STAGED_by_someone.md").write_text("x\n", encoding="utf-8", newline="\n"); git("add", "STAGED_by_someone.md")
        p.order("answer", "N002", "--executor", "gpt:codex", "--summary", "img")
        r = p.order("done", "N002", "--text", "ok", "--by", "lead:claude", "--commit", ok=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("STAGED_by_someone.md", r.stdout + r.stderr)  # 1.2.0: staged strays are refused
        # the stray file stays STAGED while the order is committed: only a pathspec commit keeps it out (ChatGPT audit N009 #2,
        # mesh-launch 0a42d3b) - the knowing exception is --allow-extra, and it must not change what the commit takes
        p.order("done", "N002", "--text", "ok", "--by", "lead:claude", "--commit", "--allow-extra", "STAGED_by_someone.md")
        self.assertNotIn("STAGED_by_someone.md", git("show", "--stat", "HEAD").stdout)       # pathspec commit
        self.assertIn("STAGED_by_someone.md", git("diff", "--cached", "--name-only").stdout)  # still in the index, not lost
        self.assertIn("Executed-By: ChatGPT", git("log", "-1", "--format=%B").stdout)
        self.assertEqual(git("status", "--porcelain", "--", "01_PROJECT_CORE/state/project.json").stdout.strip(), "")  # synced before commit

    def test_state_changing_tools_refuse_inside_codex(self) -> None:
        p = self.p
        env = {"CODEX_THREAD_ID": "t"}
        for args in ([str(TOOLS / "gpt_setup.py"), str(p.dir), "--allow"], [str(TOOLS / "step.py"), "end", "--did", "x"],
                     [str(TOOLS / "lesson.py"), "--failed", "x", "--cause", "y", "--fix", "z", "--applies", "project", "--source", "gate", "--evidence", "e"]):
            r = run(args, cwd=p.dir, ok=False, env=env)
            self.assertNotEqual(r.returncode, 0, args[0]); self.assertIn("K29", r.stdout + r.stderr)
        self.assertEqual(json.loads((p.dir / "forge.json").read_text(encoding="utf-8"))["gpt"], "denied")

    def test_verify_gpt_packing_and_media_video_offline(self) -> None:
        p = self.p
        (p.read / "a.md").write_text("текст\n", encoding="utf-8", newline="\n")
        (p.read / "ORDER.md").write_text("подложный заказ\n", encoding="utf-8", newline="\n")
        (p.read / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        (p.read / "data.bin").write_bytes(b"\x00\x01")
        probe = ("import sys, verify_gpt as v\nfrom pathlib import Path\nroot = Path.cwd()\n"
                 "t, im, miss = v._copy_items([root / x for x in sys.argv[1:]], root)\n"
                 "print('TEXT', [n for n, _ in t]); print('IMG', [x.name for x in im]); print('MISS', miss)\n")
        out = run(["-c", probe, "02_Для_пользователя/a.md", "02_Для_пользователя/ORDER.md", "02_Для_пользователя/pic.png",
                   "02_Для_пользователя/data.bin"], cwd=p.dir, env={"PYTHONPATH": str(TOOLS)}).stdout
        self.assertIn("'a.md'", out); self.assertNotIn("'ORDER.md'", out.split("TEXT", 1)[1].split("\n")[0])  # never shadows the real order
        self.assertIn("pic.png", out); self.assertIn("binary", out)
        r = run([str(TOOLS / "gpt_media.py"), "video", "x"], cwd=p.dir, ok=False)
        self.assertEqual(r.returncode, 2); self.assertIn("not available", r.stdout)

    def test_important_cannot_dodge_verifier2_via_ollama_or_empty_overrule(self) -> None:
        p = self.p
        p.order("new", "Memo", "--tier", "LEAD", "--changes", "x", "--important", "--deliverable", "02_Для_пользователя/m.md")
        p.order("take", "N001")
        (p.read / "m.md").write_text("# Записка\n\nТекст служебной записки для владельца проекта на русском языке.\n" * 3, encoding="utf-8", newline="\n")
        p.order("answer", "N001", "--executor", "lead:claude", "--summary", "s", "--files", "02_Для_пользователя/m.md")
        p.evidence("N001", "ollama", "APPROVE")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "verifier1:ollama", "--rung3-skip", "x")
        self.assertIn("verifier2", p.order("close", "N001", ok=False).stderr)               # BY ollama does not dodge Claude's check
        (p.circle / "answers" / "N001_review_v2.md").write_text("no verdict here\n", encoding="utf-8", newline="\n")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--rung3-skip", "x", "--overrule", "x")
        self.assertIn("verifier2", p.order("close", "N001", ok=False).stderr)               # an overrule needs a real CHANGES to answer
        import os, time
        old = time.time() - 3600
        os.utime(p.circle / "answers" / "N001_review.json", (old, old))
        p.order("new", "B", "--changes", "y")  # unrelated; keeps the index realistic
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "verifier1:ollama", "--rung3-skip", "x")
        self.assertIn("older", p.order("close", "N001", ok=False).stderr)                   # a stale Ollama approval is refused
        p.evidence("N001", "v2", "APPROVE")
        p.order("report", "N001", "--verdict", "APPROVE", "--text", "ok", "--by", "lead:claude", "--verified-by", "verifier2", "--rung3-skip", "K15 off in tests")
        p.order("close", "N001")

if __name__ == "__main__":
    unittest.main()
