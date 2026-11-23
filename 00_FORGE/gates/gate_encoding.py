"""Gate: every text file is UTF-8 without BOM and uses LF line endings (K11)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from forge_common import iter_files  # noqa: E402


def check(cfg: dict) -> list[str]:
    problems = []
    for p, rel in iter_files(cfg):
        raw = p.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            problems.append(f"{rel}: UTF-8 BOM")
        if b"\r" in raw:
            problems.append(f"{rel}: CR line endings (use LF)")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            problems.append(f"{rel}: not UTF-8 at byte {e.start}")
    return problems
