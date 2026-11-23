---
name: forge-rules
description: Use when deciding how to act in a Forge project - who does what, which check applies, what is forbidden - or when a rule id such as K8 is mentioned.
---
# Forge rules — index, plugin 1.2.0 (progressive disclosure: this index at start, full text on demand)

- **K1** Owner decides: DoD, merges, every irreversible or paid action, strategy.
- **K2** Lead = Claude main session: investigates, writes DoD and orders, dispatches, judges executors, never approves its own artifact.
- **K3** Executors by tier: LOCAL = Ollama · CODEX = Codex CLI (plan tasks in waves, patch mode: proposes read-only, the Lead applies) · FAST = `executor-fast` · HEAVY = `executor` (Opus; implementation, always for architecture-shaping code); CODEX vs FAST/HEAVY by limits (K37) · LEAD · GPT = images. Research → `investigator` (code/docs) or `researcher` (web/sources), never `general-purpose`.
- **K4** Verifier ladder: 0 gates → 1 Ollama → 2 Claude `verifier2` (mandatory for important) → 3 Codex/ChatGPT (cross-family; counts for ordinary Claude-made orders) → owner.
- **K5** Cheapest actor first: script → Ollama → Codex (ChatGPT plan) → FAST → HEAVY → Lead → Owner; read with Serena symbols, not whole files.
- **K6** No order — no work: `circle/orders/Nnnn_*.md` listed in `00_INDEX.md`. Default lane without an order: answers, read-only research, git housekeeping, memory. A defect seen in passing gets its own order, never a fix inside the current one.
- **K7** An order has goal, Context (read-only), one Deliverable, checkable DoD, quality bar, tier, forbidden actions.
- **K8** Whoever produced an artifact does not approve it; one model family is one actor for Ollama and for ChatGPT.
- **K9** `STATE.md` (READY · RUNNING · AWAITING_OWNER · …) is changed only by `order.py`; when work resumes after a break (new session, compaction, crash) run `tools/reconcile.py` before touching an order: STATE.md is a claim, git is the fact.
- **K10** Every transition → journal; every red gate, review finding or owner correction → a lesson; Claude spend per order from the transcripts → `answers/N_usage.json`, vs estimate at close.
- **K11** Gates are scripts (encoding, size, index, state, json, docs); green before any close or commit; `weaken_scan.py` is read at done (a report, not a gate).
- **K12** CHANGES/REJECT, refused closes, overruled reviews and owner fixes (`new --fixes`) write lessons automatically.
- **K13** Code, plugin, Core 2 in English; everything the user reads in the user's language, .md + .pdf.
- **K14** Commit after each closed order — only that order's files (staged strays are refused by name; `--allow-extra` accepts one knowingly); message in Russian; no amend/rebase/force.
- **K15** Never delete owner files; nothing goes to a cloud model without the owner's yes (`forge.json` `gpt`, deny list).
- **K16** Third-party code: copy only no-attribution licences; otherwise read, learn, rewrite.
- **K17** Lessons → audit (`forge-audit`) → plugin version with a changelog (`kernel.json` and `CHANGELOG.md`); savings only from paired runs (median of ≥4 a side, failed runs don't count); every audit lists what to delete.
- **K18** Projects never edit the plugin; proposals go to `docs/KERNEL_PROPOSALS.md`.
- **K19** Phases Investigate → Brainstorm → DoD → Plan → Execute → Verify → Review; the owner approves once, at DoD. Owner's waivers ("без тестов / без ревью") recorded verbatim: `--waive`. Open `[NEEDS CLARIFICATION: …]` markers block `take`; `amend --resolve` answers them.
- **K20** Scope × mode (interactive / autonomous); reviewers are read-only; verdicts APPROVE · CHANGES · REJECT. Autonomous = a recorded run (`forge-autonomous`, `autorun.py`): start question «Запустить», Stop blocked only while orders in scope are open, once per state of the work; questions wait in the run's list.
- **K21** Two Verify rounds without progress → HOLD (with a reason), lesson, re-plan or ask the owner. Progress = no new failing check and at least one earlier failure gone (from the files on disk).
- **K22** Ask for missing context early; every report names what is missing and the workaround.
- **K23** Core 1 = the installed plugin, immutable inside a project.
- **K24** Core 2 = `01_PROJECT_CORE/` — the part that learns.
- **K25** Ollama saves tokens only where it costs no quality; quality-critical work stays with Claude; mode cheap/expensive.
- **K26** A multi-file unit runs as a swarm only after the manifest; `swarm_probe.py --decide` checks the shape.
- **K27** Swarm acceptance at the end of the cycle, by script, then one reviewer pass and an atomic apply.
- **K28** The machine is measured (`machine.py`), never assumed; live load (`machine.py load`) caps a Codex wave, stops Ollama at critical, and is read by the Lead before a swarm.
- **K29** Claude leads and judges (request, DoD, plan, architecture, owner-facing text, Verify, close); executors are chosen by limits (K37) - Codex executes read-only and proposes full files (tier CODEX, `codex_exec.py`), the Lead reads the diff and applies; Codex reviews Claude's work; never leads, never touches git — `order.py` refuses inside Codex.
- **K30** Important orders (`--important`, implied by `--external`) get a ChatGPT audit on top of verifier2; the Lead triages every finding (`--rung3`); a disputed fact is settled by a primary source.
- **K31** Every second actor leaves an evidence file (`N_review_v2.md`, `N_review_gpt.json`, `N_codex.json`, `N_checks.json`); `close` reads them; a CHANGES review needs a re-review or a written overrule.
- **K32** Domain routing: an order names its domain(s) from `domains.json` and carries the card - one primary skill per stage, ≤2 supporting, quality bar, verify-by-running, forbidden; statuses from checks; figures with source and period, no ratings by default.
- **K33** Third-party skills: `skills_map.py vet` (static) → depth check → owner's yes → a role in `domains.json`; one skill over a library; licences per K16; never a remote install script.
- **K35** Code stack: Serena (large files) + Context7 (API docs) → plan with disjoint scopes → Codex waves in the background → `check_code.py` + tests → a Codex patch is applied only with test files when the task names tests and with its `check` green → cross-family review → the Lead commits; tools via `setup.py` (owner installs). Canon: `forge-code-structure`, `forge-code-testing` (also in Codex's prompt). Never green by weakening a check. Skill `forge-code`.
- **K36** Knobs by hook: `ss` = short read-only answer (risks stay), waiver words, "кодекс ревью", "решай сам", «стоп»; K15 guard on Codex calls; progress band (mod `forge-band`: also the compaction handoff and Claude's limit guard).
- **K37** Delegation by limits: `limits.py route --kind ...` before a task leaves the Lead - the side with more room in its plan's limits (Codex logs, Claude via the band or `set-claude`); ties by kind; critical side gets nothing while the other has room; exhausted waits; K8/K15/K29/K30 above the limits.
- **K38** A repeating lesson becomes a guard with the owner's yes (`guards.py`): shell commands checked before they run (block/warn), written files after.
- **K39** The harness is measured and pruned: `evals/` for `claude plugin eval` (Δ with vs without Forge), a case per new lesson class, the audit lists parts to delete.
- **K40** Context survives compaction: handoff before it (band), the brief brings back the request and `handoff.md` after it.
- **K34** Evidence discipline: the owner's request verbatim (else a hypothesis); expected result before a check runs + a negative control; evidence names commands, skill, numbers; undeclared changes made visible (scope snapshot); unsure executor answers `blocked` with a guess; releases replay checks on closed work and upgrade one project first.

Full text of a rule: `python <plugin>/tools/step.py rule K8` (`<plugin>` = the folder printed by the SessionStart brief).
The constitution itself: `<plugin>/CONSTITUTION.md` (read fully once, in the first session of a project).
Writing an order, a skill or a prompt → `forge-instruction-writing`; answering a person (chat, document, page) → `forge-human-explanation`; work without the owner → `forge-autonomous`.
Where Forge applies: routine with standard tools → Cowork plugins; one artifact → direct Claude (`forge-direct`);
complex recurring work with own data and rules → Forge orders (`forge-route`); code → `forge-code`; Codex/ChatGPT as reviewer, adviser, images → `forge-gpt`; which skill for which domain → `forge-route` §1 (`skills_map.py`).
