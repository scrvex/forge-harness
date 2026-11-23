"""Codex/ChatGPT as the other model family (K29-K31): the review of an order - the second actor for an ordinary Claude-made
order (1.0), rung 3 on top of Claude's own check (verifier2) for an important one; always triaged by the Lead.

    verify_gpt.py N007 [--files a.md,b.js] [--effort high]      # audit of an order -> <circle>/answers/N007_review_gpt.json
    verify_gpt.py factcheck --claims c.md [--about "..."] [--asof 2026-09-29] [--out f.json]
    verify_gpt.py advise "question" [--files a.md] [--out f.json]  # a second opinion; saved under <state>/gpt/

What ChatGPT gets: copies of the order (ORDER.md), the executor's answer (ANSWER.json: summary + checks, so it does not
report "cannot verify") and the deliverable files - text files as copies, images attached (-i). It does NOT get Claude's
review (no anchoring). What happens next is the Lead's job (K30): triage EVERY finding - accept / reject with a reason /
check against a primary source - and record it: `order.py report N ... --rung3 "..."`. A disputed fact is settled by a
primary source, never by a vote between models (pilot N008: ChatGPT was confidently wrong on a legal point once).
Pilot numbers (N008, 4 cases): 5/10 known defects found, 4 new real ones, 2 wrong findings; 1-4 min per case.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gpt_bridge  # noqa: E402
from forge_common import IMAGE_EXT, find_root, git_tree, load_cfg, read_index  # noqa: E402

TEXT_EXT = (".md", ".txt", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv", ".yaml", ".yml", ".ps1", ".sh", ".svg", ".sql")
MAX_FILE, MAX_TOTAL, MAX_IMAGES = 200_000, 600_000, 4

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdict", "dod", "findings", "usefulness"],
    "properties": {
        "verdict": {"type": "string", "enum": ["APPROVE", "CHANGES", "REJECT"]},
        "dod": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["line", "met", "why"],
                "properties": {"line": {"type": "string"}, "met": {"type": "boolean"}, "why": {"type": "string"}}}},
        "findings": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                     "required": ["file", "line", "severity", "issue", "evidence"],
                     "properties": {"file": {"type": "string"}, "line": {"type": "integer"},
                                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                                    "issue": {"type": "string"}, "evidence": {"type": "string"}}}},
        "usefulness": {"type": "string"}}}

FACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["claims", "summary"],
    "properties": {
        "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "status", "correct", "source"],
                   "properties": {"id": {"type": "string"}, "status": {"type": "string", "enum": ["подтверждено", "исправлено", "не проверено"]},
                                  "correct": {"type": "string"}, "source": {"type": "string"}}}},
        "summary": {"type": "string"}}}

ADVICE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["advice", "risks", "alternatives", "confidence"],
    "properties": {"advice": {"type": "string"}, "risks": {"type": "array", "items": {"type": "string"}},
                   "alternatives": {"type": "array", "items": {"type": "string"}},
                   "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}

READ_HINT = "Files are UTF-8 (often Cyrillic): in PowerShell use `Get-Content -Encoding UTF8`, or `rg -n`. Line 1 = first line."

REVIEW_PROMPT = """You are an independent expert auditor of one Forge order (rung 3). The author and its reviewer were another
model family (Claude); your value is catching what they missed. You advise; you never edit or create files.
Files here: `ORDER.md` = the order (Goal, Changes = the owner's request, Context, Deliverable, DoD, Quality bar);
`ANSWER.json` = what the executor says it did and checked; deliverable file(s): {files}.{images}{missing}
{hint}
Do:
1. Read the order, the answer, then every deliverable file in full. Cite exact line numbers.
2. For every DoD line: met / not met, one-line reason with file:line. If a line cannot be judged from what you have,
   say "не могу проверить: <what is missing>" and do not count it as not met.
3. Hunt for defects: a) the owner's request in Goal/Changes not carried out everywhere; b) factual claims that are wrong or
   unsupported - check checkable claims on the web and cite the URL; c) for code: bugs a real user would hit when running
   it (logic, state, events, edge cases), not style; d) invented numbers or guesses presented as facts; e) contradictions.
4. Severity: high = the owner or a third party gets a wrong result; medium = a visible defect; low = polish.
Verdict: APPROVE only if every judgeable DoD line is met and there is no high/medium finding; CHANGES if fixable;
REJECT if it is the wrong artifact. Write `why`, `issue`, `evidence`, `usefulness` in Russian. Report every finding that
changes the verdict or the owner's result, most severe first; low findings only while they stay short. Before you report a
finding, restate it in one line and read the cited lines again - drop it if it does not hold: reviewers tend to see defects
that are not there and to overrate their severity. No praise."""

FACT_PROMPT = """You are an independent fact-checker (rung 3; the author was another model family). Never edit files.
`{claims}` lists claims and open questions{about}. The facts must hold as of {asof}. {hint}
For every item: search the web, prefer primary sources (laws and acts on official portals, government service pages,
treaty registries), and decide: `подтверждено` (correct as written) / `исправлено` (wrong or misleading - give the
correct statement) / `не проверено` (no primary or reliable source found - say what is missing). For a question,
answer it in `correct`. `correct`: one or two sentences in Russian. `source`: the best URL. No praise."""

ADVICE_PROMPT = """You are an independent expert adviser to the Lead of a Forge project (another model family). You give a
second opinion; you never edit files and you do not decide - the Lead and the owner do.
Question: {question}
{files}{hint}
Answer in Russian: `advice` (what you would do and why, concrete), `risks` (what could go wrong), `alternatives` (other
options worth weighing), `confidence`. If the question needs facts, check them on the web and name the source."""


def _copy_items(paths: list[Path], root: Path) -> tuple[list[tuple[str, str]], list[Path], list[str]]:
    """-> (text copies [(name, text)], images to attach, names left out with the reason)."""
    texts, images, missing, total, seen = [], [], [], 0, set()
    for p in paths:
        rel = p.relative_to(root).as_posix() if p.is_relative_to(root) else p.name
        if not p.is_file():
            missing.append(f"{rel} (not a file)"); continue
        if p.suffix.lower() in IMAGE_EXT:
            if len(images) < MAX_IMAGES:
                images.append(p)
            else:
                missing.append(f"{rel} (image limit {MAX_IMAGES})")
            continue
        if p.suffix.lower() not in TEXT_EXT:
            missing.append(f"{rel} (binary, not sent)"); continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_FILE or total + len(text) > MAX_TOTAL:
            missing.append(f"{rel} (too large for one audit: split it)"); continue
        # Windows is case-insensitive: a deliverable "order.md" written after "ORDER.md" replaced the order copy (N010 rung 3)
        taken = {s.lower() for s in seen} | {"order.md", "answer.json"}
        name = p.name if p.name.lower() not in taken else f"{len(seen)}_{p.name}"
        seen.add(name); total += len(text)
        texts.append((name, text))
    return texts, images, missing


def _write_copies(tag: str, items: list[tuple[str, str]]) -> Path:
    wd = gpt_bridge.new_workdir(tag)
    for name, text in items:  # BOM only in these throw-away copies: Windows PowerShell 5.1 reads BOM-less UTF-8 as ANSI
        (wd / name).write_text(text, encoding="utf-8-sig", newline="\n")
    return wd


def audit_order(order_id: str, files: list[str] | None = None, effort: str = "high") -> dict:
    root = find_root(); cfg = load_cfg(root); circle = cfg["_layout"]["circle"]
    row = next((r for r in read_index(root) if r["id"] == order_id), None)
    if row is None:
        raise SystemExit(f"{order_id} not in the index")
    order_path = circle / "orders" / row["file"]
    ans_path = circle / "answers" / f"{order_id}.json"
    answer = json.loads(ans_path.read_text(encoding="utf-8")) if ans_path.is_file() else {"note": "no answer recorded yet"}
    deliverables = [root / f for f in (files or answer.get("files_changed", []))]
    deliverables = [x for p in deliverables for x in (sorted(q for q in p.rglob("*") if q.is_file()) if p.is_dir() else [p])]
    gpt_bridge.check_paths([order_path, ans_path, *deliverables])  # the answer goes out too (ChatGPT audit N009 #3)
    texts, images, missing = _copy_items(deliverables, root)
    if not texts and not images:
        raise SystemExit(f"{order_id}: nothing to audit - pass --files or record files_changed in the answer")
    items = [("ORDER.md", order_path.read_text(encoding="utf-8")), ("ANSWER.json", json.dumps(answer, ensure_ascii=False, indent=2))] + texts
    wd = _write_copies(f"audit_{order_id}", items)
    prompt = REVIEW_PROMPT.format(
        files=", ".join(f"`{n}`" for n, _ in texts) or "(none as text)",
        images=f"\nImages attached to this message: {', '.join(p.name for p in images)} - judge them against the DoD and quality bar." if images else "",
        missing=f"\nNot provided (do not fault the deliverable for what you cannot see): {'; '.join(missing)}." if missing else "",
        hint=READ_HINT)
    before = git_tree(root)  # K34d (0.9.2): Codex works on copies; the project tree must not change during the audit
    r = gpt_bridge.run(prompt, wd, schema=REVIEW_SCHEMA, effort=effort, images=images, tag=f"audit:{order_id}", log_near=root)
    after = git_tree(root)
    moved = None if before is None or after is None else sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    if moved and r.get("acl_alert"):  # 1.1.2: the rights guard wrote its own lesson after the run - not Codex's write
        lessons = (circle / "lessons").relative_to(root).as_posix() + "/"
        proposals = (cfg["_layout"]["docs"] / "KERNEL_PROPOSALS.md").relative_to(root).as_posix()
        moved = [p for p in moved if not (p.startswith(lessons) or p == proposals)]
    out = {**(r["parsed"] or {"verdict": "", "dod": [], "findings": [], "usefulness": ""}),
           "_meta": {"order": order_id, "by": "gpt:codex", "rung": 3, "advisory": True, "when": datetime.now().isoformat(timespec="seconds"),
                     "ok": r["ok"], "seconds": r["seconds"], "model": r["model"], "effort": r["effort"], "usage": r["usage"],
                     "error": r["error"], "files": [n for n, _ in texts] + [p.name for p in images], "not_sent": missing,
                     "tree_changed_during_run": moved[:50] if moved is not None else "not checked (not a git repository)"}}
    dest = circle / "answers" / f"{order_id}_review_gpt.json"
    if not out.get("verdict") and dest.is_file():  # a failed run never overwrites a finished audit (N009 verifier2 #3)
        dest = dest.with_name(f"{order_id}_review_gpt.failed.json")
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"path": dest, **out}


def factcheck(claims: str, about: str = "", asof: str = "", out: str | None = None, effort: str = "high") -> dict:
    src = Path(claims).resolve()
    gpt_bridge.check_paths([src])
    wd = _write_copies("factcheck", [("claims.md", src.read_text(encoding="utf-8"))])
    r = gpt_bridge.run(FACT_PROMPT.format(claims="claims.md", about=f" ({about})" if about else "", asof=asof or datetime.now().date().isoformat(),
                                          hint=READ_HINT), wd, schema=FACT_SCHEMA, effort=effort, tag="factcheck", log_near=src)
    res = {**(r["parsed"] or {}), "_meta": {k: r[k] for k in ("ok", "seconds", "model", "effort", "usage", "error")}}
    if out:
        Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return res


def advise(question: str, files: list[str] | None = None, out: str | None = None, effort: str = "medium") -> dict:
    root = find_root(); cfg = load_cfg(root)
    paths = [root / f for f in (files or [])]
    gpt_bridge.check_paths([root, *paths])
    texts, images, missing = _copy_items(paths, root)
    wd = _write_copies("advice", texts or [("README.txt", "no files - answer from the question")])
    files_line = (f"Files here for context: {', '.join(n for n, _ in texts)}.\n" if texts else "") + \
                 (f"Not provided: {'; '.join(missing)}.\n" if missing else "")
    r = gpt_bridge.run(ADVICE_PROMPT.format(question=question, files=files_line, hint=READ_HINT), wd, schema=ADVICE_SCHEMA,
                       effort=effort, images=images, tag="advice", log_near=root)
    res = {"question": question, **(r["parsed"] or {}), "_meta": {k: r[k] for k in ("ok", "seconds", "model", "effort", "usage", "error")}}
    dest = Path(out) if out else cfg["_layout"]["state"] / "gpt" / f"advice_{datetime.now():%Y%m%d_%H%M%S}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"path": dest, **res}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__); return 0
    if sys.argv[1] == "factcheck":
        ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("--claims", required=True)
        ap.add_argument("--about", default=""); ap.add_argument("--asof", default=""); ap.add_argument("--out"); ap.add_argument("--effort", default="high")
        a = ap.parse_args()
        print(json.dumps(factcheck(a.claims, a.about, a.asof, a.out, a.effort), ensure_ascii=False, indent=2)); return 0
    if sys.argv[1] == "advise":
        ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("question")
        ap.add_argument("--files", default=""); ap.add_argument("--out"); ap.add_argument("--effort", default="medium")
        a = ap.parse_args()
        r = advise(a.question, [f for f in a.files.split(",") if f], a.out, a.effort)
        print(f"advice -> {r['path']}\n{r.get('advice', r['_meta'].get('error'))}"); return 0
    ap = argparse.ArgumentParser(); ap.add_argument("id"); ap.add_argument("--files", default=""); ap.add_argument("--effort", default="high")
    a = ap.parse_args()
    r = audit_order(a.id, [f for f in a.files.split(",") if f] or None, a.effort)
    m = r["_meta"]
    moved = r["_meta"].get("tree_changed_during_run")
    if isinstance(moved, list) and moved:  # another session or Codex itself - visible either way (K34d)
        print(f"K34 note: {len(moved)} project path(s) changed while ChatGPT ran (another session, or Codex wrote outside its copy): "
              + "; ".join(moved[:5]))
    print(f"rung 3 (ChatGPT, advisory): {r.get('verdict') or 'NO VERDICT'} · {len(r.get('findings', []))} finding(s) · "
          f"{m['seconds']}s · in {m['usage'].get('input_tokens', '?')} tok -> {r['path']}")
    for f in r.get("findings", []):
        print(f"  [{f['severity']}] {f['file']}:{f['line']} {f['issue'][:160]}")
    if not m["ok"]:
        print("  error:", m["error"][:300])
    print(f"next: triage every finding, then order.py report {a.id} ... --rung3 \"accepted ..., rejected ... (why), checked ...\"")
    return 0 if m["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
