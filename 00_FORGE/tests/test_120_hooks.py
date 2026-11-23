"""1.2.0 (N031): hook fixes. check_file stays silent outside a Forge project; a `warn` write guard does not block (exit 0 +
additionalContext) while `block` still exits 2."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import GATES, PY, TOOLS, Project  # noqa: E402


def hook(path: Path, cwd: Path) -> subprocess.CompletedProcess:
    data = {"tool_name": "Write", "tool_input": {"file_path": str(path)}}
    return subprocess.run([PY, str(GATES / "check_file.py"), "--hook"], input=json.dumps(data), capture_output=True, text=True,
                          encoding="utf-8", cwd=cwd)


class CheckFileOutsideProject(unittest.TestCase):
    def test_silent_outside_a_forge_project(self) -> None:
        d = Path(tempfile.mkdtemp(prefix="forge_no_project_"))
        f = d / "x.md"; f.write_bytes(b"\xef\xbb\xbfa\r\nb\r\n")
        r = hook(f, d)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_still_checks_inside_a_project(self) -> None:
        p = Project()
        f = p.docs / "x.md"; f.write_bytes(b"a\r\nb\r\n")
        r = hook(f, p.dir)
        self.assertEqual(r.returncode, 2); self.assertIn("CR", r.stderr)
        r = hook(f, Path(tempfile.mkdtemp(prefix="forge_cwd_")))  # the project is found from the file, not from cwd
        self.assertEqual(r.returncode, 2)


class WriteGuardActions(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Project()

    def add(self, action: str) -> None:
        subprocess.run([PY, str(TOOLS / "guards.py"), "add", "--on", "write", "--pattern", "Patreon", "--action", action,
                        "--message", "left Patreon", "--lesson", "L007", "--by", "test"], cwd=self.p.dir, check=True,
                       capture_output=True, text=True, encoding="utf-8")

    def test_warn_does_not_block(self) -> None:
        self.add("warn")
        f = self.p.dir / "n.md"; f.write_text("via Patreon\n", encoding="utf-8", newline="\n")
        r = hook(f, self.p.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse"); self.assertIn("lesson L007", out["additionalContext"])
        f.write_text("via the site\n", encoding="utf-8", newline="\n")  # negative control
        r = hook(f, self.p.dir)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_block_still_exits_2(self) -> None:
        self.add("block")
        f = self.p.dir / "n.md"; f.write_text("via Patreon\n", encoding="utf-8", newline="\n")
        r = hook(f, self.p.dir)
        self.assertEqual(r.returncode, 2); self.assertIn("BLOCK", r.stderr); self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
