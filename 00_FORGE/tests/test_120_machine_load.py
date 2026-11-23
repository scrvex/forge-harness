"""Live machine load (K28): parsers on the real tool formats, verdict thresholds, laptop cap, snapshot robustness, Windows glue
logic, the codex wave cap and the Ollama refusals (local_exec, local_jobs)."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import machine  # noqa: E402
import codex_exec  # noqa: E402
import local_exec  # noqa: E402

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def snap(**kw):
    s = {"os": "darwin", "cpus": 10, "cpu_ratio": 0.1, "cpu_pct": None, "mem_free_pct": 70.0, "swap_growing": False,
         "throttled": False, "cpu_speed_limit": None, "thermal_pressure": None, "thermal_c": None, "gpu_pct": 5.0,
         "battery": False, "power_source": None}
    s.update(kw)
    return s


class Parsers(unittest.TestCase):
    def test_macos(self):
        self.assertEqual(machine.parse_memory_pressure("The system has 36 GB\nSystem-wide memory free percentage: 63%\n"), 63.0)
        self.assertEqual(machine.parse_vm_stat_swapouts("Pages free: 5.\nSwapins:  3.\nSwapouts:  1234.\n"), 1234)
        self.assertEqual(machine.parse_vm_stat_page_size("Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"), 4096)
        self.assertEqual(machine.parse_vm_stat_page_size(""), 16384)
        self.assertEqual(machine.parse_ioreg_gpu('| "PerformanceStatistics" = {"Device Utilization %"=12,"x"=1}\n"Device Utilization %"=40'), 40.0)
        self.assertIsNone(machine.parse_ioreg_gpu("nothing"))
        b = machine.parse_pmset_batt("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t87%; discharging; 5:00 remaining")
        self.assertTrue(b["battery"]); self.assertEqual(b["source"], "Battery Power"); self.assertEqual(b["battery_pct"], 87)
        self.assertFalse(machine.parse_pmset_batt("Now drawing from 'AC Power'\n")["battery"])

    def test_pmset_therm_real_formats(self):
        # literal strings of /usr/bin/pmset (strings(1)), not invented ones
        calm = "Note: No thermal warning level has been recorded\nNote: No performance warning level has been recorded\nNote: No CPU power status has been recorded\n"
        self.assertFalse(machine.parse_pmset_therm(calm)["throttled"])
        normal = machine.parse_pmset_therm("Thermal Warning Level = 0\nPerformance Warning Level = 0")
        self.assertFalse(normal["throttled"]); self.assertEqual(normal["thermal_warning_level"], 0)
        hot = machine.parse_pmset_therm("Thermal Warning Level = 100\nPerformance Warning Level = 0")
        self.assertTrue(hot["throttled"]); self.assertEqual(hot["thermal_warning_level"], 100)
        self.assertTrue(machine.parse_pmset_therm("Thermal Warning Level = 5")["throttled"])
        err = machine.parse_pmset_therm("Error:Failed to get thermal warning level with error code 0xe00002c2")
        self.assertFalse(err["throttled"]); self.assertIsNone(err["thermal_warning_level"])
        self.assertFalse(machine.parse_pmset_therm("Error: Failed to get performance warning level with error code 0xe00002c2")["throttled"])
        self.assertFalse(machine.parse_pmset_therm("Thermal Warning Level = 255")["throttled"])      # kIOPMThermalLevelUnknown
        self.assertFalse(machine.parse_pmset_therm("Performance Warning Level = 100")["throttled"])  # scale not checked: not used
        self.assertFalse(machine.parse_pmset_therm("")["throttled"])
        th = machine.parse_pmset_therm("CPU_Scheduler_Limit = 100\nCPU_Available_CPUs = 8\nCPU_Speed_Limit = 70")
        self.assertTrue(th["throttled"]); self.assertEqual(th["cpu_speed_limit"], 70)
        self.assertFalse(machine.parse_pmset_therm("CPU_Speed_Limit = 100")["throttled"])

    def test_thermal_pressure(self):
        self.assertEqual(machine.parse_thermal_pressure("com.apple.system.thermalpressurelevel 0"), 0)
        self.assertEqual(machine.parse_thermal_pressure("com.apple.system.thermalpressurelevel 1\n"), 1)
        self.assertEqual(machine.parse_thermal_pressure("com.apple.system.thermalpressurelevel 2"), 2)
        self.assertIsNone(machine.parse_thermal_pressure(""))
        self.assertIsNone(machine.parse_thermal_pressure("\n\n"))
        self.assertIsNone(machine.parse_thermal_pressure("notifyutil: command not found"))

    def test_linux(self):
        self.assertEqual(machine.parse_meminfo_free_pct("MemTotal:  16000000 kB\nMemFree: 1 kB\nMemAvailable:   4000000 kB\n"), 25.0)
        self.assertIsNone(machine.parse_meminfo_free_pct(""))
        self.assertEqual(machine.parse_nvidia_util("12\n87\n"), 87.0)

    def test_windows(self):
        self.assertIsNone(machine.cpu_ratio_from_times(None, (1, 2, 3)))
        self.assertIsNone(machine.cpu_ratio_from_times((1, 2, 3), (1, 2, 3)))          # no time passed: unknown, not 0
        self.assertEqual(machine.cpu_ratio_from_times((0, 0, 0), (50, 100, 100)), 0.75)  # kernel time includes idle
        self.assertEqual(machine.cpu_ratio_from_times((0, 0, 0), (200, 100, 100)), 0.0)  # clamped
        self.assertEqual(machine.win_power((1, 128)), {"battery": False, "source": "AC Power"})   # 128 = no system battery
        self.assertEqual(machine.win_power((0, 1)), {"battery": True, "source": "Battery Power"})
        self.assertTrue(machine.win_power((1, 9))["battery"])                                      # charging
        self.assertFalse(machine.win_power((255, 255))["battery"])                                 # unknown is not a laptop
        self.assertFalse(machine.win_power(None)["battery"])


class Verdict(unittest.TestCase):
    def test_ok(self):
        v = machine.verdict(snap(), 3)
        self.assertEqual((v["verdict"], v["slots"], v["reasons"], v["unknown"]), ("ok", 3, [], []))

    def test_busy_reports_slots_stay(self):
        v = machine.verdict(snap(cpu_ratio=0.7), 3)
        self.assertEqual((v["verdict"], v["slots"]), ("busy", 3))

    def test_hot(self):
        for kw in ({"cpu_ratio": 0.9}, {"mem_free_pct": 15.0}, {"thermal_pressure": 1}, {"thermal_c": 96.0}):
            v = machine.verdict(snap(**kw), 3)
            self.assertEqual((v["verdict"], v["slots"]), ("hot", 1), kw)
            self.assertTrue(v["reasons"])

    def test_gpu_is_reported_but_does_not_cut_slots(self):
        # K28: CPU scripts and the Codex wave run in parallel with GPU model runs
        v = machine.verdict(snap(gpu_pct=90.0), 3)
        self.assertEqual((v["verdict"], v["slots"]), ("busy", 3))
        self.assertIn("GPU 90%", v["reasons"])

    def test_critical(self):
        for kw in ({"throttled": True, "cpu_speed_limit": 60}, {"mem_free_pct": 8.0}, {"swap_growing": True},
                   {"thermal_pressure": 2}, {"thermal_pressure": 3}, {"thermal_pressure": 4}):
            v = machine.verdict(snap(**kw), 3)
            self.assertEqual((v["verdict"], v["slots"]), ("critical", 0), kw)

    def test_thermal_pressure_levels(self):
        self.assertEqual(machine.verdict(snap(thermal_pressure=0), 3)["verdict"], "ok")
        self.assertEqual(machine.verdict(snap(thermal_pressure=None), 3)["verdict"], "ok")
        self.assertIn("thermal pressure moderate", machine.verdict(snap(thermal_pressure=1), 3)["reasons"])
        self.assertIn("thermal pressure heavy", machine.verdict(snap(thermal_pressure=2), 3)["reasons"])
        self.assertIn("thermal pressure level 9", machine.verdict(snap(thermal_pressure=9), 3)["reasons"])

    def test_boundaries_are_not_hot(self):
        self.assertEqual(machine.verdict(snap(cpu_ratio=0.8), 3)["verdict"], "busy")
        self.assertEqual(machine.verdict(snap(mem_free_pct=20.0), 3)["verdict"], "ok")
        self.assertEqual(machine.verdict(snap(thermal_c=94.0), 3)["verdict"], "ok")

    def test_laptop_cap(self):
        self.assertEqual(machine.verdict(snap(battery=True), 5)["slots"], 2)
        self.assertEqual(machine.verdict(snap(battery=True, cpu_ratio=0.7), 5)["slots"], 2)
        self.assertEqual(machine.verdict(snap(battery=True, cpu_ratio=0.9), 5)["slots"], 1)
        self.assertEqual(machine.verdict(snap(cpu_ratio=0.7), 5)["slots"], 5)

    def test_default_base_is_not_a_profile_cap(self):
        # without a count of its own the verdict must not cut a desktop to 2: only the laptop cap and the load do
        self.assertEqual(machine.verdict(snap())["slots"], machine.NO_PROFILE_CAP)
        self.assertEqual(machine.verdict(snap(battery=True))["slots"], 2)
        self.assertEqual(machine.verdict(snap(cpu_ratio=0.9))["slots"], 1)
        self.assertEqual(machine.verdict(snap(swap_growing=True))["slots"], 0)

    def test_unknown_signals_do_not_alarm(self):
        v = machine.verdict(snap(cpu_ratio=None, mem_free_pct=None, gpu_pct=None), 2)
        self.assertEqual((v["verdict"], v["unknown"]), ("ok", ["cpu"]))

    def test_line(self):
        self.assertEqual(machine.load_line(machine.verdict(snap(), 2)), "machine: ok")
        self.assertIn("memory free 8%", machine.load_line(machine.verdict(snap(mem_free_pct=8.0), 2)))
        self.assertEqual(machine.load_line(machine.verdict(snap(cpu_ratio=None), 2)), "machine: ok (cpu unknown)")

    def test_text(self):
        t = machine.load_text(machine.verdict(snap(thermal_pressure=0), machine.NO_PROFILE_CAP))
        self.assertIn("not limited by the load", t); self.assertIn("pressure nominal", t)
        self.assertIn("slots for heavy parallel work: 1", machine.load_text(machine.verdict(snap(thermal_pressure=1), machine.NO_PROFILE_CAP)))
        t = machine.load_text(machine.verdict(snap(battery=True, thermal_pressure=0), machine.NO_PROFILE_CAP))
        self.assertIn("slots for heavy parallel work: 2 (laptop", t); self.assertIn("pressure nominal", t)


class Sampling(unittest.TestCase):
    def test_nothing_readable_does_not_raise(self):
        # every command missing, loadavg failing, no nvidia-smi: all fields unknown and the verdict is ok
        for plat in ("darwin", "linux", "win32"):
            with mock.patch.object(machine.sys, "platform", plat), mock.patch.object(machine, "_run", return_value=""), \
                    mock.patch.object(machine.os, "getloadavg", side_effect=OSError), mock.patch.object(machine.shutil, "which", return_value=None), \
                    mock.patch.object(machine, "_win_system_times", return_value=None), mock.patch.object(machine, "_win_mem_free_pct", return_value=None), \
                    mock.patch.object(machine, "_win_power_status", return_value=None):
                s = machine.sample()
            self.assertIsNone(s["cpu_ratio"], plat); self.assertFalse(s["throttled"], plat)
            v = machine.verdict(s)
            self.assertEqual((v["verdict"], v["unknown"]), ("ok", ["cpu"]), plat)

    def test_a_crashing_probe_leaves_the_others(self):
        with mock.patch.object(machine.sys, "platform", "darwin"), mock.patch.object(machine, "_run", side_effect=RuntimeError("boom")), \
                mock.patch.object(machine.shutil, "which", return_value=None):
            s = machine.sample()
        self.assertEqual(machine.verdict(s)["verdict"], "ok")

    def test_load_never_raises(self):
        with mock.patch.object(machine, "sample", side_effect=RuntimeError("boom")):
            v = machine.load()
        self.assertEqual((v["verdict"], v["unknown"]), ("ok", ["cpu"]))

    def test_macos_reads_thermal_pressure_and_pmset(self):
        outputs = {"notifyutil": "com.apple.system.thermalpressurelevel 2\n", "pmset": "Note: No thermal warning level has been recorded\n",
                   "vm_stat": "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nSwapouts:  10.\n", "memory_pressure": "System-wide memory free percentage: 50%"}
        with mock.patch.object(machine.sys, "platform", "darwin"), mock.patch.object(machine, "_run", side_effect=lambda cmd, t=10: outputs.get(cmd[0], "")), \
                mock.patch.object(machine, "_swap_growing", return_value=False), mock.patch.object(machine.shutil, "which", return_value=None):
            s = machine.sample()
        self.assertEqual(s["thermal_pressure"], 2); self.assertFalse(s["throttled"]); self.assertEqual(s["mem_free_pct"], 50.0)
        self.assertEqual(machine.verdict(s)["verdict"], "critical")

    def test_windows_without_powershell(self):
        with mock.patch.object(machine.sys, "platform", "win32"), mock.patch.object(machine, "_run", return_value="") as run, \
                mock.patch.object(machine.time, "sleep"), mock.patch.object(machine.shutil, "which", return_value=None), \
                mock.patch.object(machine, "_win_system_times", side_effect=[(0, 0, 0), (50, 100, 100)]), \
                mock.patch.object(machine, "_win_mem_free_pct", return_value=55.0), mock.patch.object(machine, "_win_power_status", return_value=(0, 1)):
            s = machine.sample()
        run.assert_not_called()
        self.assertEqual((s["cpu_ratio"], s["cpu_pct"], s["mem_free_pct"], s["battery"], s["power_source"]), (0.75, 75.0, 55.0, True, "Battery Power"))
        v = machine.verdict(s)
        self.assertEqual((v["verdict"], v["slots"], v["laptop"]), ("busy", 2, True))


class SwapGrowing(unittest.TestCase):
    PAGES = 256 * 2**20 // 16384  # SWAP_GROWTH_MB in 16 KB pages

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.f = Path(self._tmp.name) / "swap.json"

    def write(self, t, n):
        self.f.write_text(json.dumps({"t": t, "n": n}), encoding="utf-8")

    def test_first_call_is_not_growth_and_leaves_a_snapshot(self):
        self.assertFalse(machine._swap_growing(1000, state=self.f))
        self.assertEqual(json.loads(self.f.read_text(encoding="utf-8"))["n"], 1000)

    def test_one_page_is_noise_a_threshold_is_not(self):
        self.write(time.time() - 60, 1000)
        self.assertFalse(machine._swap_growing(1001, state=self.f))
        self.write(time.time() - 60, 1000)
        self.assertFalse(machine._swap_growing(1000 + self.PAGES - 1, state=self.f))
        self.write(time.time() - 60, 1000)
        self.assertTrue(machine._swap_growing(1000 + self.PAGES, state=self.f))

    def test_page_size_counts(self):
        self.write(time.time() - 60, 1000)
        self.assertFalse(machine._swap_growing(1000 + self.PAGES, page_size=4096, state=self.f))  # same pages = a quarter of the bytes

    def test_stale_snapshot_and_counter_reset_are_not_growth(self):
        self.write(time.time() - machine.SWAP_WINDOW_S - 5, 1000)
        self.assertFalse(machine._swap_growing(1000 + 10 * self.PAGES, state=self.f))
        self.write(time.time() - 60, 10 * self.PAGES)          # rebooted: the counter went down
        self.assertFalse(machine._swap_growing(5, state=self.f))
        self.assertEqual(json.loads(self.f.read_text(encoding="utf-8"))["n"], 5)

    def test_calls_close_together_compare_with_the_same_baseline(self):
        t0 = time.time() - 5
        self.write(t0, 1000)
        self.assertTrue(machine._swap_growing(1000 + self.PAGES, state=self.f))
        self.assertTrue(machine._swap_growing(1000 + self.PAGES, state=self.f))   # not diluted by the previous call
        self.assertEqual(json.loads(self.f.read_text(encoding="utf-8")), {"t": t0, "n": 1000})

    def test_broken_state_does_not_raise(self):
        for raw in ("[]", '{"t": "x", "n": 1}', '{"t": 1}', "not json", "", "null", '{"t": true, "n": true}'):
            self.f.write_text(raw, encoding="utf-8")
            self.assertFalse(machine._swap_growing(5, state=self.f), raw)
        self.assertFalse(machine._swap_growing(5, state=Path(self._tmp.name) / "no_such_dir" / "swap.json"))
        self.assertFalse(machine._swap_growing(None, state=self.f))
        with mock.patch.object(machine.tempfile, "gettempdir", side_effect=OSError):
            self.assertFalse(machine._swap_growing(5))


class Wave(unittest.TestCase):
    def test_cap_lowers(self):
        n, why = codex_exec.wave_slots(3, {"slots": 1, "verdict": "hot", "reasons": ["CPU load 0.90 per core"]})
        self.assertEqual(n, 1); self.assertIn("3 -> 1", why)

    def test_no_cap(self):
        self.assertEqual(codex_exec.wave_slots(2, {"slots": 2, "verdict": "ok", "reasons": []}), (2, ""))

    def test_zero_refuses(self):
        n, why = codex_exec.wave_slots(3, {"slots": 0, "verdict": "critical", "reasons": ["swap growing"]})
        self.assertEqual(n, 0); self.assertIn("swap growing", why)

    def test_desktop_keeps_the_wave_whatever_load_is_called_with(self):
        # the wave calls machine.load() without a count: an ok desktop must keep its 3 slots, a laptop gets 2 and the reason
        with mock.patch.object(machine, "sample", return_value=snap()):
            self.assertEqual(codex_exec.wave_slots(3, machine.load()), (3, ""))
            self.assertEqual(codex_exec.wave_slots(3, machine.load(3)), (3, ""))
            self.assertEqual(codex_exec.wave_slots(5, machine.load()), (5, ""))
        with mock.patch.object(machine, "sample", return_value=snap(battery=True)):
            for v in (machine.load(), machine.load(3)):
                n, why = codex_exec.wave_slots(3, v)
                self.assertEqual(n, 2); self.assertIn("laptop without active cooling", why); self.assertIn("3 -> 2", why)

    def test_wave_on_a_loaded_machine(self):
        with mock.patch.object(machine, "sample", return_value=snap(cpu_ratio=0.9)):
            n, why = codex_exec.wave_slots(3, machine.load())
        self.assertEqual(n, 1); self.assertIn("CPU load 0.90", why)
        with mock.patch.object(machine, "sample", return_value=snap(swap_growing=True)):
            n, why = codex_exec.wave_slots(3, machine.load())
        self.assertEqual(n, 0); self.assertIn("swap growing", why)

    def test_refuse_reason(self):
        crit = machine.verdict(snap(swap_growing=True), 2)
        with mock.patch.object(machine, "load", return_value=crit):
            self.assertIn("critical", machine.refuse_reason(1))
        with mock.patch.object(machine, "load", return_value=machine.verdict(snap(), 2)):
            self.assertIsNone(machine.refuse_reason(1))
        with mock.patch.object(machine, "load", side_effect=RuntimeError):
            self.assertIsNone(machine.refuse_reason(1))
        with mock.patch.object(machine, "sample", return_value=snap(cpu_ratio=0.9)):
            self.assertIsNone(machine.refuse_reason(1))                       # hot = 1 slot: one heavy run may start
            self.assertIn("hot", machine.refuse_reason(2))


class OllamaRefusals(unittest.TestCase):
    WHY = "machine is critical (swap growing); re-check with: python machine.py load"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def load_jobs(self):
        spec = importlib.util.spec_from_file_location("local_jobs_under_test", TOOLS / "local_jobs.py")
        mod = importlib.util.module_from_spec(spec)
        with mock.patch("forge_common.find_root", return_value=self.root), mock.patch("forge_common.layout", return_value={"state": self.root / "state"}):
            spec.loader.exec_module(mod)
        return mod

    def run_jobs(self, mod, why):
        out = io.StringIO()
        with mock.patch.object(machine, "refuse_reason", return_value=why), mock.patch.object(sys, "argv", ["local_jobs.py", "classify", "task"]), \
                contextlib.redirect_stdout(out):
            rc = mod.main()
        return rc, json.loads(out.getvalue())

    def test_local_jobs_refuses_when_critical(self):
        mod = self.load_jobs()
        with mock.patch.object(mod, "available", side_effect=AssertionError("Ollama must not be asked")), \
                mock.patch.object(mod, "ask_json", side_effect=AssertionError("no model run")):
            rc, data = self.run_jobs(mod, self.WHY)
        self.assertEqual(rc, 1)
        self.assertEqual(data, {"_error": "local run refused: " + self.WHY})

    def test_local_jobs_goes_on_when_the_machine_allows(self):
        mod = self.load_jobs()
        with mock.patch.object(mod, "available", return_value=False):
            rc, data = self.run_jobs(mod, None)
        self.assertEqual(rc, 1)
        self.assertIn("Ollama not available", data["_error"])

    def make_order(self):
        circle = self.root / "circle"
        (circle / "orders").mkdir(parents=True)
        (circle / "orders" / "N900_x.md").write_text("## Goal\nwrite a note\n\n## Deliverable\n`read/N900.md`\n", encoding="utf-8")
        cfg = {"mode": "normal", "user_language": "ru", "_layout": {"circle": circle}}
        row = {"id": "N900", "state": "TAKEN", "file": "N900_x.md"}
        return circle, cfg, row

    def run_exec(self, cfg, row, why, available):
        with mock.patch.object(local_exec, "find_root", return_value=self.root), mock.patch.object(local_exec, "load_cfg", return_value=cfg), \
                mock.patch.object(local_exec, "read_index", return_value=[row]), mock.patch.object(machine, "refuse_reason", return_value=why), \
                mock.patch.object(local_exec, "available", **available), mock.patch.object(sys, "argv", ["local_exec.py", "N900"]), \
                contextlib.redirect_stdout(io.StringIO()):
            return local_exec.main()

    def test_local_exec_refuses_without_writing_an_answer(self):
        circle, cfg, row = self.make_order()
        with self.assertRaises(SystemExit) as cm:
            self.run_exec(cfg, row, self.WHY, {"side_effect": AssertionError("Ollama must not be asked")})
        self.assertEqual(str(cm.exception), "local run refused: " + self.WHY)
        self.assertFalse((circle / "answers").exists())
        self.assertFalse((self.root / "read").exists())

    def test_local_exec_goes_on_when_the_machine_allows(self):
        circle, cfg, row = self.make_order()
        rc = self.run_exec(cfg, row, None, {"return_value": False})
        self.assertEqual(rc, 1)
        answer = json.loads((circle / "answers" / "N900.json").read_text(encoding="utf-8"))
        self.assertEqual((answer["status"], answer["summary"]), ("failed", "Ollama not reachable"))


if __name__ == "__main__":
    unittest.main()
