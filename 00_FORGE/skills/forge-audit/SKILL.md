---
name: forge-audit
description: Use when the owner asks for an audit of a Forge project or of Forge itself («сделай аудит», «ревизия», «что улучшить в Forge»), at the end of a test cycle, or before a plugin release.
---
# Cycle audit (owner-facing, md + pdf)

`<plugin>` = the Forge plugin folder (printed by the SessionStart brief).

Sources of truth only: `<circle>/journal`, `<circle>/answers` (incl. `*_review.json`), `<circle>/reports`, `<circle>/lessons`,
`<state>/cache/ollama_usage.jsonl`, `<circle>/answers/N_usage.json` (Claude tokens), `<state>/session_log.md`, git log, file timestamps. Every number has a source;
estimates are labelled "оценка". Minutes per order = TAKEN → CLOSED journal lines; Ollama calls/seconds = the usage log.
Structure: 0 summary (10 lines) · 1 facts table · 2 errors by category with evidence (plugin / process / Lead / environment) ·
3 what worked · 4 speed and tokens («Скорость и токены»: `python <plugin>/tools/usage.py report [--days N]` - spend units per
order from the transcripts (Lead vs agents), reported tokens, actual/estimate ratio, unassigned runs; `usage.py scan N --apply`
measures a closed order after the fact from its transcripts; an order without a ledger or transcript is "не измерено";
where they go, measures with expected effect) · 5 proposals to the plugin numbered R-n
with priority · 6 lessons `applies_to: kernel` → `<tech>/docs/KERNEL_PROPOSALS.md` (proposals, never edits — K18/K23) ·
7 questions for the owner (numbered, your recommendation first) · **8 Forge vs direct Claude / Cowork**: for one
representative deliverable of the cycle, compare the Forge result with what Claude would produce directly (same request,
matching skill loaded) or a Cowork plugin would give: quality checklist (DoD + quality bar, met/not met), time, tool calls,
tokens (estimate) — so "where Forge is worth it" rests on numbers (decision B, 2026-09-18) · **9 skills (K32)**: save the
session's skill list to a file and run `python <plugin>/tools/skills_map.py audit --available-file <list> --md <tech>/docs/SKILLS_INVENTORY.md`
— report domains that are Unavailable / Requires Configuration, unresolved duplicates and overlaps, and whether the
domain quality bars caught or missed this cycle's defects (proposals to `domains.json` go to KERNEL_PROPOSALS).
· **11 revision (1.1.0, from ambatupipe's revision)** - defects of the project's own rules and files, each with the two
places that show it: two files contradict each other; a doc or memory disagrees with the code or git; a dead file, branch or
script nobody calls; a rule with no trigger or no check (it cannot be seen to work); a code contract no test covers.
· **12 context and spend** - where the Lead's context went this cycle: briefs to agents vs their reports, whole-file reads
vs symbol reads, plan upkeep (from the transcripts, `usage.py report`); name the three biggest items and a change for each.
A saving is claimed only from paired runs (same task with and without the change, the median of at least four runs a side,
each with a pass/fail check of the result) - a saving on a failed task does not count (K17).
· **13 guards (D3)** - `python <plugin>/tools/guards.py propose`: lessons that repeat; for each, propose a guard (command or
written-file pattern, warn or block, its lesson) or say why a rule or a script fits better. The owner decides; nothing is
added by the audit.
· **14 what to delete** - rules, steps and components the current Claude Code or model now covers on its own (native
`/goal`, workflows, the status line's limits, plugin evals, subagent memory) or that no lesson has needed for three cycles.
A harness only grows unless someone removes parts; each removal is a proposal with the evidence that the part is idle.
· **10 replay (K34f)**: `python <plugin>/tools/replay.py` (`--figures` for analysis projects) — re-runs today's checks on the
closed orders; triage every finding as a true catch the old process missed (lesson; a fixing order if it matters) or a false
positive of the check (fix the check). Count lessons with a `check` pattern vs all lessons: a lesson that never became a check
or a rule is a diary entry.
**Before a plugin release** (Forge workspace): tests green incl. a negative control for each new check; `replay.py` on the
workspace and on one real project; then `upgrade.py <project> --dry-run` on EVERY project (a hand-made CLAUDE.md must be reported, not rewritten) →
`upgrade.py <project>` on ONE project (canary),
its gates and `step.py` green, only then the other projects.
Write it in the user part, run `python <plugin>/tools/md2pdf.py`, copy to `C:/ai/agi/02_Для_пользователя/02_Отчёты_и_аудиты/`
if that workspace exists. Nothing is applied to the plugin by the audit itself.
