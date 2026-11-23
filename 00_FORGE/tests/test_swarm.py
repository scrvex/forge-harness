"""0.7.0 swarm + machine tests: probe never crashes, acceptance goes red on a DoD breach, green passes, apply is atomic."""
import json
import pathlib
import subprocess
import sys
import unittest

PLUGIN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_kernel import Project  # noqa: E402  (1.1.0: a throw-away project, never the dev workspace - tests run in any checkout)

ROOT = STATE = TMP = None  # set per test from a throw-away project (setUp)


def run(args, cwd):
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


class TestMachine(unittest.TestCase):
    def test_probe_and_tune(self):
        sys.path.insert(0, str(PLUGIN / "tools"))
        import machine
        cap = machine.probe()
        self.assertGreaterEqual(cap["cpu_threads"], 1)
        self.assertGreaterEqual(cap["cpu_cores"], 1)
        t = machine.tune(cap)
        self.assertIn("slots", t); self.assertLessEqual(t["slots"]["claude_subagents"], 2)
        self.assertIsNone(t["num_thread"])  # never set on GPU (bench 2026-09-20)
        self.assertIn("machine:", machine.line({"capacity": cap, "tuned": t}))

    def test_tune_no_gpu(self):
        sys.path.insert(0, str(PLUGIN / "tools"))
        import machine
        t = machine.tune({"cpu_cores": 4, "cpu_threads": 8, "gpu": None, "ollama": {"available": False}})
        self.assertEqual(t["slots"]["local_heavy"], 0)  # no VRAM -> local tier OFF, never CPU-offload
        self.assertEqual(t["local_models"], {})


class TestAccept(unittest.TestCase):
    def setUp(self):
        global ROOT, STATE, TMP
        self.p = Project()
        ROOT, STATE = self.p.dir, self.p.state
        TMP = STATE / "tmp_test_swarm"
        (TMP / "stage" / "x").mkdir(parents=True, exist_ok=True)
        (TMP / "stage" / "x" / "a.md").write_bytes("ok line with 7-day trial\n".encode("utf-8"))
        self.manifest = TMP / "plan.json"
        self.out = (TMP / "out" / "a.md").relative_to(ROOT).as_posix()
        self.manifest.write_text(json.dumps({"cycle": "TESTSWARM", "checks": [], "files": [
            {"path": "x/a.md", "tier": "LOCAL", "dod": ["must contain: 7-day trial", "no: Patreon", "human line for the reviewer"]}]}),
            encoding="utf-8")

    def tearDown(self):
        self.p.cleanup()

    def accept(self, extra=()):
        return run([str(PLUGIN / "tools" / "swarm_accept.py"), str(self.manifest), "--staging", str(TMP / "stage"), *extra], cwd=ROOT)

    def test_green_then_red_then_apply(self):
        r = self.accept()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("for the reviewer", r.stdout)  # human DoD line routed to the one reviewer pass
        (TMP / "stage" / "x" / "a.md").write_bytes("mentions Patreon\n".encode("utf-8"))
        r = self.accept()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no: Patreon", r.stdout)
        self.assertNotIn("applied", self.accept(["--apply"]).stdout.replace("NOT applied", ""))  # red never applies
        (TMP / "stage" / "x" / "a.md").write_bytes("ok line with 7-day trial\n".encode("utf-8"))
        # apply into a path under state/tmp (kept out of the real tree)
        self.manifest.write_text(json.dumps({"cycle": "TESTSWARM", "checks": [], "files": [
            {"path": self.out, "tier": "LOCAL", "dod": ["must contain: 7-day trial"]}]}), encoding="utf-8")
        (TMP / "stage" / self.out).parent.mkdir(parents=True, exist_ok=True)
        (TMP / "stage" / self.out).write_bytes("ok line with 7-day trial\n".encode("utf-8"))
        r = self.accept(["--apply", "--keep-staging"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((ROOT / self.out).is_file())
        rep = json.loads((STATE / "swarm" / "TESTSWARM_accept.json").read_text(encoding="utf-8"))
        self.assertTrue(rep["applied"]); self.assertIn("sha256", rep["files"][0])

    def test_path_traversal_rejected(self):
        self.manifest.write_text(json.dumps({"cycle": "TESTSWARM", "checks": [], "files": [
            {"path": "..\\..\\..\\..\\evil.md", "tier": "LOCAL", "dod": []}]}), encoding="utf-8")
        r = self.accept(["--apply"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("escapes staging/root", r.stdout)
        self.assertFalse((ROOT.parent / "evil.md").exists())
        self.manifest.write_text(json.dumps({"cycle": "TESTSWARM", "checks": [], "files": [
            {"path": "../../../../evil.md", "tier": "LOCAL", "dod": []}]}), encoding="utf-8")
        r = self.accept(["--apply"])  # the posix spelling is refused too
        self.assertEqual(r.returncode, 1)
        self.assertIn("escapes staging/root", r.stdout)


class TestDecide(unittest.TestCase):
    def d(self, **kw):
        sys.path.insert(0, str(PLUGIN / "tools"))
        import swarm_probe
        kw.setdefault("tuned", {"slots": {"claude_subagents": 2, "local_heavy": 1, "scripts": 4}})
        return swarm_probe.decide(**kw)

    def test_single_below_breakeven(self):
        self.assertFalse(self.d(files=1)["swarm"])
        self.assertFalse(self.d(files=2)["swarm"])

    def test_pipeline_at_three_dependent(self):
        r = self.d(files=3, dependent=True)
        self.assertTrue(r["swarm"]); self.assertEqual(r["mode"], "pipeline")

    def test_parallel_and_caps(self):
        self.assertEqual(self.d(files=6, dependent=False)["mode"], "pipeline+parallel")
        r = self.d(files=6, tuned={"slots": {"claude_subagents": 0, "local_heavy": 0}})
        self.assertFalse(r["swarm"])  # no slots on this machine -> the Lead stays single

    def test_bulk_prefers_local(self):
        r = self.d(files=1, bulk=True)
        self.assertEqual(r["mode"], "map-reduce")

    def test_bulk_queues_when_slot_busy(self):
        r = self.d(files=1, bulk=True, tuned={"slots": {"claude_subagents": 2, "local_heavy": 0}})
        self.assertTrue(r["swarm"]); self.assertIn("queued", r["mode"])


if __name__ == "__main__":
    unittest.main()
