"""1.2.0 tests (N031): weaken_scan.py reports check weakening in a diff and never blocks. Synthetic diffs go through
scan_diff (no git); a few tests run the CLI in a throw-away git repository."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import PY, TOOLS  # noqa: E402

sys.path.insert(0, str(TOOLS))
import weaken_scan as ws  # noqa: E402

SCRIPT = TOOLS / "weaken_scan.py"


def diff(path: str, hunks: str, extra: str = "") -> str:
    return f"diff --git a/{path} b/{path}\n{extra}--- a/{path}\n+++ b/{path}\n{hunks}"


def kinds(text: str) -> list[str]:
    return [s["kind"] for s in ws.scan_diff(text)]


def added(path: str, *lines: str, at: int = 4) -> str:
    """A -U0 diff of `path` that adds exactly these lines (the hunk header counts them, as git does)."""
    return diff(path, f"@@ -{at - 1},0 +{at},{len(lines)} @@\n" + "".join(f"+{ln}\n" for ln in lines))


class Deleted(unittest.TestCase):
    def test_deleted_test_file(self):
        d = ("diff --git a/tests/test_a.py b/tests/test_a.py\ndeleted file mode 100644\n--- a/tests/test_a.py\n+++ /dev/null\n"
             "@@ -1,2 +0,0 @@\n-def test_x():\n-    assert 1 == 2\n")
        self.assertEqual(kinds(d), ["test_file_deleted"])

    def test_deleted_empty_test_file(self):
        d = "diff --git a/tests/test_e.py b/tests/test_e.py\ndeleted file mode 100644\nindex e69de29..0000000\n"
        self.assertEqual(kinds(d), ["test_file_deleted"])

    def test_deleted_document_in_a_test_folder_is_not_a_signal(self):
        for path in ("docs/specs/api.md", "tests/data/sample.json", "e2e/README.md"):
            with self.subTest(path=path):
                d = (f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n--- a/{path}\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-text\n")
                self.assertEqual(kinds(d), [])

    def test_deleted_source_file_is_not_a_signal(self):
        d = ("diff --git a/src/a.py b/src/a.py\ndeleted file mode 100644\n--- a/src/a.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x = 1\n")
        self.assertEqual(kinds(d), [])

    def test_emptied_test_file(self):
        d = diff("tests/test_a.py", "@@ -1,3 +0,0 @@\n-import unittest\n-def test_x():\n-    pass\n")
        self.assertIn("test_file_emptied", kinds(d))

    def test_removed_test_function(self):
        d = diff("tests/test_a.py", "@@ -5,3 +4,0 @@\n-    def test_login(self):\n-        self.assertEqual(f(), 1)\n-\n")
        got = ws.scan_diff(d)
        self.assertEqual([s["kind"] for s in got if s["kind"] == "test_function_removed"], ["test_function_removed"])
        self.assertEqual(got[0]["line"], 5)
        self.assertEqual(got[0]["side"], "old")

    def test_renamed_test_function_is_not_removed(self):
        d = diff("tests/test_a.py", "@@ -5,1 +5,1 @@\n-    def test_login(self):\n+    def test_login(self, extra=1):\n")
        self.assertNotIn("test_function_removed", kinds(d))


class Markers(unittest.TestCase):
    def test_skip_and_xfail_markers(self):
        for line in ("@unittest.skip('later')", "@pytest.mark.skip", "@pytest.mark.xfail(reason='x')", "it.skip('a', () => {})",
                     "describe.skip('b', () => {})", "test.only('c', () => {})", "xit('d', () => {})", "    self.skipTest('x')",
                     "    pytest.skip('x')", "@unittest.skipIf(x, 'y')", "fit('e', () => {})", "    t.Skip(\"later\")"):
            with self.subTest(line=line):
                d = diff("tests/test_a.py", f"@@ -3,0 +4,1 @@\n+{line}\n")
                self.assertEqual(kinds(d), ["skip_marker_added"])

    def test_marker_words_inside_text_are_not_markers(self):
        # string fixtures, comments, a method named fit: the marker is a statement or a decorator, nothing else
        for line in ("    model.fit(X, y)", "    self.assertEqual(rep, \"@pytest.mark.skip\")", "    # pytest.skip(...) is described here",
                     "    skipped = \"xit('d')\"", "    print('unittest.skip')", "for line in (\"@unittest.skip('later')\",):",
                     "    clf.fit(X)  # fit( on a model"):
            with self.subTest(line=line):
                d = diff("tests/test_a.py", f"@@ -3,0 +4,1 @@\n+{line}\n")
                self.assertEqual(kinds(d), [])

    def test_skip_in_a_removed_line_is_fine(self):
        d = diff("tests/test_a.py", "@@ -3,1 +2,0 @@\n-@unittest.skip('later')\n")
        self.assertEqual(kinds(d), [])

    def test_skip_word_in_source_code_is_ignored(self):
        d = diff("src/a.py", "@@ -3,0 +4,1 @@\n+    # we skip nothing; unittest.skip not used here\n")
        self.assertEqual(kinds(d), [])

    def test_line_numbers_follow_the_new_file(self):
        d = diff("tests/test_a.py", "@@ -1,0 +10,2 @@\n+x = 1\n+@pytest.mark.skip\n")
        self.assertEqual(ws.scan_diff(d)[0]["line"], 11)


class Assertions(unittest.TestCase):
    def test_assertion_removed(self):
        d = diff("tests/test_a.py", "@@ -7,1 +6,0 @@\n-        self.assertEqual(f(), 3)\n")
        self.assertEqual(kinds(d), ["assertion_removed"])

    def test_assert_statement_removed(self):
        d = diff("tests/test_a.py", "@@ -7,1 +6,0 @@\n-    assert f() == 3\n")
        self.assertEqual(kinds(d), ["assertion_removed"])

    def test_assertion_loosened(self):
        d = diff("tests/test_a.py", "@@ -7,1 +7,1 @@\n-        self.assertEqual(f(), 3)\n+        self.assertTrue(f())\n")
        self.assertEqual(kinds(d), ["assertion_loosened"])

    def test_js_assertion_loosened(self):
        d = diff("src/a.test.js", "@@ -7,1 +7,1 @@\n-  expect(f()).toBe(3);\n+  expect(f()).toBeTruthy();\n")
        self.assertEqual(kinds(d), ["assertion_loosened"])

    def test_same_strength_replacement_is_quiet(self):
        d = diff("tests/test_a.py", "@@ -7,1 +7,1 @@\n-        self.assertEqual(f(), 3)\n+        self.assertEqual(f(), 4)\n")
        self.assertEqual(kinds(d), [])

    def test_moved_assertion_is_still_reported(self):
        d = diff("tests/test_a.py", "@@ -7,1 +6,0 @@\n-        self.assertEqual(f(), 3)\n@@ -20,0 +20,1 @@\n+        self.assertEqual(f(), 3)\n")
        self.assertEqual(kinds(d), ["assertion_removed"])  # per block: a move is still shown, the owner decides

    def test_comparison_dropped_to_a_bare_truth_test(self):
        d = diff("tests/test_a.py", "@@ -7,1 +7,1 @@\n-    assert f() == 3\n+    assert f()\n")
        self.assertEqual(kinds(d), ["assertion_loosened"])

    def test_comparison_kept_is_quiet(self):
        d = diff("tests/test_a.py", "@@ -7,1 +7,1 @@\n-    assert f() == 3\n+    assert f() == 4\n")
        self.assertEqual(kinds(d), [])

    def test_removed_raises_check(self):
        for line in ("        with self.assertRaises(ValueError):", "    with pytest.raises(KeyError):"):
            with self.subTest(line=line):
                d = diff("tests/test_a.py", f"@@ -7,1 +6,0 @@\n-{line}\n")
                self.assertEqual(kinds(d), ["assertion_removed"])

    def test_commented_out_test_is_a_removed_test(self):
        d = diff("tests/test_a.py", "@@ -5,2 +5,2 @@\n-    def test_login(self):\n-        pass\n+    # def test_login(self):\n+    #     pass\n")
        self.assertIn("test_function_removed", kinds(d))

    def test_assertions_in_source_code_are_not_test_signals(self):
        d = diff("src/a.py", "@@ -7,1 +6,0 @@\n-    assert x > 0\n")
        self.assertEqual(kinds(d), [])

    def test_trivial_assertion_added(self):
        d = diff("tests/test_a.py", "@@ -7,0 +8,1 @@\n+    assert True\n")
        self.assertIn("trivial_assertion_added", kinds(d))


class Suppressions(unittest.TestCase):
    def test_suppressions(self):
        for path, line in (("src/a.py", "x = f()  # noqa: E501"), ("src/a.py", "y = g()  # type: ignore[arg-type]"),
                           ("src/a.ts", "// @ts-ignore"), ("src/a.js", "/* eslint-disable no-console */"),
                           ("src/a.py", "if x:  # pragma: no cover"), ("src/a.py", "from x import y  # noqa: E402,F401"),
                           ("src/a.py", "z = a // b  # type: ignore"), ("src/a.rs", "#[allow(dead_code)]")):
            with self.subTest(line=line):
                d = diff(path, f"@@ -3,0 +4,1 @@\n+{line}\n")
                self.assertEqual(kinds(d), ["suppression_added"])

    def test_text_that_only_mentions_a_suppression_is_quiet(self):
        for path, line in (("docs/a.md", "Use `# noqa` sparingly"), ("README.md", "// @ts-ignore hides type errors"),
                           ("src/a.py", "from x import y  # noqa: E402"), ("src/a.py", "x = 'a # noqa: E501'"),
                           ("src/a.py", "# we never write # noqa here"), ("src/a.js", "const s = \"// eslint-disable\";"),
                           ("src/a.py", "PAT = r\"#\\s*noqa|eslint-disable\"")):
            with self.subTest(line=line):
                d = diff(path, f"@@ -3,0 +4,1 @@\n+{line}\n")
                self.assertEqual(kinds(d), [])

    def test_removed_suppression_is_quiet(self):
        d = diff("src/a.py", "@@ -3,1 +2,0 @@\n-x = f()  # noqa\n")
        self.assertEqual(kinds(d), [])


class Stubs(unittest.TestCase):
    def test_hard_stubs_are_reported_anywhere(self):
        for line in ("    raise NotImplementedError", "    raise NotImplementedError('later')", "    todo!();", "    unimplemented!()",
                     "    throw new Error('not implemented');"):
            with self.subTest(line=line):
                d = diff("src/a.py", f"@@ -3,0 +4,1 @@\n+{line}\n")
                self.assertEqual(kinds(d), ["stub_body_added"])

    def test_soft_stubs_alone_are_everyday_code(self):
        for line in ("    pass", "    ...", "    return None", "    # TODO implement"):
            with self.subTest(line=line):
                d = diff("src/a.py", f"@@ -3,0 +4,1 @@\n+{line}\n")
                self.assertEqual(kinds(d), [])

    def test_except_pass_and_early_return_are_quiet(self):
        d = added("src/a.py", "    try:", "        run()", "    except OSError:", "        pass", "    if x is None:", "        return None")
        self.assertEqual(kinds(d), [])

    def test_new_function_with_a_stub_body(self):
        d = added("src/a.py", "def feature(x):", "    pass")
        self.assertEqual(kinds(d), ["stub_body_added"])
        d = added("src/a.js", "function feature(x) {", "  // TODO", "}")
        self.assertEqual(kinds(d), ["stub_body_added"])

    def test_real_code_replaced_by_a_stub(self):
        d = diff("src/a.py", "@@ -3,2 +3,1 @@\n-    value = compute()\n-    return value\n+    pass\n")
        got = ws.scan_diff(d)
        self.assertEqual([s["kind"] for s in got], ["stub_body_added"])
        self.assertIn("replaced", got[0]["snippet"])

    def test_emptied_function_is_one_signal(self):
        d = diff("src/a.py", "@@ -3,3 +3,2 @@\n-def f():\n-    a = 1\n-    return a\n+def f():\n+    pass\n")
        self.assertEqual(kinds(d), ["stub_body_added"])

    def test_stub_in_test_file_is_not_flagged(self):
        d = diff("tests/test_a.py", "@@ -3,0 +4,1 @@\n+        pass\n")
        self.assertEqual(kinds(d), [])
        d = diff("tests/test_a.py", "@@ -3,0 +4,1 @@\n+        raise NotImplementedError\n")
        self.assertEqual(kinds(d), [])

    def test_real_code_is_quiet(self):
        d = diff("src/a.py", "@@ -3,0 +4,2 @@\n+    value = compute()\n+    return value\n")
        self.assertEqual(kinds(d), [])

    def test_non_code_file_is_quiet(self):
        d = diff("docs/a.md", "@@ -3,0 +4,1 @@\n+    pass\n")
        self.assertEqual(kinds(d), [])


class Config(unittest.TestCase):
    def test_coverage_threshold_lowered(self):
        d = diff("pyproject.toml", "@@ -9,1 +9,1 @@\n-fail_under = 90\n+fail_under = 70\n")
        self.assertEqual(kinds(d), ["config_threshold_lowered"])

    def test_coverage_threshold_raised_is_quiet(self):
        d = diff("pyproject.toml", "@@ -9,1 +9,1 @@\n-fail_under = 70\n+fail_under = 90\n")
        self.assertEqual(kinds(d), [])

    def test_coverage_threshold_removed(self):
        d = diff("setup.cfg", "@@ -9,1 +8,0 @@\n-addopts = --cov-fail-under=80\n")
        self.assertEqual(kinds(d), ["config_threshold_removed"])

    def test_strict_turned_off(self):
        d = diff("tsconfig.json", '@@ -3,1 +3,1 @@\n-    "strict": true,\n+    "strict": false,\n')
        self.assertEqual(kinds(d), ["config_loosened"])

    def test_mypy_untyped_allowed(self):
        d = diff("mypy.ini", "@@ -3,1 +3,1 @@\n-disallow_untyped_defs = True\n+disallow_untyped_defs = False\n")
        self.assertIn("config_loosened", kinds(d))

    def test_eslint_rule_off(self):
        d = diff(".eslintrc.json", '@@ -3,1 +3,1 @@\n-    "no-unused-vars": "error",\n+    "no-unused-vars": "off",\n')
        self.assertEqual(kinds(d), ["config_loosened"])

    def test_ci_continue_on_error(self):
        d = diff(".github/workflows/ci.yml", "@@ -3,0 +4,1 @@\n+        continue-on-error: true\n")
        self.assertEqual(kinds(d), ["config_loosened"])

    def test_coverage_threshold_block_removed(self):
        d = diff("package.json", '@@ -5,5 +4,0 @@\n-  "coverageThreshold": {\n-    "global": {\n-      "branches": 80,\n-      "lines": 80\n-    }\n')
        self.assertEqual(kinds(d), ["config_threshold_removed"])

    def test_strict_line_removed(self):
        d = diff("tsconfig.json", '@@ -3,1 +2,0 @@\n-    "strict": true,\n')
        self.assertEqual(kinds(d), ["config_check_removed"])

    def test_strict_line_moved_is_quiet(self):
        d = diff("tsconfig.json", '@@ -3,1 +2,0 @@\n-    "strict": true,\n@@ -9,0 +9,1 @@\n+    "strict": true,\n')
        self.assertEqual(kinds(d), [])

    def test_a_limit_raised_weakens_but_lowered_tightens(self):
        d = diff(".pylintrc", "@@ -4,1 +4,1 @@\n-max-statements=50\n+max-statements=80\n")
        self.assertEqual(kinds(d), ["config_limit_raised"])
        d = diff(".pylintrc", "@@ -4,1 +4,1 @@\n-max-statements=50\n+max-statements=40\n")
        self.assertEqual(kinds(d), [])

    def test_ci_test_step_removed(self):
        d = diff(".github/workflows/ci.yml", "@@ -9,1 +8,0 @@\n-      - run: pytest -q\n")
        self.assertEqual(kinds(d), ["config_check_removed"])
        d = diff(".github/workflows/ci.yml", "@@ -9,1 +9,1 @@\n-      - run: pytest -q\n+      - run: pytest\n")
        self.assertEqual(kinds(d), [])

    def test_tightening_is_quiet(self):
        d = diff("tsconfig.json", '@@ -3,1 +3,1 @@\n-    "strict": false,\n+    "strict": true,\n')
        self.assertEqual(kinds(d), [])

    def test_same_setting_in_other_file_is_ignored(self):
        d = diff("src/a.py", "@@ -9,1 +9,1 @@\n-fail_under = 90\n+fail_under = 70\n")
        self.assertEqual(kinds(d), [])


class Parser(unittest.TestCase):
    def test_content_looking_like_a_header_is_content(self):
        d = diff("tests/test_a.py", "@@ -1,1 +1,1 @@\n--- a/not_a_header\n+++ b/not_a_header\n")
        self.assertEqual([f["path"] for f in ws.parse_diff(d)], ["tests/test_a.py"])

    def test_empty_and_garbage_input(self):
        self.assertEqual(ws.scan_diff(""), [])
        self.assertEqual(ws.scan_diff("not a diff at all\n"), [])

    def test_snippet_is_trimmed(self):
        d = diff("src/a.py", "@@ -3,0 +4,1 @@\n+x = f()  # noqa " + "y" * 400 + "\n")
        self.assertLessEqual(len(ws.scan_diff(d)[0]["snippet"]), ws.SNIPPET_MAX)

    def test_untracked_file_diff_is_scanned(self):
        d = ws.added_file_diff("tests/test_new.py", "import pytest\n@pytest.mark.skip\ndef test_a():\n    pass\n")
        self.assertEqual(kinds(d), ["skip_marker_added"])

    def test_is_test_path(self):
        for p in ("tests/a.py", "src/test_a.py", "a_test.go", "web/a.spec.ts", "src/__tests__/a.js", "tests\\a.py"):
            self.assertTrue(ws.is_test_path(p), p)
        for p in ("src/contest.py", "src/a.py", "docs/testing.md", "docs/specs/api.md", "tests/data.json", "e2e/README.md",
                  "docs/test_plan.md"):
            self.assertFalse(ws.is_test_path(p), p)

    def test_clean_everyday_diff_gives_no_signals(self):
        # negative controls of the false positives that once made the report unreadable
        d = "".join([
            added("src/a.py", "import sys", "from x import y  # noqa: E402", "    try:", "        run()", "    except OSError:", "        pass",
                  "    if x is None:", "        return None"),
            added("tests/test_a.py", "    model.fit(X, y)", "    fixture = \"@pytest.mark.skip\"", "    # pytest.skip(...) is only described"),
            added("docs/a.md", "Use `# noqa` sparingly", "    pass"),
            "diff --git a/docs/specs/api.md b/docs/specs/api.md\ndeleted file mode 100644\n--- a/docs/specs/api.md\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-text\n",
        ])
        self.assertEqual(kinds(d), [])

    def test_the_detector_and_its_tests_are_quiet(self):
        # both files are full of marker words inside strings and regexes; none of them is a signal
        for path, name in ((SCRIPT, "tools/weaken_scan.py"), (Path(__file__), "tests/test_120_weaken_scan.py")):
            with self.subTest(path=name):
                d = ws.added_file_diff(name, path.read_text(encoding="utf-8"))
                self.assertEqual(ws.scan_diff(d), [])

    def test_a_renamed_test_keeps_its_old_path_in_mind(self):
        d = ("diff --git a/tests/test_a.py b/docs/old_a.py\nsimilarity index 80%\nrename from tests/test_a.py\nrename to docs/old_a.py\n"
             "--- a/tests/test_a.py\n+++ b/docs/old_a.py\n@@ -3,0 +4,1 @@\n+@pytest.mark.skip\n")
        self.assertEqual(kinds(d), ["skip_marker_added"])


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd), check=True, capture_output=True)


SKIPPED_TEST = ("import unittest\n\n\nclass T(unittest.TestCase):\n    @unittest.skip('x')\n    def test_a(self):\n"
                "        self.assertEqual(1, 1)\n")
ANSWERS = "01_PROJECT_CORE/circle/answers"


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q")
        self.write("forge.json", "{}\n")  # a Forge project, so --order finds this folder and not the plugin's own project
        self.write("tests/test_a.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, 1)\n")
        self.write("tests/test_c.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_c(self):\n        self.assertEqual(2, 2)\n")
        self.write("src.py", "def f():\n    return 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        f = self.repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")

    def run_cli(self, *args: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([PY, str(SCRIPT), *args], cwd=str(cwd or self.repo), capture_output=True, text=True, encoding="utf-8",
                              env={**os.environ, **(env or {})})

    def report(self, *args: str, **kw) -> dict:
        r = self.run_cli(*args, "--json", **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_clean_tree_reports_nothing_and_exits_zero(self):
        rep = self.report()
        self.assertEqual(rep["count"], 0)
        self.assertIsNone(rep["note"])

    def test_weakened_tree_is_reported_exit_zero(self):
        self.write("tests/test_a.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    @unittest.skip('x')\n    def test_a(self):\n        self.assertTrue(1)\n")
        self.write("src.py", "def f():\n    raise NotImplementedError\n")
        rep = self.report()
        self.assertTrue(rep["report_only"])
        for k in ("skip_marker_added", "assertion_loosened", "stub_body_added"):
            self.assertIn(k, rep["by_kind"])
        for s in rep["signals"]:
            self.assertEqual(set(s), {"file", "line", "kind", "snippet", "side"})

    def test_untracked_test_with_skip(self):
        self.write("tests/test_b.py", "import pytest\n@pytest.mark.xfail\ndef test_b():\n    assert False\n")
        self.assertIn("skip_marker_added", self.report()["by_kind"])

    def test_untracked_binary_file_is_not_read_as_text(self):
        (self.repo / "tests" / "test_bin.py").write_bytes(b"\x00\x01@pytest.mark.skip\n")
        self.assertEqual(self.report()["count"], 0)

    def test_text_output(self):
        (self.repo / "tests" / "test_a.py").unlink()
        r = self.run_cli()
        self.assertEqual(r.returncode, 0)
        self.assertIn("[test_file_deleted]", r.stdout)
        self.assertIn("tests/test_a.py:1", r.stdout)

    def test_renamed_test_is_not_a_deleted_test(self):
        git(self.repo, "mv", "tests/test_a.py", "tests/test_b.py")
        self.assertEqual(self.report()["count"], 0)

    def test_base_ref(self):
        self.write("src.py", "def f():\n    pass\n")
        git(self.repo, "commit", "-qam", "stub")
        self.assertEqual(self.report()["count"], 0)
        self.assertIn("stub_body_added", self.report("--base", "HEAD~1")["by_kind"])

    def test_bad_base_still_exits_zero(self):
        rep = self.report("--base", "no-such-ref")
        self.assertTrue(rep["note"])
        self.assertEqual(rep["count"], 0)

    def test_base_that_looks_like_an_option_is_refused_with_a_note(self):
        rep = self.report("--base=--output=x.txt")
        self.assertIn("not a git ref", rep["note"])
        self.assertFalse((self.repo / "x.txt").exists())

    def test_base_named_like_a_file(self):
        git(self.repo, "branch", "topic")
        self.write("topic", "a file with the name of the branch\n")
        self.write("tests/test_a.py", SKIPPED_TEST)
        rep = self.report("--base", "topic")
        self.assertIn("skip_marker_added", rep["by_kind"])

    def test_owner_git_config_cannot_hide_tracked_files(self):
        # diff.noprefix / diff.mnemonicPrefix change the a/ b/ prefixes the parser reads; the scan must not depend on them
        self.write("tests/test_a.py", SKIPPED_TEST)
        for key in ("diff.noprefix", "diff.mnemonicPrefix"):
            with self.subTest(key=key):
                git(self.repo, "config", key, "true")
                rep = self.report()
                self.assertIn("skip_marker_added", rep["by_kind"])
                self.assertEqual({s["file"] for s in rep["signals"]}, {"tests/test_a.py"})
                git(self.repo, "config", "--unset", key)

    def test_unparsed_diff_is_not_a_clean_report(self):
        def fake(args, cwd, literal=False):
            return (0, str(self.repo)) if args[0] == "rev-parse" else (0, "some unexpected output\nfrom git\n") if args[0] == "diff" else (0, "")
        with mock.patch.object(ws, "_git", fake):
            text, note = ws.collect_diff(self.repo, "HEAD", None)
        self.assertEqual(text, "some unexpected output\nfrom git\n")
        self.assertIn("not parsed", note)

    def test_outside_a_repository_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            ceiling = str(Path(d).resolve().parent)  # a temp folder inside a repository must not be searched upwards
            r = subprocess.run([PY, str(SCRIPT), "--json"], cwd=d, capture_output=True, text=True, encoding="utf-8",
                               env={**os.environ, "GIT_CEILING_DIRECTORIES": ceiling})
            self.assertEqual(r.returncode, 0)
            rep = json.loads(r.stdout)
            self.assertEqual(rep["count"], 0)
            self.assertIn("not a git repository", rep["note"])

    def test_subfolder_reports_the_whole_repository_with_root_relative_names(self):
        self.write("tests/test_a.py", SKIPPED_TEST)
        self.write("sub/test_y.py", "import pytest\n@pytest.mark.skip\ndef test_y():\n    pass\n")
        rep = self.report(cwd=self.repo / "sub")
        self.assertEqual({s["file"] for s in rep["signals"]}, {"tests/test_a.py", "sub/test_y.py"})

    def test_files_option_limits_the_scan(self):
        self.write("tests/test_a.py", SKIPPED_TEST)
        self.write("tests/test_c.py", SKIPPED_TEST)
        rep = self.report("--files", "tests/test_c.py")
        self.assertEqual({s["file"] for s in rep["signals"]}, {"tests/test_c.py"})

    def test_a_crash_inside_the_scan_still_exits_zero_and_says_so(self):
        out = io.StringIO()
        with mock.patch.object(ws, "scan", side_effect=RuntimeError("boom")), contextlib.redirect_stdout(out):
            self.assertEqual(ws.main(["--json"]), 0)
        self.assertIn("scan failed: RuntimeError: boom", json.loads(out.getvalue())["note"])

    # --order: the declared files of circle/answers/<id>.json
    def declare(self, order: str, content) -> None:
        self.write(f"{ANSWERS}/{order}.json", content if isinstance(content, str) else json.dumps(content))

    def test_order_scans_only_the_declared_files(self):
        self.write("tests/test_a.py", SKIPPED_TEST)
        self.write("tests/test_c.py", SKIPPED_TEST)  # changed too, but not declared
        self.declare("N001", {"files_changed": ["tests/test_a.py"]})
        rep = self.report("--order", "N001")
        self.assertEqual({s["file"] for s in rep["signals"]}, {"tests/test_a.py"})
        self.assertEqual(rep["scope"], "order N001")
        self.assertIsNone(rep["note"])

    def test_order_without_an_answer_yet_scans_the_whole_tree_and_says_so(self):
        # forge-done runs the scan BEFORE `order.py done` writes the answer: that must not look clean
        self.write("tests/test_a.py", SKIPPED_TEST)
        rep = self.report("--order", "N999")
        self.assertIn("skip_marker_added", rep["by_kind"])
        self.assertIn("no answers/N999.json yet", rep["note"])
        self.assertIn("whole working tree", rep["note"])

    def test_order_with_a_broken_answer_scans_the_whole_tree_and_says_so(self):
        self.write("tests/test_a.py", SKIPPED_TEST)
        for name, content in (("list", "[]"), ("string", '{"files_changed": "tests/test_a.py"}'), ("garbage", "{not json"),
                              ("empty", '{"files_changed": []}')):
            with self.subTest(name):
                self.declare("N002", content)
                rep = self.report("--order", "N002")
                self.assertIn("skip_marker_added", rep["by_kind"])
                self.assertIn("whole working tree", rep["note"])


if __name__ == "__main__":
    unittest.main()
