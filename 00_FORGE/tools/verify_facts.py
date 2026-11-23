"""verify_facts — Verifier 1 (K4 rung 1): the local model checks a document's claims against source texts.

    verify_facts.py <document.md> --sources a.txt,b.txt [--out report.json] [--chunk 14000]

The document is cut into chunks; for each chunk the model receives the chunk plus the sources and returns
JSON: claims that are NOT supported by the sources, claims that CONTRADICT them, and wrong attributions
(author/year/section). Advisory only (K8): the Lead reads the findings and decides. Sources are read-only.
Exit code 0 always; findings go to stdout and --out.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import re
import sys
from pathlib import Path


def _kernel_tools() -> Path:
    here = Path(__file__).resolve()
    for d in here.parents:
        if (d / "forge.json").is_file():
            cfg = json.loads((d / "forge.json").read_text(encoding="utf-8"))
            return d / (cfg.get("layout", {}).get("kernel") or cfg.get("kernel_dir", "kernel")) / "tools"
    raise SystemExit("forge.json not found above " + str(here))


sys.path.insert(0, str(_kernel_tools()))
from ollama_client import MODEL, ask_json, available  # noqa: E402

SYSTEM = ("You are a strict fact-checker. You get a DOCUMENT CHUNK (Russian or Ukrainian) that paraphrases academic "
          "SOURCES (English). Check every factual claim in the chunk: attributions (author, year, section, page), "
          "definitions, findings, numbers. Use ONLY the SOURCES; never your own knowledge. Return one JSON object: "
          "{\"unsupported\": [{\"claim\": str, \"why\": str}], \"contradicted\": [{\"claim\": str, \"source_says\": str}], "
          "\"wrong_attribution\": [{\"claim\": str, \"correct\": str}], \"ok_count\": int}. "
          "Quote the claim in its original language. Be concrete and brief. Empty lists are fine.")


def chunks(text: str, size: int) -> list[str]:
    out, cur = [], ""
    for para in re.split(r"\n(?=#{1,3} )", text):
        if len(cur) + len(para) > size and cur:
            out.append(cur); cur = ""
        cur += ("\n" if cur else "") + para
    if cur:
        out.append(cur)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("doc"); ap.add_argument("--sources", required=True); ap.add_argument("--out")
    ap.add_argument("--chunk", type=int, default=14000)
    a = ap.parse_args()
    if not available():
        print("Ollama not reachable"); return 0
    src = "\n\n".join(f"### {Path(s).name}\n{Path(s).read_text(encoding='utf-8', errors='replace')}"
                      for s in a.sources.split(",") if s.strip())
    doc = Path(a.doc).read_text(encoding="utf-8")
    findings = {"doc": a.doc, "model": MODEL, "chunks": []}
    for i, ch in enumerate(chunks(doc, a.chunk), 1):
        prompt = f"# DOCUMENT CHUNK {i}\n{ch}\n\n# SOURCES\n{src}"
        r = ask_json(SYSTEM, prompt, job="verify_facts")
        r.pop("_meta", None)
        findings["chunks"].append(r)
        for k in ("unsupported", "contradicted", "wrong_attribution"):
            for item in r.get(k) or []:
                print(f"[{i}] {k.upper()}: {json.dumps(item, ensure_ascii=False)}")
        print(f"[{i}] ok_count={r.get('ok_count')}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(findings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
