"""1.2.0 gate_docs: the plugin's documents agree with the plugin on disk; a no-op outside the plugin source.

Fault-injection tests run on a copy of the plugin and look only for the problems the injected fault adds (`new()`), so they do not
depend on drift that is already open in the real tree; `test_real_plugin_is_consistent` is the one test that reads the real tree
and wants it clean."""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "gates"))
import gate_docs  # noqa: E402
import run_gates  # noqa: E402

KEEP = ("README.md", "TOOLS.md", "CONSTITUTION.md", "kernel.json", "domains.json")


class TestGateDocs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = self.tmp / "plugin"
        self.p.mkdir()
        for n in KEEP:
            shutil.copy(PLUGIN / n, self.p / n)
        shutil.copytree(PLUGIN / ".claude-plugin", self.p / ".claude-plugin")
        for d in ("skills", "agents", "gates", "tools"):
            shutil.copytree(PLUGIN / d, self.p / d, ignore=shutil.ignore_patterns("__pycache__"))
        self.base = set(gate_docs.check_plugin(self.p))  # whatever is already open in the real tree stays out of the way

    def new(self) -> list[str]:
        return [x for x in gate_docs.check_plugin(self.p) if x not in self.base]

    def edit(self, rel: str, old: str, new: str):
        f = self.p / rel
        t = f.read_text(encoding="utf-8")
        self.assertIn(old, t)
        f.write_text(t.replace(old, new, 1), encoding="utf-8", newline="\n")

    def expect(self, *needles: str):
        out = self.new()
        for needle in needles:
            self.assertTrue(any(needle in x for x in out), (needle, out))

    def version(self) -> str:
        return json.loads((self.p / "kernel.json").read_text(encoding="utf-8"))["kernel_version"]

    # ---- the real tree ----
    def test_real_plugin_is_consistent(self):
        self.assertEqual(gate_docs.check_plugin(PLUGIN), [])

    # ---- versions ----
    def test_plugin_json_version_mismatch(self):
        v = self.version()
        self.edit(".claude-plugin/plugin.json", f'"version": "{v}"', '"version": "0.0.1"')
        out = self.new()
        self.assertTrue(any("plugin.json" in x and repr(v) in x for x in out), out)

    def test_headers_must_name_the_version(self):
        v = self.version()
        self.edit("README.md", f"# Forge plugin v{v}", "# Forge plugin v0.0.1")
        self.edit("TOOLS.md", f"v{v})", "v0.0.1)")
        out = self.new()
        self.assertTrue(any(x.startswith("README.md") and "0.0.1" in x and v in x for x in out), out)
        self.assertTrue(any(x.startswith("TOOLS.md") and "0.0.1" in x and v in x for x in out), out)

    def test_forge_rules_header_must_name_the_version(self):
        v = self.version()
        self.edit("skills/forge-rules/SKILL.md", f"plugin {v}", "plugin 0.0.1")
        out = self.new()
        self.assertTrue(any(x.startswith("skills/forge-rules/SKILL.md") and "0.0.1" in x and v in x for x in out), out)

    def test_changelog_last_entry_is_current(self):
        k = json.loads((self.p / "kernel.json").read_text(encoding="utf-8"))
        k["changelog"].append({"version": "9.9.9", "date": "2026-01-01", "what": "x"})
        (self.p / "kernel.json").write_text(json.dumps(k, indent=2), encoding="utf-8", newline="\n")
        out = self.new()
        self.assertTrue(any("changelog" in x and "9.9.9" in x for x in out), out)

    # ---- counts ----
    def test_rule_count_and_domains(self):
        self.edit("README.md", "rules K1–K", "rules K1–K9")
        self.edit("README.md", "11 domains", "12 domains")
        out = self.new()
        self.assertTrue(any("README.md" in x and "K1-K9" in x for x in out), out)
        self.assertTrue(any("12 domains" in x and "expected 11" in x for x in out), out)

    def test_new_rule_in_the_constitution(self):
        n = max(int(x) for x in re.findall(r"\*\*K(\d+)\b", (self.p / "CONSTITUTION.md").read_text(encoding="utf-8")))
        with (self.p / "CONSTITUTION.md").open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"\n**K{n + 1}** a new rule\n")
        self.expect(f"expected K1-K{n + 1}")

    def test_reworded_rule_count_is_not_silently_skipped(self):
        n = re.search(r"rules K1[–-]K(\d+)", (self.p / "README.md").read_text(encoding="utf-8"))[1]
        self.edit("README.md", f"rules K1–K{n}", f"K1–K{n} rules")
        self.expect("no 'rules K1-Kn' statement")

    def test_reworded_domain_count_is_not_silently_skipped(self):
        n = len(json.loads((self.p / "domains.json").read_text(encoding="utf-8"))["domains"])
        self.edit("README.md", f"{n} domains", "eleven domains")
        self.expect("no 'N domains' statement")

    # ---- the README file tree ----
    def test_skill_agent_and_tool_lists(self):
        shutil.copytree(self.p / "skills" / "forge-done", self.p / "skills" / "forge-extra")
        (self.p / "agents" / "ghost.md").write_text("---\nname: ghost\n---\n", encoding="utf-8")
        (self.p / "tools" / "newtool.py").write_text("", encoding="utf-8")
        (self.p / "gates" / "gate_newone.py").write_text("", encoding="utf-8")
        out = self.new()
        for needle in ("'forge-extra'", "'ghost'", "newtool.py", "gate_newone.py"):
            self.assertTrue(any(needle in x for x in out), (needle, out))

    def test_tree_names_a_tool_that_does_not_exist(self):
        self.edit("README.md", "· reconcile", "· reconcile · ghosttool")
        self.expect("tools/ entry lists 'ghosttool'")

    def test_tree_forgets_a_tool_that_exists(self):
        self.edit("README.md", "· reconcile ·", "·")
        self.expect("tools/ entry does not list 'reconcile'")

    def test_tree_lists_a_tool_twice(self):
        self.edit("README.md", "step · order", "step · order · step")
        self.expect("tools/ entry lists 'step' more than once")

    def test_tree_forgets_a_gate_that_exists(self):
        self.edit("README.md", "· gate_docs (1.2.0)", "")
        self.expect("gates/ entry does not list 'gate_docs'")

    def test_tree_names_a_gate_that_does_not_exist(self):
        self.edit("README.md", "· gpt_guard", "· gpt_guard · gate_ghost")
        self.expect("gates/ entry names 'gate_ghost'")

    def test_renamed_tree_entry_is_not_silently_skipped(self):
        for folder in ("skills", "agents", "gates", "tools"):
            with self.subTest(folder=folder):
                shutil.copy(PLUGIN / "README.md", self.p / "README.md")
                self.edit("README.md", f"├── {folder}/", f"├── {folder}z/")
                self.expect(f"no `{folder}/` entry")

    # ---- TOOLS.md rows ----
    def drop_rows(self, name: str):
        f = self.p / "TOOLS.md"
        lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [ln for ln in lines if not (ln.startswith("|") and name in gate_docs.tools_md_rows(ln))]
        self.assertLess(len(kept), len(lines), f"no row for {name}")
        f.write_text("".join(kept), encoding="utf-8", newline="\n")

    def test_missing_tools_row_even_if_another_name_contains_it(self):
        self.drop_rows("setup")  # `gpt_setup.py` still has its row; it must not stand in for setup.py
        self.expect("tool setup.py is not listed")

    def test_missing_gate_row(self):
        self.drop_rows("gate_docs")
        self.expect("gate gate_docs.py is not listed")

    def test_rows_are_the_first_column_only(self):
        md = "| script | purpose |\n|---|---|\n| `a.py` | calls b.py and `c.py` |\n| d.py | x |\nprose about e.py\n"
        self.assertEqual(gate_docs.tools_md_rows(md), {"a", "d"})
        self.assertEqual(gate_docs.tools_md_rows("| `gpt_setup.py` | x |"), {"gpt_setup"})

    # ---- where it runs ----
    def test_noop_outside_the_project_tree(self):
        self.edit(".claude-plugin/plugin.json", f'"version": "{self.version()}"', '"version": "0.0.1"')  # a real fault
        project = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, project, True)
        self.assertEqual(gate_docs.check({"_kernel_dir": self.p, "_root": project}), [])

    def test_runs_on_the_source_inside_the_workspace(self):
        self.edit(".claude-plugin/plugin.json", f'"version": "{self.version()}"', '"version": "0.0.1"')
        out = gate_docs.check({"_kernel_dir": self.p, "_root": self.tmp})
        self.assertTrue(any("plugin.json" in x for x in out), out)

    def test_run_gates_runs_the_gate_and_reports_its_problems(self):
        # run_gates.run is the entry point of step.py / order.py: the gate must be found there and a fault must turn it red
        self.assertIn("gate_docs", {f.stem for f in run_gates.HERE.glob("gate_*.py")})
        self.edit(".claude-plugin/plugin.json", f'"version": "{self.version()}"', '"version": "0.0.1"')
        only = self.tmp / "only_gate_docs"
        only.mkdir()
        shutil.copy(PLUGIN / "gates" / "gate_docs.py", only / "gate_docs.py")
        with mock.patch.object(run_gates, "HERE", only):
            res = run_gates.run({"_kernel_dir": self.p, "_root": self.tmp})
        self.assertEqual(set(res), {"gate_docs"})
        self.assertFalse(res["gate_docs"]["ok"])
        self.assertTrue(any("plugin.json" in x for x in res["gate_docs"]["problems"]), res)


if __name__ == "__main__":
    unittest.main()
