"""Swarm acceptance (K27, 0.7.0): end-of-cycle check -> accept -> apply, by script; the lead judges the report, not drafts.

    python <plugin>/tools/swarm_accept.py plan.json [--apply] [--ollama] [--staging DIR] [--keep-staging]

The manifest (written by the lead BEFORE any worker starts - K26) lists every file of the cycle in order:
  {"cycle": "N010", "mode": "pipeline", "checks": ["python 01_PROJECT_CORE/tools/some_check.py"],
   "files": [{"path": "02_.../doc.md", "tier": "HEAVY", "goal": "...", "tasks": ["..."], "context": ["..."],
              "dod": ["must contain: X", "no: Y", "regex: ...", "json: true", "min_kb: 2", "max_kb: 64",
                      "a human line the reviewer reads"], "depends_on": []}]}
Workers write into <state>/staging/<cycle>/<path>; nothing touches the project until every file passes:
  0. exists, UTF-8 without BOM, LF, size (gate_encoding/gate_size rules);
  1. scriptable DoD lines (must contain / no / regex / json / min_kb / max_kb);
  2. project checks from "checks" (each must exit 0);
  3. --ollama: advisory review of short text files via the tuned local model (never a veto).
Red -> only the failing stage reruns with its problems list (max 2 rounds, then HOLD + lesson - K21).
Green -> ONE reviewer pass over the "for_reviewer" lines -> --apply copies all files in one go (sha256 in the
report), clears staging (--keep-staging keeps it) and the cycle closes with `order.py done --commit`.
No worker judges its own file (K8); the report lands in <state>/swarm/<cycle>_accept.json.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from forge_common import find_root, load_cfg  # noqa: E402


def check_file(entry: dict, staged: pathlib.Path) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    if not staged.exists():
        return ["missing in staging"], []
    raw = staged.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("UTF-8 BOM")
    if b"\r\n" in raw:
        problems.append("CRLF line endings")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return problems + ["not UTF-8"], []
    kb = len(raw) / 1024
    for_reviewer: list[str] = []
    for line in entry.get("dod", []):
        l = line.strip()
        if l.startswith("must contain:"):
            if l.split(":", 1)[1].strip() not in text:
                problems.append("DoD: " + l)
        elif l.startswith("no:"):
            if l.split(":", 1)[1].strip().lower() in text.lower():
                problems.append("DoD: " + l)
        elif l.startswith("regex:"):
            if not re.search(l.split(":", 1)[1].strip(), text, re.S):
                problems.append("DoD: " + l)
        elif l.startswith("json:"):
            try:
                json.loads(text)
            except ValueError as e:
                problems.append(f"DoD: json ({e})")
        elif l.startswith("min_kb:"):
            if kb < float(l.split(":")[1]):
                problems.append(f"DoD: {l} (is {kb:.1f} KB)")
        elif l.startswith("max_kb:"):
            if kb > float(l.split(":")[1]):
                problems.append(f"DoD: {l} (is {kb:.1f} KB)")
        else:
            for_reviewer.append(l)
    if kb > 64 and not any(x.strip().startswith("max_kb:") for x in entry.get("dod", [])):
        problems.append("over 64 KB (R31)")
    return problems, for_reviewer


def main() -> int:
    if "--apply" in sys.argv:
        from forge_common import refuse_in_codex
        refuse_in_codex("swarm_accept.py --apply")  # K29: applies files into the project - the Lead's job
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest"); ap.add_argument("--apply", action="store_true"); ap.add_argument("--staging")
    ap.add_argument("--ollama", action="store_true"); ap.add_argument("--keep-staging", action="store_true")
    a = ap.parse_args()
    root = find_root(); lay = load_cfg(root)["_layout"]
    m = json.loads(pathlib.Path(a.manifest).read_text(encoding="utf-8")); cycle = m.get("cycle", "cycle")
    staging = pathlib.Path(a.staging) if a.staging else lay["state"] / "staging" / cycle
    report: dict = {"cycle": cycle, "mode": m.get("mode"), "time": time.strftime("%Y-%m-%d %H:%M"),
                    "files": [], "checks": [], "green": True, "for_reviewer": {}}
    for e in m["files"]:
        e["path"] = str(e.get("path", "")).replace("\\", "/")  # manifest paths are posix; a Windows `..\\` escapes on every OS (1.1.0)
        staged = staging / e["path"]
        # containment guard (verifier2 2026-09-20): a manifest path must never escape staging or the project root
        try:
            ok_path = staged.resolve().is_relative_to(staging.resolve()) and (root / e["path"]).resolve().is_relative_to(root.resolve())
        except (OSError, ValueError):
            ok_path = False
        if not ok_path:
            report["files"].append({"path": e["path"], "tier": e.get("tier"), "ok": False, "problems": ["path escapes staging/root — rejected"], "advisory": None})
            report["green"] = False
            continue
        problems, for_rev = check_file(e, staged)
        adv = None
        if a.ollama and not problems and staged.suffix in (".md", ".txt") and staged.stat().st_size < 6000:
            r = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("local_jobs.py")), "summarize", str(staged)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            adv = (r.stdout or r.stderr)[-500:]
        report["files"].append({"path": e["path"], "tier": e.get("tier"), "ok": not problems, "problems": problems, "advisory": adv})
        if for_rev:
            report["for_reviewer"][e["path"]] = for_rev
        if problems:
            report["green"] = False
    for cmd in m.get("checks", []):
        r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
        report["checks"].append({"cmd": cmd, "exit": r.returncode, "tail": (r.stdout + r.stderr)[-400:]})
        if r.returncode != 0:
            report["green"] = False
    if a.apply and report["green"]:
        for e, rf in zip(m["files"], report["files"]):
            src = staging / e["path"]; dst = root / e["path"]
            dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
            rf["sha256"] = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
        report["applied"] = True
        if not a.keep_staging and staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
    out = lay["state"] / "swarm"; out.mkdir(parents=True, exist_ok=True)
    (out / f"{cycle}_accept.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({k: report[k] for k in ("cycle", "green")}, ensure_ascii=False), "| checks:", [c["exit"] for c in report["checks"]])
    for f in report["files"]:
        print(("  OK   " if f["ok"] else "  FAIL ") + f["path"] + ("" if f["ok"] else "  — " + "; ".join(f["problems"])))
    if report["for_reviewer"]:
        print("  for the reviewer (not scriptable):", json.dumps(report["for_reviewer"], ensure_ascii=False))
    if a.apply:
        print("  applied, staging cleared" if report.get("applied") else "  NOT applied (red)")
    return 0 if report["green"] else 1


if __name__ == "__main__":
    sys.exit(main())
