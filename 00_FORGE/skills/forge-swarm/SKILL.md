---
name: forge-swarm
description: Use when one request produces several dependent files (sources -> facts -> document -> PDF, a generated site, data -> table -> chart), when the owner says «рой», «конвейер» or «по файлам», or when a single order would need more than two review rounds. Not for one artifact (forge-direct).
---
# Swarm (K26–K28) — manifest first, one file per agent, acceptance at the end

`<plugin>` = the Forge plugin folder from the brief. The lead architect **controls the system**: it plans the manifest,
launches workers, and judges only from the acceptance report — it never reads drafts and never delegates judgement
(owner 2026-09-20). Verified practice behind each rule: `N006_research_patterns.md` / `N006_research_local.md` in the
Forge workspace (Anthropic, OpenAI, MetaGPT, Aider, SWE-agent, MAST, Reflexion — with URLs).

## 0. Should this be a swarm? — run the K26 check, don't guess
`python <plugin>/tools/swarm_probe.py --decide files=N[,independent][,bulk][,owner]` returns swarm/single + mode + why
(`order.py new` prints it for HEAVY/LEAD units automatically). Breakeven: ~3 separable-context files; 1–2 files →
`forge-direct`/one order. Web research fan-out → **ask the owner first** with a cost estimate (measured 2026-09-20:
~130–150k tokens and 6–9 min per research agent). Record `mode: X, because ...` in the order.

## 1. Probe this machine (0 tokens — K28)
The brief already prints the machine line (`machine.py`, cached 6 h). For sizing: `python <plugin>/tools/swarm_probe.py
[--manifest plan.json] [--refresh]` → slots (Claude ≤ 2, one heavy local run, scripts = cores/2), local models chosen by
free VRAM, `num_thread` never set on GPU (bench: model reload + ~4× slower prompt eval), CPU offload off (~11× slower,
memory-bound). Keep total load < 90% — the person is also using this PC. Run CPU scripts in parallel with GPU model runs.

## 2. Write the manifest before anyone works (K26)
`<circle>/plans/<cycle>.json` — schema in `<plugin>/templates/swarm.plan.example.json`. One entry per file, in chain
order; `goal` + `tasks` + `context` + `dod` are the worker's ENTIRE brief (objective, format, sources, boundaries —
nothing else is sent); scriptable DoD lines (`must contain:` `no:` `regex:` `json:` `min_kb:` `max_kb:`) plus human
lines; `depends_on` defines the chain, files without it may run in parallel within slots. The owner sees the file list
once (K19), then the swarm runs autonomously.

## 3. Pick the mode, write why (one line in the order)
| mode | best for | agents | acceptance |
|---|---|---|---|
| single | one artifact | 1 | gates + one reviewer |
| pipeline | chain of dependent files | 1–3 sequential | end of cycle |
| pipeline + parallel | independent branches | ≤ slots | end of cycle |
| map-reduce | long sources → LOCAL chunks → HEAVY merge | 1 local loop + 1 merger | merger output |
| generate → critique → fix | owner-facing docs, code with tests | 2, ≤ 2 rounds (K21) | reviewer list drives round 2 |
| research fan-out | unknown domain — **owner's yes first** | 2 | URLs present + lead |
Cheapest mode whose failure the person would notice; escalate one step only after a red acceptance with a lesson.
The mode table learns: `<config>/swarm_modes.json` — demote after 2 reds in a row on a task type, promote after 2
first-time greens (audit maintains it).

## 4. Run
In Claude Code the workflow `/forge:swarm-run` runs this section and §5 up to the apply (1.1.0): read the manifest, pass it
parsed in `args` with `order`, `plugin`, `root`, `manifestPath`, `staging` and `slots` (the machine's Claude slots); it
starts one Forge agent per file in dependency order, never more than `slots` at a time, runs `swarm_accept.py`, redoes a red
file once with its problems, then one `verifier2` pass over the lines no script checks - and returns the report. You judge
it and run `--apply` yourself. It is resumable after an interruption. Elsewhere (Cowork, no workflows) do the steps by hand:
Workers write ONLY to `<state>/staging/<cycle>/<path>`. Prompts carry file paths, not pasted content; every worker
returns a short pointer reply (file, findings that change the decision, open questions) + Recommendations. Every worker prompt names the order id (the usage hook books its
tokens, R-37); the manifest's `estimate_tokens` is compared with the actual total at close. LOCAL stages → `local_exec.py` / `local_jobs.py` with the models
from the machine profile; HEAVY stages → a Forge agent with the right skill (forge-route): `forge:researcher` for research,
sources and summaries, `forge:investigator` for questions about code, `forge:executor-fast` for mechanical edits by spec,
`forge:executor` for implementation, `forge:verifier2` for review - never `general-purpose` - it starts at ~73k tokens of prompt against ~10k for a Forge agent (measured 2026-10-11 over 72 runs: ~16.5M units of prefix alone);
a stage that needs judgement across several files is the Lead's; deterministic stages → scripts. No screenshots for verification — code-level checks (browser JS asserts, linters, project checks).

## 5. Accept at the end (K27)
`python <plugin>/tools/swarm_accept.py <circle>/plans/<cycle>.json [--ollama]` → per-file encoding/size/DoD + project
`checks`; red → rerun only the failing stage with its problems list (max 2 rounds → HOLD + lesson); green → ONE
reviewer pass over `for_reviewer` lines only (fresh context, diff + criteria — never the whole tree) → `--apply`
(atomic, sha256 in the report, staging cleared) → `order.py done N --commit`. Report: `<state>/swarm/<cycle>_accept.json`.

## 6. Learn
Lessons carry the mode; the audit compares the mode choice with the outcome and updates `swarm_modes.json`; kernel-level
findings go up as R-n proposals (K18).
