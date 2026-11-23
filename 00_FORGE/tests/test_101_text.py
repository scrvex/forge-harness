"""1.0.1 (K37, owner 2026-10-12): no rule text names a default executor. The owner's correction of 1.0.0 was exactly this
sentence; this test keeps it from coming back through a skill, a template or the skills map (N025 verifier2 r1 F1/F2)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kernel import PLUGIN  # noqa: E402

BANNED = [r"codex[^.\n]{0,40}\bby default", r"\bcodex(?:'s)? fallback", r"default (code )?executor", r"claude leads, codex writes",
          r"where codex is off\)", r"tier codex runs it", r"основной исполнитель"]
NEGATION = re.compile(r"(?i)\b(no|not|neither|nor|never|none of them|не)\b")


def rule_texts() -> list[Path]:
    return ([PLUGIN / "CONSTITUTION.md", PLUGIN / "domains.json", PLUGIN / "README.md"] + sorted((PLUGIN / "skills").glob("*/SKILL.md"))
            + sorted((PLUGIN / "templates").glob("*.md")) + sorted((PLUGIN / "agents").glob("*.md")))


class NoDefaultExecutor(unittest.TestCase):
    def offending(self, text: str) -> list[str]:
        hits = []
        for line in text.splitlines():
            for rx in BANNED:
                m = re.search(rx, line, re.I)
                if m and not NEGATION.search(line[:m.start()][-60:] + " " + line[m.end():][:20]):
                    hits.append(line.strip()[:140])
        return hits

    def test_rule_texts_name_no_default_executor(self) -> None:
        found = {p.relative_to(PLUGIN).as_posix(): h for p in rule_texts() if (h := self.offending(p.read_text(encoding="utf-8")))}
        self.assertEqual(found, {})

    def test_the_scan_catches_the_old_wording(self) -> None:  # negative control: the 1.0.0 sentences are caught
        for old in ("Tier: CODEX by default; HEAVY when", "`HEAVY` = Claude sub-agent `executor` (Codex's fallback);",
                    "Codex — основной исполнитель кода", "Codex is the **default executor** of specified"):
            self.assertTrue(self.offending(old), old)
        for ok in ("Neither family is the default executor (owner, 2026-10-12).", "none of them is the default;",
                   "Вы поправили: Codex не «основной исполнитель»."):
            self.assertFalse(self.offending(ok), ok)


if __name__ == "__main__":
    unittest.main()
