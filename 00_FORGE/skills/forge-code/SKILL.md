---
name: forge-code
description: Use when a Forge project needs a change to code - a feature, a bug fix, a refactor, a script, a site or a tool («сделай», «почини», «добавь», «перепиши» about code) - or when an order's domain is code or frontend. Not for a question about code (answer it directly) and not for a one-shot artifact (forge-direct).
---
# Code in Forge 1.0 — Claude leads, the limits pick the writer (Codex or a Claude agent), both families check

`<plugin>` = the Forge plugin folder from the brief. Missing tools → `python <plugin>/tools/setup.py doctor` and ask the owner
to run `setup.py install` (an agent never installs software, K35f). Codex off in this project (K15) → the same flow with the
`executor` agent (HEAVY) or `executor-fast` instead of Codex. The code canon is in skills `forge-code-structure` and
`forge-code-testing`: load them before the plan; Codex gets them in its prompt, the `executor` agent reads them.

Three rules first (1.1.0): never get green by weakening a check (skipping or deleting a test, loosening an assertion,
widening a tolerance, editing a lint/type/test config); a bug seen in passing is named and opened as its own order
(`order.py new`), never fixed inside the current one; `codex_exec.py run|wave|fix` and long test commands run in the
background (Bash `run_in_background`) - a foreground call over 10 minutes is moved to the background anyway, and an
interrupted run is reset by `codex_exec.py status N`.

## 0. Lane (K6)
Question, explanation, read-only look → answer directly, no order. The moment you decide what to change → an order:
first `limits.py route --kind code`, then `order.py new "<title>" --tier <CODEX|HEAVY as routed> --domain code --changes "..." --request "<owner's words>" --request-source "chat <date>"
--dod "...;..." --quality "..." [--important for owner-facing finals, security, data, payments] [--waive "tests: <words>"]`.
Tier: what `limits.py route --kind code` says (CODEX or HEAVY - neither is the default, K37); HEAVY when the change shapes
the architecture or Codex is off; LEAD only for a few lines.

## 1. Investigate (cheap, read-only)
- Serena first for large files: `get_symbols_overview`, `find_symbol` / `find_referencing_symbols` for what the change
  touches; `list_memories` → `read_memory core` for the project's conventions. Small files and small edits: plain Read and
  Edit - for a one-line edit `Edit` sends several times less than `replace_symbol_body` (Serena's own evaluation).
- Library or framework API → Context7: `resolve-library-id` → `query-docs` (state the version). Never code from memory of an API.
- Several questions or a big codebase → `investigator` agents (Sonnet, read-only, up to 5 in parallel, one numbered packet
  each) — never `general-purpose` for research.

## 2. DoD and plan (the owner approves once, K19)
- DoD lines a script can check (a test passes, a command prints X, check_code green) + reviewer lines (behaviour, edge cases).
- One coherent edit → no plan: one task. Several parts → `answers/N_plan.json`:
  `{"order": "N007", "tasks": [{"id": "T1", "task": "what + acceptance", "scope": ["src/a.py", "tests/test_a.py"],
  "context": ["docs/api.md"], "depends": []}, ...]}` — scopes disjoint inside a wave, each task self-contained (Codex sees
  only the order, its task and the files). Tests are part of the task that changes the behaviour.
- What `apply` will demand is written into the task (1.2.0, K35g); Codex reads it in its prompt:
  - `"tests"`: `true` = the patch must change a test file; a path or a list of paths = the patch must change those files or
    folders; `false` = no tests needed whatever the DoD says. Left out = the DoD decides: tests are required when it mentions
    tests, the owner did not waive `tests:` and the task's scope holds a test path (a docs-only scope only prints a warning).
    Keep the test paths inside `scope` - `wave` refuses a plan whose `tests` its scope cannot hold, before Codex starts.
  - `"check"` (a command, or a list of commands) and `"checks"` (a list): `apply` runs them in that order through the platform
    shell (cmd.exe on Windows, /bin/sh elsewhere) on a scratch copy of the project with the patch laid over it; each must exit 0
    within `FORGE_CHECK_TIMEOUT` seconds (default 900; the whole process tree is killed at the limit). Write a command that
    imports from its own working directory: an editable install would test the original tree, not the patch.
  - Tests required and no `check` / `checks` → forge.json `code.test` runs instead; neither exists → `apply` refuses.
  - Example task: `{"id": "T1", "task": "...", "scope": ["src/a.py", "tests/test_a.py"], "depends": [], "tests": true,
    "check": "python -m unittest tests.test_a"}`.
- Several plans (stages) → one order per stage, each closed with its own commit.

## 3. Execute
- First `python <plugin>/tools/limits.py route --kind code --order N007` (K37): Codex or the `executor` agent, by the limits left.
- Codex: `python <plugin>/tools/codex_exec.py wave N007` (or `run N007 --task "..." --scope a,b` for one task). Codex runs
  read-only and PROPOSES (patch mode, owner's choice 2026-10-12): it never writes to the project. Claude: the `executor`
  agent per task, same scopes.
  Effort: forge.json `code.codex_effort` (medium); `--effort high` for tricky logic. Slots: `wave --slots N` (default 3), lowered
  by `wave` itself to the live machine load (K28), and 0 refuses the wave.
  Codex reads nothing of Forge but its prompt: the project's code rules ride in it - the Serena memory `core` and the files
  in forge.json `code.rules_files` (e.g. `PROJECT_RULES.md`, a project skill's SKILL.md).
- Read the printed result: PROPOSED / PARTIAL / BLOCKED / FAILED, `question`, rejected files (outside the scope or Forge state —
  never staged), K34d writes during the run (should be none). BLOCKED → answer the question (amend the order) and `fix`.
  FAILED or limit → the printed `next:` line: it follows the limits (K37) - `fix` while Codex has room, else the `executor`
  agent (HEAVY), same task and scope; the second failure of a task prints HOLD: hold the order and ask the owner (K21).
  Read the answer's `decision:` / `beyond the task:` / `IRREVERSIBLE:` lines - they are what the diff does not show.
- Read `answers/N007_T1.patch` yourself — you are the second family for Codex's code. Wrong → `fix` with what is wrong.
  Right → `codex_exec.py apply N007 T1`: it refuses a file changed since the proposal, a patch without the test files the task
  requires, and a patch on which its `check` / `checks` fail (nothing is written then → `fix` with the printed output).
  `--skip-checks "reason"` bypasses the tests and the checks of that apply; the reason is journaled and kept in the evidence -
  it is not a way to get green. The next `wave` starts only the tasks whose dependencies are applied. Last apply: `--answer`
  records the order's answer from the applied files.

## 4. Verify (by running — K35c)
- `python <plugin>/gates/check_code.py --order N007` → `answers/N007_checks.json`; red → `codex_exec.py fix` with the
  failing output, not a hand patch that hides it. Run the change on a real input (the script, the page, the endpoint).
- Critical module or tricky logic: mutation and property-based checks (skill `forge-code-testing`) - surviving mutants
  are missing tests.
- Two Verify rounds without progress → HOLD + lesson (K21). Progress = no new failing check and at least one earlier
  failure gone, counted from `answers/N_checks.json` runs, not from memory.

## 5. Review and close
- Codex-made code: the Lead's report judges it (two families); `--important` → `verifier2` too (save its output to
  `answers/N_review_v2.md`).
- Claude-made code (HEAVY/LEAD): `python <plugin>/tools/verify_gpt.py N007` (Codex review; ordinary orders: its APPROVE is
  the second actor) or `verifier2`; triage every finding (`--rung3`). Uncommitted code outside an order: `/codex:review`.
- `order.py done N007 --text "..." --commit` — git only by the Lead, only the order's files (K14). Memories: a stable,
  non-obvious convention learned here → Serena `write_memory` (linked from `core`); state → CONTEXT.md.

## Never
Codex reviewing Codex (K8) · Codex touching git, forge.json, the circle or files outside its scope · code from memory of a
library API · research on anything but the lean agents (never `general-purpose`) · installing software from a session · sending anything from a project
the owner did not open to ChatGPT (K15 — the guard refuses it anyway).
