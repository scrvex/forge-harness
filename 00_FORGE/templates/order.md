# {{ID}} — {{TITLE}}

- **Tier:** {{TIER}}   **Created:** {{DATE}}   **Phase:** Investigate → Brainstorm → DoD → Plan → **Execute** → Verify → Review
- **Mode:** autonomous within scope   **Parent order:** -
- **Changes:** {{CHANGES}}
{{DOMAIN_HEADER}}
- **Language of the deliverable:** {{LANG}} (the user's language; internal notes may be English; en is fine for code, websites and other artifacts with an international audience — say so in the Goal)

## Request (verbatim — the owner's words; when anything below disagrees, this wins — K34a)
{{REQUEST}}

## Goal
{{GOAL}}

## Decision (≤ 5 lines for the owner at DoD — cheap to review)
{{DECISION}}

## Context (read only — never edit, never "update" these files)
{{SCOPE}}

## Deliverable (the one file the executor writes; nothing else)
- `{{DELIVERABLE}}`

## DoD (checkable; the owner approves this section once, K19)
{{DOD}}

## Plan (only when the work splits into tasks — K19 one plan; the executor per task by limits, K37)
Tasks with disjoint file scopes in `circle/answers/{{ID}}_plan.json` (the Lead writes it): `limits.py route --kind code --order {{ID}}` picks the
executor; Codex: `codex_exec.py wave {{ID}}` (read-only proposals), the Lead reads each `answers/{{ID}}_<task>.patch` and runs
`apply`; Claude: the `executor` agent per task, same scopes; `status {{ID}}` shows
progress; a failed task goes to `fix` or to the `executor` agent.

## Quality bar (what an expert in this role would call good — K25; the executor aims here, the reviewer checks here)
{{QUALITY}}

{{DOMAIN_VERIFY}}
## Forbidden
- writing anywhere except the Deliverable; deleting owner files; secrets or personal data to a cloud model (K15)
- approving your own result (K8); changing STATE.md or 00_INDEX.md by hand (K9); git commands (commit, branch, push,
  stash, reset are the Lead's)
- presenting guesses as facts: anything not taken from Context or the user's sources is marked "(предположение)" / "(assumption)"
{{DOMAIN_FORBIDDEN}}

## Context for the executor (what to read first, in order)
- `kernel/CONSTITUTION.md` sections A–C; this file; the Context files above

## Answer
`circle/answers/{{ID}}.json` — keys: order, executor, tier, status, files_changed, summary, checks
(`order.py answer {{ID}} --executor ... --summary ... --files a,b` — every changed file, literally). The Lead's verdict: `circle/reports/{{ID}}.md`.
Unsure what is meant? Do not guess inside the deliverable: `order.py answer {{ID}} --executor ... --status blocked --summary "..." --question "..." --ambiguous "..." --guess "what I would do"` (K34e).
