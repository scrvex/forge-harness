"""Gate: no text file above `max_kb` (K11) — big files are read whole and burn tokens; split them.
Data files (`data_ext`, default .csv/.tsv/.jsonl) have their own limit `max_kb_data` (default 2048): a dataset is read by
scripts, not by the model (0.9.2 — the N012 live run's 269 KB synthetic A/B dataset failed the 64 KB document limit)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from forge_common import iter_files  # noqa: E402


def check(cfg: dict) -> list[str]:
    limit = int(cfg.get("max_kb", 64)) * 1024
    data_limit = int(cfg.get("max_kb_data", 2048)) * 1024
    data_ext = {e.lower() for e in cfg.get("data_ext", [".csv", ".tsv", ".jsonl"])}
    out = []
    for p, rel in iter_files(cfg):
        lim = data_limit if p.suffix.lower() in data_ext else limit
        if p.stat().st_size > lim:
            out.append(f"{rel}: {p.stat().st_size // 1024} KB > {lim // 1024} KB" + (" (data file limit)" if lim == data_limit else ""))
    return out
