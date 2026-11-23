"""Verifier 1 for an order (cheap mode): the local model checks the deliverable against the order's DoD, line by line.

    verify.py N001              # -> <circle>/answers/N001_review.json + a one-line summary; advisory only (K8)
    verify.py N001 --print      # also print the findings

Cascade with a threshold (audit §7.4, R24/R29): after a LOCAL draft the Lead reads THIS summary, not the draft, for
internal orders; the full text is read only when the summary shows gaps or the document goes to the user / third
parties. Never closes an order by itself — the Lead or the owner does (K8). Moved into the kernel from trial.py."""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_common import find_root, load_cfg, now_hm, read_index, today  # noqa: E402
from ollama_client import MODEL, ask_json, available  # noqa: E402

LANG = {"ru": "Russian", "en": "English", "uk": "Ukrainian", "de": "German", "es": "Spanish", "fr": "French"}


def verify_text(order_md: str, output: str, lang: str) -> dict:
    order_md = re.split(r"^## Answer", order_md, flags=re.M)[0]
    dod = [l.strip() for l in re.findall(r"^- \[ \] (.+)$", order_md, re.M)] or ["(no DoD lines)"]
    qsec = re.search(r"^## Quality bar.*?\n(.*?)(?=^## |\Z)", order_md, re.S | re.M)
    quality = [l[2:].strip() for l in (qsec.group(1).splitlines() if qsec else []) if l.startswith("- ") and not l.startswith("- (")]
    dod = dod + quality  # the quality bar is judged line by line too (K25)
    system = ("You are a strict, skeptical reviewer of a document produced by another model. For EACH DoD line decide "
              "met=true/false with a one-line reason. Also check: is the whole document written in the required language; "
              "is it the deliverable itself (not a plan edit or commentary); are unverifiable facts marked as assumptions. "
              "Return one JSON object: {lines: [{line, met, why}], language_ok: bool, is_deliverable: bool, "
              "findings: list of up to 4 short English strings naming concrete gaps, usefulness: one English sentence}. "
              "Be harsh: a generic template that ignores the person's specifics is NOT met.")
    prompt = (f"REQUIRED LANGUAGE: {LANG.get(lang, lang)}\n\nORDER (Goal, Context list, DoD):\n{order_md[:3000]}\n\nDoD LINES:\n" +
              "\n".join(f"- {x}" for x in dod) + f"\n\nOUTPUT:\n{output[:6000]}")
    d = ask_json(system, prompt, job="verify")
    lines = [x for x in (d.get("lines") or []) if isinstance(x, dict)]
    met = sum(1 for x in lines if x.get("met") is True)
    total = max(len(dod), len(lines), 1)
    language_ok = bool(d.get("language_ok", False)); is_deliv = bool(d.get("is_deliverable", False))
    verdict = "APPROVE" if lines and met == len(lines) and language_ok and is_deliv else "CHANGES"
    findings = [str(x) for x in (d.get("findings") or [])][:4]
    if not language_ok:
        findings.insert(0, f"language is not {LANG.get(lang, lang)}")
    if not is_deliv:
        findings.insert(0, "not a deliverable (plan edit / commentary)")
    return {"verdict": verdict, "dod_met": f"{met}/{total}", "language_ok": language_ok, "is_deliverable": is_deliv,
            "lines": lines, "findings": findings[:5], "usefulness": str(d.get("usefulness", "")), "model": MODEL,
            "when": f"{today()} {now_hm()}", "advisory": True}


def summary_line(r: dict) -> str:
    return (f"V1 {r['verdict']}: DoD {r['dod_met']}, lang_ok={r['language_ok']}, deliverable={r['is_deliverable']}"
            + (f"; findings: {'; '.join(r['findings'])}" if r["findings"] else ""))


def main() -> int:
    from forge_common import refuse_in_codex
    refuse_in_codex("verify.py")  # K29: writes review evidence - the Lead's job, never ChatGPT's
    if len(sys.argv) < 2:
        print(__doc__); return 2
    oid = sys.argv[1]
    root = find_root(); cfg = load_cfg(root); circle = cfg["_layout"]["circle"]
    if cfg.get("mode") == "expensive":
        print("expensive mode: the local verifier is off — use the verifier2 sub-agent or the owner"); return 0
    if not available():
        print("Ollama not reachable"); return 1
    row = next((r for r in read_index(root) if r["id"] == oid), None)
    if not row:
        raise SystemExit(f"{oid} not in index")
    order_md = (circle / "orders" / row["file"]).read_text(encoding="utf-8")
    ans = circle / "answers" / f"{oid}.json"
    files = json.loads(ans.read_text(encoding="utf-8")).get("files_changed", []) if ans.is_file() else []
    m = re.search(r"`([^`]+)`", re.search(r"^## Deliverable\b.*?\n(.*?)(?=^##|\Z)", order_md, re.S | re.M).group(1) if re.search(r"^## Deliverable", order_md, re.M) else "")
    target = root / (files[0] if files else (m[1] if m else ""))
    if not target.is_file():
        raise SystemExit(f"{oid}: deliverable not found ({target}) — execute the order first")
    CODE_EXT = {".html", ".htm", ".js", ".ts", ".css", ".py", ".svg", ".xml", ".json", ".exe", ".zip", ".pdf"}
    if target.suffix.lower() in CODE_EXT:  # R-17 (0.7.0, lesson L003 ank-launch): judge text, never code, by reading
        print(f"V1 SKIP: {target.name} is code/binary — behavioural DoD needs scripted or browser checks, "
              "not a text review; run the project checks and verifier2 instead")
        return 0
    r = verify_text(order_md, target.read_text(encoding="utf-8", errors="replace"), cfg.get("user_language", "ru"))
    (circle / "answers" / f"{oid}_review.json").write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(summary_line(r))
    if "--print" in sys.argv:
        for l in r["lines"]:
            print(f"  - {'OK ' if l.get('met') else 'NO '} {l.get('line', '')[:80]} — {l.get('why', '')[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
