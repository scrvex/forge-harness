"""Swarm sizing (K26/K28, 0.7.0): read this machine's profile and size the swarm for a manifest.

    python <plugin>/tools/swarm_probe.py                          # machine line + slots (probe cached by machine.py)
    python <plugin>/tools/swarm_probe.py --manifest plan.json     # + agents/mode for that manifest
    python <plugin>/tools/swarm_probe.py --refresh                # force a fresh probe

Sizing rule (SWARM_METHOD, research N006): agents = clamp(ceil(files / 3), 1, slots); files without `depends_on`
may run in parallel within the slots; one heavy local run at a time; the lead stays out of the worker count -
it plans, launches and judges from the acceptance report (owner 2026-09-20: the lead architect controls the system).
"""
from __future__ import annotations

import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from machine import ensure, line  # noqa: E402

TIER_W = {"LOCAL": 1, "FAST": 2, "HEAVY": 4, "LEAD": 6}


def decide(files: int = 1, dependent: bool = True, bulk: bool = False, owner_facing: bool = False, tuned: dict | None = None) -> dict:
    """K26 optimality check the Lead runs for EVERY complex unit: swarm or single, and why.
    Grounds (research N006 + owner 2026-09-20): worker isolation saves tokens only when stages have separable
    contexts; the fixed overhead (manifest + acceptance) breaks even around 3 files; multi-agent costs 3-15x a
    single pass, so a swarm must be justified, never a default; slots on THIS machine cap the parallelism;
    web-research fan-out needs the owner's explicit yes."""
    tuned = tuned or ensure()["tuned"]
    slots = tuned.get("slots", {})
    if bulk:
        if slots.get("local_heavy"):
            return {"swarm": True, "mode": "map-reduce", "workers": {"local": 1, "claude_merge": 1},
                    "why": "bulk chunks on the free local slot + one HEAVY merge - GPU time instead of tokens"}
        return {"swarm": True, "mode": "map-reduce (queued)", "workers": {"local": 0, "claude_merge": 1},
                "why": "bulk work wants the local slot, but it is busy right now (a model is loaded) - queue the chunks for the slot instead of burning Claude tokens on them"}
    if files <= 1:
        return {"swarm": False, "mode": "single", "why": "one deliverable - a single order (forge-direct) is cheapest; swarm overhead unpaid"}
    if files == 2:
        return {"swarm": False, "mode": "single", "why": "two files fit one order; manifest+acceptance overhead breaks even at ~3 separable files"}
    claude = slots.get("claude_subagents", 0)
    if claude == 0:
        return {"swarm": False, "mode": "single", "why": "no sub-agent slots on this machine - the Lead runs the chain itself"}
    mode = "pipeline" if dependent else ("pipeline+parallel" if claude >= 2 else "pipeline")
    out = {"swarm": True, "mode": mode, "workers": {"claude": min(max(1, -(-files // 3)), claude), "local": slots.get("local_heavy", 0)},
           "why": f"{files} separable-context files - workers see only their manifest line; acceptance is one scripted pass"}
    if owner_facing:
        out["why"] += "; owner-facing final file adds one generate->critique->fix round (<=2, K21)"
    return out



def size_swarm(manifest: dict, tuned: dict) -> dict:
    files = manifest.get("files", [])
    by_tier: dict[str, int] = {}
    for f in files:
        by_tier[f.get("tier", "HEAVY")] = by_tier.get(f.get("tier", "HEAVY"), 0) + 1
    complexity = sum(TIER_W.get(f.get("tier", "HEAVY"), 4) * (1 + len(f.get("depends_on", []))) for f in files)
    slots = tuned.get("slots", {})
    wanted = max(1, -(-len(files) // 3))
    claude = min(wanted, slots.get("claude_subagents", 0)) if (by_tier.get("HEAVY", 0) + by_tier.get("FAST", 0)) else 0
    local = min(by_tier.get("LOCAL", 0), slots.get("local_heavy", 0))
    mode = "single" if len(files) <= 1 else ("pipeline" if all(f.get("depends_on") for f in files[1:]) else "pipeline+parallel")
    return {"files": len(files), "by_tier": by_tier, "complexity": complexity, "mode_guess": mode,
            "agents": {"claude": claude, "local": local, "scripts": slots.get("scripts", 1)},
            "rule": "agents = clamp(ceil(files/3), 1, slots); parallel only without depends_on; one heavy local run"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--decide", help="K26 optimality check: files=N[,independent][,bulk][,owner] -> swarm or single, with why")
    a = ap.parse_args()
    if a.decide:
        kv = a.decide.split(",")
        d = decide(files=int(kv[0].split("=")[-1]), dependent="independent" not in kv, bulk="bulk" in kv, owner_facing="owner" in kv)
        print(json.dumps(d, ensure_ascii=False)); return 0
    d = ensure(refresh=a.refresh)
    print(line(d))
    out = {"tuned": d["tuned"]}
    if a.manifest:
        out["swarm"] = size_swarm(json.loads(pathlib.Path(a.manifest).read_text(encoding="utf-8")), d["tuned"])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
