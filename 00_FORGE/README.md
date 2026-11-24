# Forge plugin v1.2.0 — a method layer for complex recurring work and code (Core 1)

Forge is a Claude Code / Cowork **plugin** that adds a repeatable process on top of Claude: interview → restated
task → a plan of small orders, each with a definition of done and a check → verification by a second actor (never the
author) → lessons that become enforced rules. It adds process, memory and cheap grunt work — it does not replace
Claude's own quality.

The rules are numbered **K1–K40** (`CONSTITUTION.md`) so orders, reports and lessons can cite them. The full version
history (0.6 → 1.2) lives in [`CHANGELOG.md`](CHANGELOG.md).

```
00_FORGE/  (this folder = the plugin)
├── .claude-plugin/plugin.json   name, version
├── CONSTITUTION.md              rules K1–K40 (roles, token ladder, circle, gates, hygiene, self-improvement, three levels, routing, swarm, two model families, skills map, code, limits, guards, evals, compaction)
├── kernel.json                  tiers, state tokens, schema keys, limits, changelog
├── domains.json                 skills map: 11 domains → pipeline (one primary skill per stage), quality bar, verify, conflicts, combos
├── TOOLS.md                     registry of hooks, gates and scripts
├── CHANGELOG.md                 version history, newest first (1.2.0; change logs live here, not in CONSTITUTION.md)
├── skills/                      forge-rules · forge-new · forge-route · forge-code · forge-direct · forge-review · forge-done · forge-handover · forge-audit · forge-swarm · forge-gpt ·
│                                forge-autonomous · forge-code-structure · forge-code-testing · forge-instruction-writing · forge-human-explanation (1.1.0)
├── agents/                      investigator (read-only Q&A on code/docs) · researcher (web/source research → one file) · verifier2 (independent reviewer) · executor-fast (FAST) · executor (HEAVY)
├── hooks/hooks.json             SessionStart brief (+ after compaction) · UserPromptSubmit knobs · PreToolUse K15 guard + lesson guards · PostToolUse check_file + guards · Agent/SubagentStop token ledger · Stop runbook (blocks only an autonomous run)
├── workflows/swarm-run.js       the swarm cycle as a native workflow (/forge:swarm-run, 1.1.0)
├── evals/                       behavioural cases for `claude plugin eval` (1.1.0, K39; they call models - run on the owner's word)
├── gates/                       run_gates + gate_encoding/size/index/state/json · check_file · check_html · check_figures · check_code · gpt_guard · gate_docs (1.2.0)
├── tools/                       step · order · usage · lesson · init_project · local_exec · local_jobs · verify · verify_facts · ollama_client · md2pdf · forge_common ·
│                                swarm_probe · swarm_accept · machine · bench_threads · gpt_bridge · verify_gpt · gpt_media · gpt_setup · skills_map · upgrade · replay ·
│                                codex_exec · limits · setup · transcripts · autorun · guards · prompt_knobs · statusline (1.1.0) · codex_safety (1.1.2) · reconcile · weaken_scan · order_checks · codex_accept (1.2.0)
├── templates/                   order · report · DoD · brief (interview) · HELLO · HANDOVER · GUIDE (user guide, RU) · CLAUDE.project · plan.example · answer · lesson
└── tests/ (kernel, swarm, skills map, 1.0–1.2)  python -m unittest discover -s 00_FORGE/tests -v  (CI: .github/workflows/tests.yml)
```

## Install (once)
- Dev workspace: `claude plugin marketplace add C:/ai/agi` then `claude plugin install forge@forge-dev`
  (or open `C:/ai/agi` — its `.claude/settings.json` recommends the plugin; confirm the prompt).
- Test without installing: `claude --plugin-dir C:/ai/agi/00_FORGE`.
- Requirements: Python 3.12 on PATH (`python`), `reportlab` for PDFs, Ollama with `gemma4:12b` (+ `gemma3:4b`) for cheap mode;
  for ChatGPT: the Codex app/CLI logged in with the owner's ChatGPT plan (`gpt_bridge.py --selftest`).

## How a project uses it
1. Skill `forge-new`: interview → play-back → mode → `plan.json` (Lead-written) →
   `python <plugin>/tools/init_project.py C:/ai/<task> --name <task> --plan plan.json --mode cheap --request "..." --git`.
2. Open the project folder: the SessionStart hook prints the brief (plugin path, mode, state, next command).
3. Work = orders (`order.py new/take/done`, `--domain` from `skills_map.py detect`); `forge-route` before each; `forge-review` before closing; `forge-done` closes.
4. Lessons accumulate in `01_PROJECT_CORE/circle/lessons/`; `forge-audit` turns them into proposals; plugin bumps happen
   only in `C:/ai/agi` (K18/K23).

## Design choices (why)
- **Files, not a database** — every LLM and the owner can read and diff them; git is the audit trail.
- **Scripts own the state** — STATE.md and 00_INDEX.md are edited only by `order.py`, so gates can trust them.
- **Context travels in the order file** — the local model receives goal + scope files + DoD + quality bar.
- **Plugin, not a copied kernel** (0.6.0) — Core 1 is installed once and versioned by Claude Code; projects stay small.
- Ported from the owner's Circle v1/v2 (state tokens, order index, gates, runner idea), rewritten in Python.

Workspace `C:\ai\agi` is the plugin's source and its own dogfooding project (`01_PROJECT_CORE/`, `02_Для_пользователя/`).
