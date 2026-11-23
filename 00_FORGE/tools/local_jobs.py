# -*- coding: utf-8 -*-
"""Concrete Ollama jobs — the grunt work the Lead offloads to the local model (0 tokens).

    python tools/local_jobs.py summarize <file> [--words 200]
    python tools/local_jobs.py review <file> [<file> ...] --rules <rules.md>   # contradictions / gaps → JSON
    python tools/local_jobs.py lessons <journal.md>                            # lesson candidates → JSON
    python tools/local_jobs.py classify "<task in one sentence>"               # domain / pack routing → JSON
    python tools/local_jobs.py draft-order "<what to do>" [--context <file>]   # order skeleton (markdown)

Output: JSON to stdout (and, for review/lessons/classify, a copy in state/cache/last_<job>.json).
The local model *proposes*; the Lead confirms. Nothing here writes into docs or kernel.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ollama_client import SMALL_MODEL, ask_json, ask_text, available  # noqa: E402

from forge_common import find_root, layout  # noqa: E402
ROOT = find_root()
OUT_DIR = layout(ROOT)["state"] / "cache"
MAX_CHARS = 36000            # ~9k tokens of input, leaves room inside num_ctx=16k


def read(path: str, limit: int = MAX_CHARS) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    txt = p.read_text(encoding="utf-8", errors="replace")
    return txt[:limit] + ("\n[...truncated...]" if len(txt) > limit else "")


def save(job: str, data: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"last_{job}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- jobs
def summarize(path: str, words: int) -> str:
    system = ("You compress documents for an engineer. Keep facts, numbers, names, rules. "
              "No praise, no filler. Output plain text in the document's language.")
    prompt = f"Summarise the following document in at most {words} words, as bullet points.\n\n{read(path)}"
    return ask_text(system, prompt, job="summarize")


def review(files: list[str], rules: str) -> dict:
    system = ("You are a strict reviewer of instruction documents for an AI agent system. "
              "Find CONTRADICTIONS between documents, statements that violate the rules, STALE references "
              "(paths, names that do not match), and GAPS (a rule mentioned but never defined). "
              "Be concrete: quote the exact sentence and file. Do not invent problems; an empty list is a valid answer. "
              "Answer in JSON: {\"findings\": [{\"severity\": \"high|medium|low\", \"type\": \"contradiction|violation|stale|gap\", "
              "\"file\": str, \"quote\": str, \"problem\": str, \"suggestion\": str}], \"summary\": str}")
    parts = [f"=== RULES ({rules}) ===\n{read(rules, 14000)}"]
    budget = MAX_CHARS - len(parts[0])
    for f in files:
        chunk = read(f, max(2000, budget // max(1, len(files))))
        parts.append(f"=== FILE {f} ===\n{chunk}")
    out = ask_json(system, "\n\n".join(parts), job="review")
    # Lesson L001: JSON mode guarantees syntax, not schema — a finding may escape findings[] to the top level.
    findings = out.get("findings") if isinstance(out.get("findings"), list) else []
    if {"severity", "problem"} <= set(out):
        findings.append({k: out.pop(k) for k in ("severity", "type", "file", "quote", "problem", "suggestion") if k in out})
    out["findings"] = findings
    save("review", out)
    return out


def lessons(journal: str) -> dict:
    system = ("You mine an engineering journal for lessons. A lesson = something that failed or worked, its root "
              "cause, and what should change in rules/templates/checks so it does not repeat. "
              "Only lessons supported by the text; quote the evidence. Answer in JSON: "
              "{\"lessons\": [{\"what\": str, \"root_cause\": str, \"fix\": str, \"applies_to\": \"kernel|pack|project\", "
              "\"evidence\": str, \"confidence\": \"high|medium|low\"}]}")
    out = ask_json(system, f"Journal:\n\n{read(journal)}", job="lessons")
    save("lessons", out)
    return out


def classify(task: str) -> dict:
    system = ("You route a task description to a domain pack for an AI-assisted work system. "
              "Domains: invest, analytics, marketing, accounting, legal, code, decision_support, writing, research, other. "
              "Answer in JSON: {\"domain\": str, \"confidence\": \"high|medium|low\", \"secondary_domains\": [str], "
              "\"inputs\": [str], \"outputs\": [str], \"decisions_owner_must_make\": [str], "
              "\"deterministic_checks\": [str], \"data_sources\": [str], \"risks\": [str], "
              "\"clarifying_questions\": [str] (max 7)}")
    out = ask_json(system, f"Task: {task}", job="classify", model=SMALL_MODEL)  # M7: routing on the small model
    save("classify", out)
    return out


def draft_order(what: str, context: str | None) -> str:
    system = ("You draft a self-contained work order for an executor agent that cannot ask questions. Sections, in "
              "this order: Goal · Read first (files) · Write (allowed paths) · Do not touch · Steps · Answer format "
              "(JSON: files_created, files_modified, tests_added, not_done, blocked_by) · Gates that must pass. "
              "Never state the expected result values (only what must be checked). English, markdown, ≤ 60 lines.")
    prompt = f"Task: {what}\n\n" + (f"Context:\n{read(context, 12000)}" if context else "")
    return ask_text(system, prompt, job="draft_order")


# ---------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="job", required=True)
    s = sub.add_parser("summarize"); s.add_argument("file"); s.add_argument("--words", type=int, default=200)
    r = sub.add_parser("review"); r.add_argument("files", nargs="+"); r.add_argument("--rules", required=True)
    l = sub.add_parser("lessons"); l.add_argument("journal")
    c = sub.add_parser("classify"); c.add_argument("task")
    d = sub.add_parser("draft-order"); d.add_argument("what"); d.add_argument("--context")
    a = ap.parse_args()

    try:  # K28: no Ollama run while the machine is critical (throttling, memory pressure)
        from machine import refuse_reason
        why = refuse_reason(1)
    except Exception:
        why = None
    if why:
        print(json.dumps({"_error": f"local run refused: {why}"}, ensure_ascii=False))
        return 1
    if not available():
        print(json.dumps({"_error": "Ollama not available — start it or pull the model (ollama list)"}))
        return 1
    if a.job == "summarize":
        print(summarize(a.file, a.words))
    elif a.job == "review":
        print(json.dumps(review(a.files, a.rules), ensure_ascii=False, indent=2))
    elif a.job == "lessons":
        print(json.dumps(lessons(a.journal), ensure_ascii=False, indent=2))
    elif a.job == "classify":
        print(json.dumps(classify(a.task), ensure_ascii=False, indent=2))
    elif a.job == "draft-order":
        print(draft_order(a.what, a.context))
    return 0


if __name__ == "__main__":
    sys.exit(main())
