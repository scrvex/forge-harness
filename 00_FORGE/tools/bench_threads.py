"""Thread benchmark for local models (swarm spec §5, question 1 to the owner): num_thread 8 (P-cores) vs 22 (owner rule) vs default.

    python <plugin>/tools/bench_threads.py [model] [--cpu]   # default qwen3:8b; writes <state>/bench_threads*.json

Measures one fixed extraction-style prompt per setting via the Ollama API: load time, prompt eval t/s, generation t/s,
and the GPU/CPU split from `ollama ps`. Honest expectation: on a model that fits 100 % in VRAM the thread count barely
matters (threads drive CPU inference); the point of the measurement is to prove that and to set the rule for the
CPU-offload case, which the swarm rules forbid anyway.
"""
import json, pathlib, subprocess, sys, time, urllib.request

import sys as _s; _s.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from forge_common import find_root, load_cfg
STATE = load_cfg(find_root())["_layout"]["state"]
MODEL = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "qwen3:8b")
CPU = "--cpu" in sys.argv  # force CPU inference (num_gpu 0) — the case where num_thread actually matters
PROMPT = ("Extract every number with its context from this text as '- value — context' lines, nothing else:\n"
          "The installer is 48 MB and needs Windows 10 or 11 (64-bit). The menu opens in under 30 ms, holds up to 3 nested rings "
          "with 8 sectors each, ships 1756 icons, and the trial lasts 7 days. The license costs $19.99 once, templates $2, "
          "refunds within 14 days. Kando has about 6000 stars, v2.3.1 shipped in June 2026; a Stream Deck costs $149.99. "
          "Paddle charges 5% + $0.50 per sale. The editor preview is 450x450 px and profiles reload in about 1 second. "
          "Repeat the extraction once more, then list the three largest values.")

def bench(num_thread):
    body = {"model": MODEL, "prompt": PROMPT, "stream": False,
            "options": {"temperature": 0, "num_predict": 400, **({"num_gpu": 0} if CPU else {}), **({"num_thread": num_thread} if num_thread else {})}}
    t0 = time.time()
    req = urllib.request.Request("http://localhost:11434/api/generate", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r: d = json.loads(r.read())
    wall = time.time() - t0
    ps = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout.strip().splitlines()
    proc = next((" ".join(l.split()[3:5]) for l in ps[1:] if l.startswith(MODEL.split(":")[0])), "?")
    return {"num_thread": num_thread or "default", "wall_s": round(wall, 1),
            "load_s": round(d.get("load_duration", 0) / 1e9, 2),
            "prompt_tok_s": round(d.get("prompt_eval_count", 0) / max(d.get("prompt_eval_duration", 1) / 1e9, .001), 1),
            "gen_tok_s": round(d.get("eval_count", 0) / max(d.get("eval_duration", 1) / 1e9, .001), 1),
            "eval_count": d.get("eval_count"), "processor": proc}

out = {"model": MODEL, "date": time.strftime("%Y-%m-%d %H:%M"), "runs": []}
bench(None)  # warm-up: load the model once so load time doesn't pollute run 1
for n in (None, 8, 22, 8, 22):  # alternate to average out thermal / cache noise
    out["runs"].append(bench(n)); print(out["runs"][-1], flush=True)
byn = {}
for r in out["runs"][1:]:
    byn.setdefault(r["num_thread"], []).append(r["gen_tok_s"])
out["avg_gen_tok_s"] = {str(k): round(sum(v) / len(v), 1) for k, v in byn.items()}
out["mode"] = "cpu" if CPU else "gpu"
dest = STATE / ("bench_threads_cpu.json" if CPU else "bench_threads.json")
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print("saved:", dest, "| avg gen tok/s:", out["avg_gen_tok_s"])
