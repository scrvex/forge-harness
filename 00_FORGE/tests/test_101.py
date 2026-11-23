"""1.0.1 tests: limit-aware delegation and real limit readers, with negative controls."""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import PLUGIN, PY, TOOLS, Project, run  # noqa: E402

sys.path.insert(0, str(TOOLS))
import limits  # noqa: E402


def reading(provider: str, used: float, week: float | None = None) -> dict:
    return {"provider": provider,
            "five_hour": {"used": used, "resets_at": "", "reset_since_reading": False},
            "seven_day": {"used": week, "resets_at": "", "reset_since_reading": False} if week is not None else None}


class LimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = {name: os.environ.get(name) for name in ("FORGE_GPT_POLICY", "CODEX_HOME", "FORGE_HOME")}
        self.p = Project()
        self.pol = self.p.tmp / "policy.json"
        self.pol.write_text(json.dumps({"deny": []}), encoding="utf-8", newline="\n")
        os.environ["FORGE_GPT_POLICY"] = str(self.pol)
        os.environ["CODEX_HOME"] = str(self.p.tmp / "codex")
        os.environ["FORGE_HOME"] = str(self.p.tmp / "forge")

    def tearDown(self) -> None:
        for name, value in self.env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.p.cleanup()

    def allow_gpt(self) -> None:
        f = self.p.dir / "forge.json"
        cfg = json.loads(f.read_text(encoding="utf-8"))
        cfg["gpt"] = "allowed"
        f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    def test_code_uses_the_side_with_more_room(self) -> None:
        self.allow_gpt()
        for claude, codex, expected in ((85, 20, "CODEX"), (20, 85, "HEAVY")):
            with self.subTest(claude=claude, codex=codex):
                r = limits.route("code", root=self.p.dir, claude=reading("claude", claude), codex=reading("codex", codex))
                self.assertEqual(r["decision"], expected)
                self.assertIn("more room", r["reason"])

    def test_close_limits_use_the_kind(self) -> None:
        self.allow_gpt()
        for kind, expected in (("code", "CODEX"), ("research", "INVESTIGATOR")):
            with self.subTest(kind=kind):
                r = limits.route(kind, root=self.p.dir, claude=reading("claude", 40), codex=reading("codex", 45))
                self.assertEqual(r["decision"], expected)
                self.assertIn("kind decides", r["reason"])
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 40), codex=reading("codex", 55))
        self.assertEqual(r["decision"], "HEAVY")
        self.assertNotIn("kind decides", r["reason"])
        r = limits.route("research", root=self.p.dir, claude=reading("claude", 55), codex=reading("codex", 40))
        self.assertEqual(r["decision"], "GPT-ADVISE")
        self.assertNotIn("kind decides", r["reason"])

    def test_critical_side_is_not_given_code_when_the_other_has_room(self) -> None:
        self.allow_gpt()
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 40), codex=reading("codex", 95))
        self.assertEqual(r["decision"], "HEAVY")
        self.assertIn("Codex limit critical", r["reason"])
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 40), codex=reading("codex", 35))
        self.assertEqual(r["decision"], "CODEX")
        self.assertNotIn("critical", r["reason"])
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 95), codex=reading("codex", 80))
        self.assertEqual(r["decision"], "CODEX")
        self.assertIn("Claude limit critical", r["reason"])
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 65), codex=reading("codex", 80))
        self.assertEqual(r["decision"], "HEAVY")
        self.assertNotIn("critical", r["reason"])

    def test_both_critical_choose_less_loaded_but_exhausted_wait(self) -> None:
        self.allow_gpt()
        for claude, codex, expected in ((93, 96, "HEAVY"), (96, 93, "CODEX")):
            with self.subTest(claude=claude, codex=codex):
                r = limits.route("code", root=self.p.dir, claude=reading("claude", claude), codex=reading("codex", codex))
                self.assertEqual(r["decision"], expected)
                self.assertIn("both limits critical", r["reason"])
                if expected == "CODEX":
                    self.assertTrue(r["how"].endswith(" - run with --force (both limits critical, Codex is the less loaded)"))
                else:
                    self.assertNotIn("--force", r["how"])
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 99.5), codex=reading("codex", 99.5))
        self.assertEqual(r["decision"], "WAIT")
        self.assertNotIn("both limits critical", r["reason"])
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 99.5), codex=reading("codex", 20))
        self.assertEqual(r["decision"], "CODEX")

    def test_closed_gpt_routes_to_claude_even_when_critical(self) -> None:
        cfg = json.loads((self.p.dir / "forge.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["gpt"], "denied")
        for used in (95, 20):
            with self.subTest(claude=used):
                r = limits.route("code", root=self.p.dir, claude=reading("claude", used), codex=reading("codex", 20))
                self.assertEqual(r["decision"], "HEAVY")
                self.assertIn("Codex is off", r["reason"])
        self.allow_gpt()
        for used in (95, 20):
            with self.subTest(claude=used, gpt="allowed"):
                r = limits.route("code", root=self.p.dir, claude=reading("claude", used), codex=reading("codex", 20))
                self.assertEqual(r["decision"], "CODEX")
                self.assertNotIn("Codex is off", r["reason"])

    def test_codex_made_review_stays_with_claude(self) -> None:
        self.allow_gpt()
        for important, expected in ((False, "LEAD"), (True, "VERIFIER2")):
            with self.subTest(important=important):
                r = limits.route("review", root=self.p.dir, made_by="codex", important=important,
                                 claude=reading("claude", 85), codex=reading("codex", 20))
                self.assertEqual(r["decision"], expected)
                self.assertIn("K8", r["reason"])
                self.assertNotIn("Codex audit", r["how"])
        r = limits.route("review", root=self.p.dir, made_by="claude", important=False,
                         claude=reading("claude", 85), codex=reading("codex", 20))
        self.assertEqual(r["decision"], "GPT-REVIEW")
        self.assertNotIn("K8", r["reason"])

    def test_important_claude_review_audits_only_when_codex_has_room(self) -> None:
        self.allow_gpt()
        r = limits.route("review", root=self.p.dir, made_by="claude", important=True,
                         claude=reading("claude", 40), codex=reading("codex", 20))
        self.assertEqual(r["decision"], "VERIFIER2")
        self.assertIn("Codex audit", r["how"])
        self.assertNotIn("--rung3-skip", r["how"])
        r = limits.route("review", root=self.p.dir, made_by="claude", important=True,
                         claude=reading("claude", 40), codex=reading("codex", 95))
        self.assertEqual(r["decision"], "VERIFIER2")
        self.assertIn("--rung3-skip", r["how"])
        self.assertNotIn("+ Codex audit", r["how"])
        r = limits.route("review", root=self.p.dir, made_by="claude", important=False,
                         claude=reading("claude", 40), codex=reading("codex", 20))
        self.assertEqual(r["decision"], "GPT-REVIEW")

    def test_plan_stays_with_lead_uses_economy_and_waits_only_exhausted(self) -> None:
        self.allow_gpt()
        r = limits.route("plan", root=self.p.dir, claude=reading("claude", 85), codex=reading("codex", 20))
        self.assertEqual(r["decision"], "LEAD")
        self.assertNotIn("ECONOMY", r["reason"])
        r = limits.route("plan", root=self.p.dir, claude=reading("claude", 95), codex=reading("codex", 20))
        self.assertEqual(r["decision"], "LEAD")
        self.assertIn("ECONOMY", r["reason"])
        r = limits.route("plan", root=self.p.dir, claude=reading("claude", 99.5), codex=reading("codex", 20))
        self.assertEqual(r["decision"], "WAIT")
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 95), codex=reading("codex", 20))
        self.assertEqual(r["decision"], "CODEX")

    def test_unknown_claude_uses_kind_without_inventing_a_reading(self) -> None:
        self.allow_gpt()
        r = limits.route("code", root=self.p.dir, claude=None, codex=reading("codex", 80))
        self.assertEqual(r["decision"], "CODEX")
        self.assertEqual(r["claude_level"], "unknown")
        self.assertIn("unknown", r["reason"])
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 20), codex=reading("codex", 80))
        self.assertEqual(r["decision"], "HEAVY")
        self.assertNotIn("unknown", r["reason"])

    def test_weekly_pressure_can_make_a_side_critical(self) -> None:
        self.allow_gpt()
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 40, 20), codex=reading("codex", 20, 95))
        self.assertEqual(r["decision"], "HEAVY")
        self.assertEqual(r["codex_level"], "critical")
        r = limits.route("code", root=self.p.dir, claude=reading("claude", 40, 20), codex=reading("codex", 20, 25))
        self.assertEqual(r["decision"], "CODEX")
        self.assertEqual(r["codex_level"], "ok")

    def test_codex_reader_uses_session_limits_and_clears_past_resets(self) -> None:
        self.assertIsNone(limits.codex_limits())
        sessions = Path(os.environ["CODEX_HOME"]) / "sessions" / "2026" / "10" / "12"
        sessions.mkdir(parents=True)
        event = {"timestamp": "2026-10-12T00:00:00Z", "type": "event_msg",
                 "payload": {"type": "token_count", "rate_limits": {
                     "primary": {"used_percent": 25.0, "window_minutes": 300, "resets_at": 4102444800},
                     "secondary": {"used_percent": 9.0, "window_minutes": 10080, "resets_at": 4102444800},
                     "plan_type": "plus"}}}
        first = sessions / "rollout-x.jsonl"
        first.write_text(json.dumps(event) + "\n", encoding="utf-8", newline="\n")
        os.utime(first, (1700000000, 1700000000))
        r = limits.codex_limits()
        self.assertIsNotNone(r)
        self.assertEqual(r["provider"], "codex")
        self.assertEqual(r["five_hour"]["used"], 25)
        self.assertEqual(r["seven_day"]["used"], 9)
        for name in ("five_hour", "seven_day"):
            self.assertFalse(r[name]["reset_since_reading"])
        for name in ("primary", "secondary"):
            event["payload"]["rate_limits"][name]["resets_at"] = 1000000000
        second = sessions / "rollout-y.jsonl"
        second.write_text(json.dumps(event) + "\n", encoding="utf-8", newline="\n")
        os.utime(second, (1700000010, 1700000010))
        r = limits.codex_limits()
        self.assertIsNotNone(r)
        self.assertEqual(r["source"], second.name)
        for name in ("five_hour", "seven_day"):
            self.assertEqual(r[name]["used"], 0)
            self.assertTrue(r[name]["reset_since_reading"])

    def test_claude_reader_is_unknown_until_set_claude(self) -> None:
        self.assertIsNone(limits.claude_limits())
        r = run([str(TOOLS / "limits.py"), "set-claude", "--five-hour", "42", "--week", "18"],
                cwd=PLUGIN, env={"FORGE_HOME": os.environ["FORGE_HOME"]})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(Path(r.args[0]), Path(PY))
        cl = limits.claude_limits()
        self.assertIsNotNone(cl)
        self.assertEqual(cl["provider"], "claude")
        self.assertEqual(cl["five_hour"]["used"], 42)
        self.assertEqual(cl["seven_day"]["used"], 18)
        for name in ("five_hour", "seven_day"):
            self.assertFalse(cl[name]["reset_since_reading"])


    def test_codex_reader_compares_event_times_across_files(self) -> None:
        sessions = Path(os.environ["CODEX_HOME"]) / "sessions"
        sessions.mkdir(parents=True)
        for name, mtime, timestamp, used in (("old", 1700000000, "2026-10-12T02:00:00Z", 95),
                                            ("new", 1700000010, "2026-10-12T01:00:00Z", 10)):
            event = {"timestamp": timestamp, "payload": {"rate_limits": {
                "primary": {"used_percent": used}}}}
            f = sessions / f"rollout-{name}.jsonl"
            f.write_text(json.dumps(event) + "\n", encoding="utf-8", newline="\n")
            os.utime(f, (mtime, mtime))
        r = limits.codex_limits()
        self.assertEqual(r["source"], "rollout-old.jsonl")
        self.assertEqual(r["five_hour"]["used"], 95)
        self.assertEqual(limits.codex_limits(max_files=1)["five_hour"]["used"], 10)
        f = sessions / "rollout-new.jsonl"
        with f.open("a", encoding="utf-8", newline="\n") as log:
            log.write(json.dumps({"payload": {"rate_limits": {"primary": {"used_percent": 30}}}}) + "\n")
        os.utime(f, (2000000000, 2000000000))
        r = limits.codex_limits()
        self.assertEqual(r["source"], f.name)
        self.assertEqual(r["five_hour"]["used"], 30)
        self.assertEqual(r["seen_at"], limits._iso(2000000000))

    def test_missing_windows_stay_unknown(self) -> None:
        home = Path(os.environ["FORGE_HOME"]); home.mkdir(parents=True)
        (home / "claude_limits.json").write_text(json.dumps({"rateLimits": [
            {"kind": "seven_day", "percentUsed": 92}]}), encoding="utf-8", newline="\n")
        cl = limits.claude_limits()
        self.assertIsNone(cl["five_hour"])
        self.assertEqual(limits.fmt(cl, "Claude"), "Claude 7d 92%")
        self.assertEqual(limits.pressure(cl), 92)
        self.assertEqual(limits.earliest_reset(cl), "")
        sessions = Path(os.environ["CODEX_HOME"]) / "sessions"; sessions.mkdir(parents=True)
        (sessions / "rollout-unknown.jsonl").write_text(json.dumps({"payload": {"rate_limits": {
            "primary": {"resets_at": 1000000000}}}}) + "\n", encoding="utf-8", newline="\n")
        cx = limits.codex_limits()
        self.assertIsNotNone(cx)
        self.assertIsNone(cx["five_hour"])
        self.assertIsNone(cx["seven_day"])
        self.assertIsNone(limits.pressure(cx))
        self.assertEqual(limits.fmt(cx, "Codex"), "Codex unknown")
        self.assertEqual(limits.earliest_reset(cx), "")
        self.allow_gpt()
        r = limits.route("code", root=self.p.dir, claude=cl, codex=cx)
        self.assertEqual(r["decision"], "CODEX")
        self.assertEqual(r["codex_level"], "unknown")
        for used in (None, "bad", "42", [], {}, True, float("nan"), float("inf")):
            with self.subTest(used=used):
                self.assertIsNone(limits._window(used, "2000-01-01T00:00:00"))
        self.assertEqual(limits._window(0, None)["used"], 0)

    def test_naive_resets_are_utc_and_bad_values_do_not_crash(self) -> None:
        naive = "2000-01-01T00:00:00"
        self.assertEqual(limits._time(naive).tzinfo, timezone.utc)
        w = limits._window(95, naive)
        self.assertEqual(w["used"], 0)
        self.assertTrue(w["reset_since_reading"])
        self.assertEqual(limits._window(95, "2100-01-01T00:00:00")["used"], 95)
        for resets in ("not a time", [], {}, float("inf")):
            with self.subTest(resets=resets):
                w = limits._window(95, resets)
                self.assertEqual(w["used"], 95)
                self.assertFalse(w["reset_since_reading"])
        home = Path(os.environ["FORGE_HOME"]); home.mkdir(parents=True)
        (home / "claude_limits.json").write_text(json.dumps({"rateLimits": [
            {"kind": "seven_day", "percentUsed": 99.5, "resetsAt": "2100-01-01T00:00:00"}]}),
            encoding="utf-8", newline="\n")
        cl = limits.claude_limits()
        self.assertEqual(limits.earliest_reset(cl), "2100-01-01T00:00:00")
        self.assertEqual(limits.route("plan", root=self.p.dir, claude=cl)["decision"], "WAIT")

    def test_manual_windows_expire_independently_without_resets(self) -> None:
        home = Path(os.environ["FORGE_HOME"]); home.mkdir(parents=True)
        f = home / "claude_limits.json"
        data = {"source": "set-claude (by hand)",
                "updated": (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
                "rateLimits": [{"kind": "five_hour", "percentUsed": 95}, {"kind": "seven_day", "percentUsed": 92}]}
        f.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        cl = limits.claude_limits()
        self.assertIsNone(cl["five_hour"])
        self.assertEqual(cl["seven_day"]["used"], 92)
        self.assertEqual(limits.fmt(cl, "Claude"), "Claude 7d 92%")
        data["rateLimits"][0]["resetsAt"] = "2100-01-01T00:00:00Z"
        f.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        self.assertEqual(limits.claude_limits()["five_hour"]["used"], 95)
        data["rateLimits"][0].pop("resetsAt")
        data["updated"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        f.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        cl = limits.claude_limits()
        self.assertIsNone(cl["five_hour"])
        self.assertIsNone(cl["seven_day"])
        self.assertEqual(limits.fmt(cl, "Claude"), "Claude unknown")
        data["source"] = "forge-band"
        f.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        self.assertEqual(limits.claude_limits()["five_hour"]["used"], 95)

    def test_set_claude_accepts_and_validates_reset_times(self) -> None:
        env = {"FORGE_HOME": os.environ["FORGE_HOME"]}
        r = run([str(TOOLS / "limits.py"), "set-claude", "--five-hour", "42", "--week", "18",
                 "--five-hour-resets", "2100-01-01T00:00:00", "--week-resets", "2100-01-07T00:00:00Z"],
                cwd=PLUGIN, env=env)
        self.assertEqual(r.returncode, 0)
        data = json.loads((Path(env["FORGE_HOME"]) / "claude_limits.json").read_text(encoding="utf-8"))
        self.assertEqual(data["rateLimits"][0]["resetsAt"], "2100-01-01T00:00:00")
        self.assertEqual(data["rateLimits"][1]["resetsAt"], "2100-01-07T00:00:00Z")
        self.assertIsNotNone(datetime.fromisoformat(data["updated"]).tzinfo)
        cl = limits.claude_limits()
        self.assertEqual(cl["five_hour"]["used"], 42)
        for args in (("--five-hour-resets", "bad"), ("--week-resets", "2100-01-07T00:00:00Z")):
            with self.subTest(args=args):
                r = run([str(TOOLS / "limits.py"), "set-claude", "--five-hour", "42", *args],
                        cwd=PLUGIN, env=env, ok=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(json.loads((Path(env["FORGE_HOME"]) / "claude_limits.json").read_text(encoding="utf-8")), data)

    def test_step_brief_prints_limits(self) -> None:
        env = {name: os.environ[name] for name in ("FORGE_HOME", "CODEX_HOME")}
        run([str(TOOLS / "limits.py"), "set-claude", "--five-hour", "42", "--week", "18"], cwd=PLUGIN, env=env)
        r = run([str(TOOLS / "step.py"), "--brief"], cwd=self.p.dir, env=env)
        lines = [line for line in r.stdout.splitlines() if line.startswith("  limits:")]
        self.assertEqual(len(lines), 1)
        self.assertIn("Claude 5h 42% · 7d 18%", lines[0])
        self.assertIn("Codex unknown", lines[0])


if __name__ == "__main__":
    unittest.main()
