# -*- coding: utf-8 -*-
"""Local LLM client (Ollama on the owner's GPU) for token-free grunt work.

Adapted from the owner's C:\\ai\\invest local_llm.py (same author, no licence issue) with two additions:
a SHA-256 response cache (idea from Circle v1 circle_prompt_cache.py) and a usage log.

    from ollama_client import ask_json, ask_text, available, stats
    data = ask_json(system, prompt)          # dict, JSON mode, temperature 0, cached
    text = ask_text(system, prompt)          # plain text, cached

Config via env: OLLAMA_URL (default http://localhost:11434), FORGE_MODEL (default gemma4:12b),
FORGE_NUM_CTX (default 16384 — fits 12 GB VRAM with a 12B Q4 model). Cache: state/cache/ollama_cache.json.
Standard library only. UTF-8, LF.
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from forge_common import find_root, layout  # noqa: E402
ROOT = find_root()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("FORGE_MODEL", "gemma4:12b")
SMALL_MODEL = os.environ.get("FORGE_SMALL_MODEL", "gemma3:4b")  # routing / classification / short checks (M7); falls back to MODEL if absent
NUM_CTX = int(os.environ.get("FORGE_NUM_CTX", "16384"))
TIMEOUT = 600
CACHE_FILE = layout(ROOT)["state"] / "cache" / "ollama_cache.json"
USAGE_FILE = layout(ROOT)["state"] / "cache" / "ollama_usage.jsonl"
CACHE_MAX = 500


# ---------------------------------------------------------------- cache
def _cache_load() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_save(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if len(cache) > CACHE_MAX:                       # drop oldest entries
        for k in sorted(cache, key=lambda k: cache[k].get("ts", 0))[: len(cache) - CACHE_MAX]:
            cache.pop(k, None)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")


def _key(system: str, prompt: str, json_mode: bool, model: str = "") -> str:
    return hashlib.sha256(f"{model or MODEL}\x00{json_mode}\x00{system}\x00{prompt}".encode("utf-8")).hexdigest()


def _log_usage(job: str, seconds: float, prompt_tokens, eval_tokens, cached: bool, model: str = "") -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model or MODEL, "job": job, "seconds": seconds,
           "prompt_tokens": prompt_tokens, "eval_tokens": eval_tokens, "cached": cached}
    with USAGE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- calls
def available() -> bool:
    try:
        r = urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5).read()
        return any(m["name"].startswith(MODEL.split(":")[0]) for m in json.loads(r).get("models", []))
    except Exception:
        return False


def ask(prompt: str, system: str = "", json_mode: bool = True, temperature: float = 0.0,
        num_ctx: int = NUM_CTX, job: str = "ask", use_cache: bool = True, model: str = "") -> dict:
    """Raw call. Returns the Ollama response dict plus _seconds and _cached. `model` overrides MODEL (e.g. SMALL_MODEL)."""
    model = model or MODEL
    key = _key(system, prompt, json_mode, model)
    cache = _cache_load() if use_cache else {}
    if use_cache and key in cache:
        hit = cache[key]
        _log_usage(job, 0.0, None, None, True, model)
        return {"response": hit["response"], "_seconds": 0.0, "_cached": True}
    body = {"model": model, "prompt": prompt, "system": system, "stream": False, "think": False,
            "options": {"temperature": temperature, "num_ctx": num_ctx}}
    if json_mode:
        body["format"] = "json"
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT).read())
    r["_seconds"] = round(time.time() - t, 1)
    r["_cached"] = False
    _log_usage(job, r["_seconds"], r.get("prompt_eval_count"), r.get("eval_count"), False, model)
    if use_cache:
        cache[key] = {"ts": time.time(), "response": r.get("response", "")}
        _cache_save(cache)
    return r


def ask_text(system: str, prompt: str, job: str = "text", temperature: float = 0.0, model: str = "") -> str:
    r = ask(prompt, system=system, json_mode=False, temperature=temperature, job=job, model=model)
    return (r.get("response") or "").strip()


def ask_json(system: str, prompt: str, job: str = "json", retries: int = 2, model: str = "") -> dict:
    """JSON-mode call; returns dict (with _meta) or {} on failure. Shrinks the prompt on HTTP 500."""
    for i in range(retries + 1):
        p = prompt if i == 0 else prompt + "\n\nReturn ONLY valid JSON. No prose."
        try:
            r = ask(p, system=system, job=job, use_cache=(i == 0), model=model)
        except urllib.error.HTTPError:            # context overflow or server hiccup → shrink and retry
            prompt = prompt[: max(4000, int(len(prompt) * 0.6))]
            continue
        except Exception as e:
            return {"_error": str(e)}
        txt = (r.get("response") or "").strip()
        m = re.search(r"\{.*\}", txt, re.S)
        for candidate in (txt, m.group(0) if m else ""):
            if not candidate:
                continue
            try:
                out = json.loads(candidate)
                if isinstance(out, dict):
                    out["_meta"] = {"model": MODEL, "seconds": r["_seconds"], "cached": r["_cached"],
                                    "prompt_tokens": r.get("prompt_eval_count"), "eval_tokens": r.get("eval_count")}
                    return out
            except json.JSONDecodeError:
                pass
    return {}


def stats() -> dict:
    """Totals from the usage log — for the dashboard."""
    n = calls = sec = ptok = etok = 0
    cached = 0
    try:
        for line in USAGE_FILE.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            n += 1
            if rec.get("cached"):
                cached += 1
            else:
                calls += 1
                sec += rec.get("seconds") or 0
                ptok += rec.get("prompt_tokens") or 0
                etok += rec.get("eval_tokens") or 0
    except FileNotFoundError:
        pass
    return {"requests": n, "live_calls": calls, "cache_hits": cached, "gpu_seconds": round(sec, 1),
            "prompt_tokens": ptok, "eval_tokens": etok, "model": MODEL}


if __name__ == "__main__":
    import sys
    if not available():
        print(f"Ollama not available or model {MODEL} not pulled at {OLLAMA_URL}")
        sys.exit(1)
    r = ask_json("You answer with JSON only.", 'Return {"ok": true, "model_says": "<one short word>"}', job="smoke")
    print(json.dumps(r, ensure_ascii=False))
    print(json.dumps(stats()))
