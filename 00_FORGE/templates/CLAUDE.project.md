# CLAUDE.md — project `{{PROJECT}}` (Forge plugin v{{KERNEL_VERSION}}; user language: {{LANG}}; mode: {{MODE}})

Read at session start: this file → `{{TECH}}/docs/CONTEXT.md` → the SessionStart brief (`step.py --brief`, printed by the
Forge plugin hook). Language: the user's language ({{LANG}}) with the owner and in everything under `{{READ}}/`; English
in `{{TECH}}/`.

## Three levels (K23–K25) — what lives where
- **Core 1 = the Forge plugin** (installed in Claude Code / Cowork, not copied here): constitution, skills, agents, hooks,
  gates, scripts, templates. Never edited from a project; wanted changes go to `{{TECH}}/docs/KERNEL_PROPOSALS.md`.
  The plugin folder is printed in the brief (`plugin: …`) — that is where `tools/` and `gates/` are (`{{PLUGIN}}` now).
- `{{TECH}}/` — **Core 2, the project core that learns**: `circle/` (orders, answers, reports, journal, lessons), `state/`,
  `tools/` (project scripts + TOOLS.md), `config/`, `docs/` (brief, CONTEXT, EN), `PLAN.md`, `PROJECT_RULES.md`.
- `{{READ}}/` — **for the user**: numbered context folders (tables, final documents), the usage guide (md + **pdf**).
  Only end results of requests, plain language; every document the user reads also as PDF (`md2pdf.py`).
- `CLAUDE.md`, `README.md`, `forge.json` — entry points at the root.

## Mode (K25) — ask before the first order
`forge.json` → `mode`: `cheap` = the local model (Ollama) does extraction, first-pass checks and boilerplate drafts
(LOCAL orders allowed); `expensive` = no local model, Claude does everything (LOCAL orders refused). If the mode is
not set, the brief says so: **ask the owner, then** `python <plugin>/tools/step.py mode cheap|expensive`.

## Skills (from the plugin — load them, do not re-derive)
Before every order `forge-route` (who executes, which Claude skill leads each stage). Code → `forge-code` (+ canon
`forge-code-structure`, `forge-code-testing`). Codex/ChatGPT as reviewer, adviser, images → `forge-gpt`. One artifact →
`forge-direct`. Several dependent files → `forge-swarm`. Close → `forge-done`; check → `forge-review`. Work without the
owner → `forge-autonomous`. Writing an order, a skill or a prompt → `forge-instruction-writing`; answering a person →
`forge-human-explanation`. Rules → `forge-rules` (full text `step.py rule Kn`). Also `forge-new`, `forge-handover`, `forge-audit`.
Agents: `investigator` (read-only questions about code and documents), `researcher` (web and source research into one
named file), `verifier2` (independent read-only reviewer), `executor-fast` and `executor` - never `general-purpose`, and
the executor never judges itself (K8). Hooks: brief at start and after a compaction (K40), owner's knobs, K15 guard and
lesson guards on commands (K38), `check_file` after writes, token ledger, Stop runbook (blocks only an autonomous run, K20).

## Two model families (K29–K31, Forge 1.0) — the owner's decisions 2026-10-05 and 2026-10-11
- **Claude leads and judges**: request, DoD, plan and task cutting, architecture, owner-facing text, Verify, every close.
- **Executors by limits (K37)**: before a task leaves you, `python <plugin>/tools/limits.py route --kind ...` names Codex or
  a Claude agent; neither is the default. Codex runs as tier CODEX in patch mode (`codex_exec.py`: it proposes read-only,
  you read the diff and `apply`) and reviews Claude-made work (`verify_gpt.py N`; for an ordinary order its APPROVE is the
  second actor). Important orders (`--important`, implied by `--external`) need `verifier2` too (output saved to
  `{{TECH}}/circle/answers/N_review_v2.md`, first line `VERDICT: …`). Codex never reviews Codex's work (K8).
- Off until the owner says yes (`gpt_setup.py --allow`); then Codex reads `AGENTS.md` (written by the plugin — do not
  hand-edit it); `order.py` refuses to run inside Codex. Skills: `forge-code`, `forge-gpt`.

## Code (K35–K36, Forge 1.0) — when this project writes code
Skill `forge-code` has the flow: Serena for large files, Context7 for library APIs, plan with disjoint scopes in
`{{TECH}}/circle/answers/N_plan.json`, Codex waves in the background → read each `.patch` → `apply`, then
`gates/check_code.py --order N` and the project's tests (forge.json `code.test`). Git stays with you. Never green by
weakening a check. Missing tools: `python <plugin>/tools/setup.py doctor` (the owner runs `setup.py install`).

## Swarm and the machine (K26–K28)
Every complex unit starts with the K26 check (`swarm_probe.py --decide files=N[,independent][,bulk][,owner]`; `order.py
new` prints it): ≥ 3 files with separable contexts → swarm (skill `forge-swarm`: manifest in `{{TECH}}/circle/plans/`,
workers write only to `{{TECH}}/state/staging/<cycle>/`, `swarm_accept.py` at the end, one reviewer pass, atomic apply);
1–2 files → one order. The PC is measured (`machine.py`, in the brief): local models by free VRAM, load < 90 %, Claude
sub-agents ≤ 2; web-research fan-out only after the owner's yes with a cost estimate.

## Skills map (K32–K33)
Every order names its domain(s): `skills_map.py detect "<task in the owner's words>"` → confirm → `order.py new ... --domain
<ids>` (default = forge.json `domains`). The order then carries the card: one primary skill per stage (load it before the
work), the quality bar, "Verify by running" and the forbidden lines. A missing skill → say so and keep the card's bar.
Third-party skills: `skills_map.py vet <folder>` → the owner's yes → a role in the plugin's `domains.json` (K33).

## Evidence discipline (K34, Forge 0.9.2)
The owner's words verbatim (`--request`, `--request-source`), else the Goal is a hypothesis until the DoD is approved;
unclear points as `[NEEDS CLARIFICATION: …]` in the DoD (`take` refuses until `amend --resolve`). Every check has an
expected result and a negative control. Declare every changed file in `--files`. An unsure executor answers `blocked`
with its question and guess. "Готово" only with evidence (forge-review). Plugin upgrades: `upgrade.py <project> --dry-run`.

## Session routine
1. Brief (hook) or `python <plugin>/tools/step.py` — mode, state, open orders, gates, next command.
2. Act on the owner's request only; cheapest actor first (K5) within the mode; `forge-route` before every order.
   Default lane (K6): questions, read-only research, git housekeeping and memory updates need no order — anything that
   produces a deliverable or changes project files does. Owner's knobs: `ss` = short read-only answer; "без тестов /
   без ревью …" = a waiver, recorded word for word (`--waive`, K19).
3. Every unit: `order.py new` → `take` → execute → **`order.py done N --text "verdict" [--executor lead:claude --summary ...] --commit`**
   (= answer + report + close + commit, gates inside). `--deliverable` = a file in the right user folder under
   `{{READ}}/`; `--scope` = context files (read only); `--dod` and `--quality` written by Claude, never by the local model.
4. End: `python <plugin>/tools/step.py end --did "..." --commit "..."`; refresh PDFs of changed user documents;
   append "what could be improved" to the project's improvements file in `{{READ}}/`.

## Commands (all 0 tokens; `<plugin>` = the folder printed in the brief; the full registry: `<plugin>/TOOLS.md`)
| what | command |
|---|---|
| where are we | `python <plugin>/tools/step.py` · gates: `python <plugin>/gates/run_gates.py` |
| new order | `python <plugin>/tools/order.py new "title" --tier ... --changes "..." --dod "x;y" --quality "..." --domain ids --request "owner's words" --request-source "..."` |
| close in one command | `python <plugin>/tools/order.py done N001 --text "..." [--executor lead:claude --summary "..."] --commit` |
| Codex proposes / applies | `python <plugin>/tools/codex_exec.py wave N001` (background) → read `answers/N001_T1.patch` → `apply N001 T1` |
| code checks | `python <plugin>/gates/check_code.py --order N001` |
| autonomous run | `python <plugin>/tools/autorun.py start --words "..." ` · `status` · `ask "..." --guess "..."` · `stop` |
| lesson / guard | `python <plugin>/tools/lesson.py --failed ... --cause ... --fix ...` · `python <plugin>/tools/guards.py propose` |
| md → pdf | `python <plugin>/tools/md2pdf.py file.md [out.pdf]` |

## When the user greets or asks "what can you do"
Skill `forge-new`: answer with `<plugin>/templates/HELLO.ru.md` — plain words, no internals — and ask the first three
interview questions; continue from `templates/brief.md` until the picture is complete; play the task back before building.

## When the base setup is done (first orders closed, guide filled)
Skill `forge-handover` (project is autonomous; can be detached by moving the folder — the plugin stays installed).

## Writing files (R31)
Write files with the Write/Edit tools, UTF-8 without BOM, LF line endings, ≤ 64 KB (split bigger ones). The plugin's
PostToolUse hook runs `check_file.py` after every Write/Edit and reports a bad file immediately — fix it before moving
on. Never build Python source through shell heredoc + string replace (lesson L004).
Windows PowerShell 5.1 sends text piped into a program as ASCII: pass Cyrillic or JSON to a script through a file (write it
with the Write tool, pass the path) or run the command from Bash (from ambatupipe v8.11.7). Forge's own Codex calls pass
the prompt from Python, which keeps UTF-8.

## Standing rules (owner, 2026-09-17/18)
- K22: name missing context/data and what it would improve; if declined — propose a workaround.
- Deliverables in the user's language; never invent numbers; visualisations and repetitive work are scripts.
- R19: when the person's gender is unknown, write impersonal or plural forms ("вы", "рекомендуется"), never guess.
- R26: LOCAL orders may be TAKEN alongside your one HEAVY/LEAD order; judge them with `step.py --brief` + `order.py done`.
- Routine work with standard tools (marketing, analytics, mail, CRM) → Cowork plugins/connectors; one-shot artifacts →
  direct Claude (`forge-direct`); Forge orders only for the complex recurring work this project exists for (decision B).

## Project rules (beyond the constitution)
See `{{TECH}}/PROJECT_RULES.md`.
