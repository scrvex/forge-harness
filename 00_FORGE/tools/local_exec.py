"""LOCAL-tier executor: run an order on Ollama *without losing context* (K3, K21).

The order file is the context package: goal, scope files, DoD, forbidden actions. This script reads it,
packs the scope files (bounded), adds the constitution's executor rules, asks the local model, and writes
`circle/answers/Nnnn.json` (+ `Nnnn_output.md` with the model's text). The Lead then judges (K8).

    local_exec.py N003                 # text task (summary / draft / extraction as markdown)
    local_exec.py N003 --json          # model must answer with a JSON object (schema in the order's DoD)
    local_exec.py N003 --max-chars 24000

Good for: summaries, extraction, drafts, first-pass reviews, classification. Not for: architecture, judgement,
owner-facing text (K3). Ollama offline -> answer status "failed", the order stays TAKEN.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import find_root, load_cfg, now_hm, read_index, today  # noqa: E402
from ollama_client import MODEL, ask_json, ask_text, available  # noqa: E402

LANG_NAMES = {"ru": "Russian", "en": "English", "de": "German", "es": "Spanish", "fr": "French"}
SYSTEM = ("You are the LOCAL executor of a Forge project. Write the CONTENT of the Deliverable file the ORDER asks "
          "for - a finished document the user will read (template, checklist, table, plan, text). The CONTEXT files "
          "are read-only background: do not update, rewrite or comment on them. Do not write an answer record, JSON "
          "envelope or status fields - the calling script writes circle/answers/*.json itself. Never invent files "
          "or facts: mark anything not taken from the context or the user's sources as an assumption "
          "('(предположение)' in Russian, '(assumption)' in English). If the order can be read two ways, do not "
          "guess: output ONLY one line 'BLOCKED: <your question> | <what exactly is ambiguous> | <what you would do>' "
          "(when JSON is required: {{\"BLOCKED\": \"<question> | <ambiguous> | <guess>\"}}) and stop (K34e). LANGUAGE OF THE DELIVERABLE: {lang} - the user's language, for every heading and line.")


def block(order_id: str, root: Path, raw: str) -> int:
    """K34e: record the model's question as a blocked answer (the order goes BLOCKED); nothing is written to the deliverable."""
    q, amb, guess = ([x.strip() for x in raw.split("|")] + ["", "", ""])[:3]
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "order.py"), "answer", order_id, "--executor",
                    f"ollama:{MODEL}", "--tier", "LOCAL", "--status", "blocked", "--summary", "the local model asked a question",
                    "--question", q or "(no question given)", "--ambiguous", amb or "(not given)", "--guess", guess or "(no guess given)"],
                   cwd=root)
    print(f"{order_id}: BLOCKED — the local model asks: {q or '(no question given)'}")
    return 1


def section(md: str, name: str) -> str:
    m = re.search(rf"^##\s*{name}\b[^\n]*\n(.*?)(?=^##\s|\Z)", md, re.S | re.M)
    return m[1].strip() if m else ""


LATEX = {"$\\le$": "≤", "$\\ge$": "≥", "$\\leq$": "≤", "$\\geq$": "≥", "$\\rightarrow$": "→", "$\\to$": "→",
         "$\\times$": "×", "$\\approx$": "≈", "$\\neq$": "≠", "$\\%$": "%"}


def normalize(text: str) -> str:
    """Deterministic clean-up of a markdown deliverable: the local model likes LaTeX tokens that renderers ignore."""
    for k, v in LATEX.items():
        text = text.replace(k, v)
    return re.sub(r"\$\\?(\w+)\$", r"\1", text)


def deliverable_path(order_md: str, order_id: str) -> str:
    m = re.search(r"`([^`]+)`", section(order_md, "Deliverable"))
    return m[1] if m else f"read/{order_id}.md"


def pack(root: Path, order_md: str, max_chars: int) -> str:
    scope = re.findall(r"`([^`]+)`", section(order_md, "Context") or section(order_md, "Scope"))
    parts, used = [], 0
    for rel in scope:
        p = root / rel
        if not p.is_file():
            parts.append(f"### {rel}\n(missing)"); continue
        text = p.read_text(encoding="utf-8", errors="replace")
        room = max_chars - used
        if room <= 0:
            parts.append(f"### {rel}\n(omitted: budget)"); continue
        text = text[:room] + ("\n...(truncated)" if len(text) > room else "")
        used += len(text)
        parts.append(f"### {rel}\n{text}")
    return "\n\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id"); ap.add_argument("--json", action="store_true"); ap.add_argument("--max-chars", type=int, default=20000)
    ap.add_argument("--by-section", action="store_true", help="R16: one model call per '## ' section of the DoD/Goal outline; each call sees only the context slice it needs (long sources, fewer wrong attributions)")
    a = ap.parse_args()
    root = find_root(); cfg = load_cfg(root)
    row = next((r for r in read_index(root) if r["id"] == a.id), None)
    if not row:
        raise SystemExit(f"{a.id} not in index")
    if row["state"] != "TAKEN":
        raise SystemExit(f"{a.id} is {row['state']} — take it first: order.py take {a.id}")
    if cfg.get("mode") == "expensive":
        raise SystemExit("K25: expensive mode — the local model is off; the Lead or a Claude sub-agent executes this order")
    order_md = (cfg["_layout"]["circle"] / "orders" / row["file"]).read_text(encoding="utf-8")
    order_md = re.split(r"^## Answer", order_md, flags=re.M)[0].rstrip()  # L003: the answer-JSON section misleads the model
    lang = LANG_NAMES.get(cfg.get("user_language", "ru"), cfg.get("user_language", "ru"))
    system = SYSTEM.format(lang=lang)
    rel_out = deliverable_path(order_md, a.id)
    prompt = (f"# ORDER {a.id}\n{order_md}\n\n# CONTEXT FILES (read only)\n{pack(root, order_md, a.max_chars)}\n\n"
              f"# YOUR OUTPUT = content of `{rel_out}`, in {lang}\n"
              + ("One JSON object only." if a.json else "Markdown text only - the finished deliverable, no JSON, no status fields, no commentary about the plan."))
    try:  # K28: no Ollama run while the machine is critical (throttling, memory pressure)
        from machine import refuse_reason
        _why = refuse_reason(1)
    except Exception:
        _why = None
    if _why:
        raise SystemExit(f"local run refused: {_why}")
    ans_dir = cfg["_layout"]["circle"] / "answers"; ans_dir.mkdir(parents=True, exist_ok=True)
    answer = {"order": a.id, "executor": f"ollama:{MODEL}", "tier": "LOCAL", "status": "failed",
              "files_changed": [], "summary": "", "checks": [], "when": f"{today()} {now_hm()}"}
    if not available():
        answer["summary"] = "Ollama not reachable"
    else:
        out_file = root / rel_out
        out_file.parent.mkdir(parents=True, exist_ok=True)
        if a.json:
            data = ask_json(system, prompt, job=f"exec_{a.id}")
            if isinstance(data, dict) and isinstance(data.get("BLOCKED"), str):  # K34e in JSON mode
                return block(a.id, root, data["BLOCKED"])
            out_file = out_file.with_suffix(".json")
            out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        elif a.by_section:
            # R16: outline first (cheap), then one call per section with the full context re-packed per call
            outline = ask_json(system, prompt + "\n\nFIRST STEP: return ONLY a JSON object {sections: [3-8 short section titles in the required language]} for the deliverable.", job=f"exec_{a.id}_outline")
            if isinstance(outline, dict) and isinstance(outline.get("BLOCKED"), str):  # K34e before any section is written
                return block(a.id, root, outline["BLOCKED"])
            titles = [str(t) for t in (outline.get("sections") or [])][:8] or ["Основная часть"]
            parts = []
            for i, title in enumerate(titles, 1):
                sec_prompt = (prompt + f"\n\nWRITE ONLY SECTION {i} of {len(titles)}: '{title}'. Other sections: {', '.join(titles)}. "
                              "Start with the section heading (## ...), no preamble, no repetition of other sections.")
                part = normalize(ask_text(system, sec_prompt, job=f"exec_{a.id}_s{i}")).strip()
                if part.startswith("BLOCKED:"):
                    return block(a.id, root, part[8:])
                parts.append(part)
            out_file.write_text("\n\n".join(parts).rstrip() + "\n", encoding="utf-8", newline="\n")
        else:
            text = normalize(ask_text(system, prompt, job=f"exec_{a.id}"))
            if text.lstrip().startswith("BLOCKED:"):  # K34e: the model asks instead of guessing - the order goes BLOCKED
                return block(a.id, root, text.lstrip()[8:])
            out_file.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
        answer.update(status="done", files_changed=[out_file.relative_to(root).as_posix()],
                      summary=f"LOCAL deliverable written to {out_file.relative_to(root).as_posix()} in {lang}; Lead must judge (K8)",
                      checks=["language: " + lang, "schema: see gate_json"])
    (ans_dir / f"{a.id}.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{a.id}: {answer['status']} — {answer['summary']}")
    if answer["status"] == "done" and not a.json and "--no-verify" not in sys.argv:  # cascade with a threshold (R24/R29)
        from verify import summary_line, verify_text  # noqa: E402
        r = verify_text(order_md, out_file.read_text(encoding="utf-8", errors="replace"), cfg.get("user_language", "ru"))
        (ans_dir / f"{a.id}_review.json").write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print("  " + summary_line(r) + "  (advisory; read the draft only if gaps or user-facing)")
    return 0 if answer["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
