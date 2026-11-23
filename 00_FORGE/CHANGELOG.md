# Forge changelog

The plugin's version history, newest first. The versioned files are primary: `.claude-plugin/plugin.json` and `kernel.json` carry the current version and its short line; this file carries the reasons. The rules themselves live in `CONSTITUTION.md`; change logs live here, not there.

## 1.2.0 (2026-10-17)

Targeted harness updates that bring in principles of the ambatupipe pipeline (the owner asked for targeted updates on ambatupipe's principles; the verbatim request is in order N031). Each mechanism is a report or a refusal with a named cause; no rule is renumbered.

- Resume: new `tools/reconcile.py [Nnnn]` (read-only, offline) compares STATE.md, the orders index, the order file, `answers/Nnnn_plan.json`, `Nnnn_codex.json` and `Nnnn.json` `files_changed` with git facts (the tree against the take snapshot `Nnnn_scope.json`, commits since take); one line per mismatch, exit 0 consistent, 1 mismatches. `step.py --brief` adds a `reconcile:` line only when mismatches exist. The file-level claims are skipped when the take snapshot is absent or skipped (K9).
- Order gates (`order.py`): a text review (verifier2) counts only when its first non-empty line is `VERDICT: APPROVE|CHANGES|REJECT`, and only when it is newer than the newest declared file; `--by owner` needs `--owner-text` (kept verbatim in the report as `OWNER-TEXT` and in the journal; `close` refuses an owner report without it); `done --commit` refuses staged files outside the order's scope; `--allow-extra "a,b"` names staged paths that are left out of the commit knowingly - the commit is by pathspec, so they are not committed, they stay staged - and journals the exception (K8, K14, K31). The refusal hints that ask for the owner's confirmation name `--by owner --owner-text "<the owner's words>"`. Breaking for scripts and tests: `--by owner` without `--owner-text` is refused.
- Codex patch acceptance (`codex_exec.py apply`): a task with `tests` in the plan (or tests named in the order's DoD, without the owner's `tests:` waiver) needs test files in the patch; a task with `check` / `checks` (a command or a list of commands, run through the platform shell with PYTHONUTF8=1, each killed with its process tree after `FORGE_CHECK_TIMEOUT` seconds, default 900) runs them on a temporary copy of the project with the patch applied, and the working tree is written only after both pass; when tests are required and the task declares no check, forge.json `code.test` runs instead, and without either the apply is refused; `wave` refuses a plan whose `tests` its scope cannot hold, before Codex starts; `--skip-checks "reason"` bypasses both and journals the reason (K35g).
- `tools/weaken_scan.py [--base REF] [--order Nnnn | --files a,b] [--json]`: report-only detector of weakened checks in the git diff (removed or emptied tests, skip/xfail/only markers, trivial or loosened assertions, new suppressions, stub bodies, lowered thresholds, loosened check configs); exit code always 0; the Lead reads it at done. Not a gate until the owner has measured its false positives (K11, K35c).
- `gates/gate_docs.py` (7th gate, K11): plugin.json version = kernel.json `kernel_version` = README / TOOLS / forge-rules headers, the last `kernel.json` changelog entry = current version, `K1-Kn` and the domain count in the README, the README tree lists of skills, agents and gates against the disk, a TOOLS.md row for every gate and tool; a no-op for an installed plugin outside the project tree. `tests/test_120_corpus.py` checks skill and agent frontmatter, `forge:` references, markdown links, paths in backticks and K ids. Maintenance rule: a new tool or gate gets its TOOLS.md row, a new skill or agent its README tree entry, in the same change.
- Hook fixes: `check_file.py --hook` is silent when no `forge.json` is found above the file (the project is searched from the file, not from the cwd); a write guard with `action: warn` no longer blocks (exit 0, the message goes to the model as PostToolUse `additionalContext`), `action: block` still exits 2 (K38).
- Documents: this file replaces the `Change log` blocks that stood inside CONSTITUTION.md (text unchanged; 0.7.0, 0.7.1, 0.9.3, 1.1.1 and 1.1.2 added from `kernel.json`); rules K9, K11, K14, K17, K28, K31, K35 and K38 extended (K35 gets a new item (g) after the installer's (f); no rule is renumbered); skills forge-done and forge-direct name `--owner-text`, `--allow-extra` and weaken_scan; DECISIONS.md carries a numbered registry of decisions since 2026-09-18.
- Live machine load (K28): `machine.py load [--json]` takes a snapshot now (CPU load per core, memory, thermal throttling, GPU, battery; macOS, Linux and Windows probes, each under 3 s, no sudo) and gives a verdict ok / busy / hot / critical with the recommended slots; a machine with a battery is a laptop without active cooling and is capped at 2 heavy actors. `codex_exec.py wave` uses min(slots, load slots) and refuses at 0; Ollama runs (local_exec, local_jobs) refuse when critical; the brief shows `machine: <verdict> (<reasons>)` only when not ok. `codex_exec.py wave` passes its requested slots to the reading (so the profile is not replaced by a constant) and, if the reading raises, keeps the requested slots and prints one line that the live check failed. The swarm scripts and `/forge:swarm-run` do not read the live load (the cached profile and the `slots` argument only): before a swarm or parallel Claude sub-agents the Lead runs `machine.py load` and starts at most the recommended slots (K28).
- Tests: `test_120_machine_load` (38), `test_120_reconcile` (35), `test_120_order_gates` (28), `test_120_codex_apply` (43), `test_120_weaken_scan` (77), `test_120_hooks` (4), `test_120_gate_docs` (22), `test_120_corpus` (18).
- Not taken from ambatupipe: its global CLAUDE.md canon, memory-sync, a blocking Stop hook outside an autonomous run (decisions in 1.0.0 and 0.9.2 stand).

## 1.1.2 (2026-10-15)

the owner's «не ломай кодекс во время работы» after the Codex desktop app lockout of 2026-10-15 (AppModel-Runtime 0x80070005: %LOCALAPPDATA%\Packages had lost SYSTEM and Administrators; suspect, not proven: Codex's elevated Windows sandbox, which Forge requests) (N028): new tools/codex_safety.py reads the folder's DACL as SDDL and compares SIDs (never localized names), and never changes an ACL; every codex_exec run/wave/fix and every gpt_bridge.run is bracketed - refused while SYSTEM or Administrators are missing, with the owner's icacls fix in Russian; ok before + broken after = the rights vanished during that run (a coincidence in time, stated as such, not as a proof) - a loud warning naming the run, an incident file (the run, the Codex binary, both SDDLs, overlapping Forge runs, tails of ~/.codex/.sandbox logs), a kernel lesson (confidence medium) in the project and a block until the owner fixes it (lifted automatically once fixed); every reading with its process id in <forge home>/codex_acl.jsonl; the guard's own lesson is kept out of the K34d write checks (codex_exec, verify_gpt); gpt_bridge re-checks in a finally; a reader error never blocks (fail-open); setup.py doctor line `packages acl` (the fix printed for the owner, never an install step); tests/test_112.py, test_kernel isolates FORGE_HOME with a healthy ACL stand-in, fake_codex FAKE_CODEX_ACL. Scratch measurement on the read-only audit path: unelevated works (shell reads, no ACL change), no override blocks every shell command. Then the owner said «делай» (N029): gpt_bridge read-only runs (audit, advice, fact check; an audit (N028) and a fact check with web search (N029) were measured, advice was not run separately - same read path) use windows.sandbox=unelevated; writing runs (gpt_media images) and codex_exec keep elevated (not measured); env FORGE_GPT_WINDOWS_SANDBOX = elevated | unelevated | none overrides (rollback; other values are ignored with a note); setup.py doctor says which mode runs where.

## 1.1.1 (2026-10-15)

1.1.0 from the GitHub branch claude/peaceful-rubin-vxsu4l plus Windows fixes found by its tests on the owner's machine (N027): the hooks read Claude Code's JSON in the Windows locale (cp1251), so Russian knobs never fired and check_file skipped every file with a Cyrillic path in projects without PYTHONIOENCODING - prompt_knobs, check_file and gpt_guard now decode stdin as UTF-8 (step.py and statusline too, for alignment: they read only ASCII fields); prompt_knobs printed the «Запустить» knob in cp1251, and gpt_guard, once it reads real Cyrillic paths, prints folder names that would go out in cp1251 (a name outside cp1251 would raise and drop the K15 deny) - its stdout and stderr are now UTF-8 (check_file already got UTF-8 output from forge_common); the second skill scanner kept the quotes of a quoted Windows path (skills_map.scanner_argv drops one matching pair, Windows only); test_110 wrote CRLF on Windows (LF now) and runs its outside-a-project negative control from a copy of the plugin, since find_root also looks above the plugin's own files; test_110.WindowsIO sends raw UTF-8 with the encoding variables unset (fails on 1.1.0).

## 1.1.0 (2026-10-13)

the owner's yes to the review of 2026-10-13 (research of the Claude Code ecosystem, harness
engineering and ambatupipe v8.11–v8.14). Fixed: forge-code frontmatter was invalid YAML (the skill loaded without its
description); SessionStart/Stop hooks failed outside a Forge project; Codex runs that were cut off stayed `running`; a code
check that checked nothing was green; the next step after a failed Codex task ignored K37; the band hid other mods' rows; two swarm tests needed the
dev workspace. New: K38–K40; K20 autonomous run (`autorun.py`, skill forge-autonomous); K21 progress defined; K19
clarification markers; K35 canon skills (forge-code-structure, forge-code-testing), test-integrity ban, background waves,
project rules in Codex's prompt, an answer with decisions / beyond the task / irreversible / memory; skills
forge-instruction-writing and forge-human-explanation; reviewers' evidence table and restate-then-check triage (superpowers,
Trail of Bits); no numeric output quotas for agents (they acted as filters); skill descriptions say when, not how; the Forge
status line; doctor checks for settings that switch hooks off; an optional second skill scanner (SARIF); silent green gates;
CI. Not taken: a blocking Stop outside an autonomous run (0.9.2 stands); a global CLAUDE.md canon; backlog renumbering;
Codex writing into the project; pinning `researcher` to a smaller model (owner's rule N022: no silent downgrade).

## 1.0.1 (2026-10-12)

the owner's correction of 1.0.0 — no default executor; new K37 and `tools/limits.py`; K3, K5,
K29 reworded; codex_exec limit preflight; brief line and band show both limits; the band records Claude's limits.

## 1.0.0 (2026-10-11)

owner's request — compile the agent-playbook/ambatupipe approach with Forge, delegate more to
Codex, add Serena, Context7 and the progress mod. Taken from ambatupipe: the default lane (K6), executor waves with file
scopes (K35b; in patch mode - Codex never writes to the project, the owner's choice 2026-10-12), investigator/executor agents with model and effort in the frontmatter, Serena + Context7 (K35a), deterministic
self-check (K35c), waivers recorded in the order (K19), conversation knobs (K36), git only by the Lead (K35e), an installer
with a doctor (K35f), the progress band. Changed: K29 (Codex an executor - since 1.0.1 by limits, K37 - and a counted reviewer for ordinary
orders), K3 (tier CODEX, agents), K4, K5, K31. Kept from Forge: orders and evidence, the two-actor rule by family, Ollama
cheap mode, lessons and audits, the skills map, K15 data policy (now also enforced by a PreToolUse guard). Not taken: a global
CLAUDE.md canon (decision B: Forge stays per project), the private memory-sync plugin, Codex's Stop-hook review gate
(drains the ChatGPT limit).

## 0.9.3 (2026-10-11)

R-37 Claude tokens per order (cycle audit 2026-10-05: metrics removed in 0.6.0, estimates off ~3x): tools/usage.py - ledger answers/N_usage.json; hook on finished Agent calls (PostToolUse exact totals for foreground agents, SubagentStop transcript for background ones, marked approx) books tokens to the first open order named in the prompt (id or file name), else the session's last named order, else the current order if it is the one TAKEN and taken < 24 h ago, else an unassigned log; per-file locks for parallel agents; stdin read as UTF-8 bytes; order.py usage / answer / done --usage block or --tokens (deduplicated with the hook), new/amend --estimate (or a swarm manifest's estimate_tokens), close prints and journals total vs estimate; step.py end --tokens books the Lead's estimate only when one order can own it; brief tokens line (the 16-line cut keeps next:); usage.py report for forge-audit; skills forge-done/swarm/review/audit, K10; N020: the meter's source is the Claude Code transcripts (tools/transcripts.py) - close scans the sessions that worked on the order: the Lead's requests in its window (order.py commands) and its agents (named / spawned / window), per-request usage with the last row per message id, spend in input-token units by model; hook, hand and estimated runs they cover are superseded; usage.py scan; tests never read the real Claude home; N021: verifier re-check rule - resume the same verifier only within 5 minutes of its last activity (warm cache), else a fresh verifier2 with the previous findings, the fix list and paths (191 runs measured: 36 cold resumes, 4.7M units; global 1 h TTL +15%, not set); N022: agent forge:researcher (explicit tools Read/Grep/Glob/WebSearch/WebFetch/Write, no model line - inherits, no silent downgrade, one named output file, <=300-token reply); forge-swarm/forge-route/forge-review use Forge agents and never general-purpose (~73k vs ~10k prompt start, 72 runs ~16.5M units of prefix); N023: skills_map.py profile <project> - domains from forge.json (else detect over CONTEXT.md and PLAN.md), plugin sets their cards use (installed skills only) vs the rest, ledger <state>/skill_profile.json with reasons and the restore step, the .claude/settings.local.json enabledPlugins snippet printed (name@marketplace from the CLI registry, else name@synced), --apply-file to a non-settings path only, --restore; never writes settings; desktop-delivered sets may ignore project settings - one-line /context test

## 0.9.2 (2026-10-08)

new K34 (section M) from the owner's review of a design-ops pipeline deck and the N012 live
runs: verbatim request + 5-line decision + "deliberately not done" (templates order/report); `order.py` scope snapshot at take
and undeclared-change list at close, L010 refusal of missing paths, `answer --status blocked`; `templates/evidence.md` and
`skills_map.py audit` evidence gaps; Stop hook runbook as a JSON systemMessage shown to the owner once per situation (plain Stop stdout reaches only the debug log -
the 0.6-0.9.1 reminder was never seen; never blocks: Stop fires after every turn); tools `upgrade.py` (R-28)
and `replay.py`; project template sections Swarm, Skills map, Evidence discipline (R-46); `check_figures` negation adjacent to
the term (table cells are clauses), `check_html --seo`, language check on .md documents (+ a declared .txt deliverable).
Not taken from the deck: three judgement lenses per order (1.5-2x tokens), a blocking Stop hook, Figma-specific scripts.

## 0.9.1 (2026-10-08)

first K33 adoption after the owner's yes - frontend-design, gsap-skills, InvestSkill fact-check
and 10k-digest installed as external dependencies with their licences, each with one role in `domains.json` and provenance in
`third_party.installed`; InvestSkill's BUY/HOLD/SELL block is suppressed by a conflict rule (the card wins, K32).

## 0.9.0 (2026-10-08)

new K32–K33 and `domains.json` (11 domains, 5 combos); tools `skills_map.py`
(detect / show / audit / vet) and `gates/check_figures.py`; `order.py new --domain`, `forge.json` `domains` from the plan;
order template gains the domain header, the "Verify by running" section and domain forbidden lines; skills forge-route
(rebuilt around the map), forge-direct, forge-new, forge-audit (§9 skills inventory), forge-rules. Source: the owner's
"skills ecosystem" prompt — taken: domain routing, one role per skill, per-domain quality bars, tool split (Blender / Three.js /
GSAP / Remotion / FFmpeg), conflict rules, vetting, honest statuses, investment source discipline; not taken: mass install
of external libraries (K16, decision B, context cost) — candidates wait for the owner's yes.

## 0.8.0 (2026-10-05)

K3 tier GPT (images only); K4 rung 3 = ChatGPT, advisory, on top of verifier2; K8 by model
family; K12 lessons for overrules and owner fixes; K14 commit only the order's files; K15 forge.json `gpt` + deny list;
new K29–K31; tools gpt_bridge / verify_gpt / gpt_media / gpt_setup; skill forge-gpt; template AGENTS.project.md;
hold/block need a reason; md2pdf never writes into a non-PDF; `step.py end --help` no longer closes the session.

## 0.7.1 (2026-09-20)

Swarm as the structure's logic (owner): swarm_probe.decide() - the K26 optimality check (breakeven ~3 separable files, machine-slot caps, bulk->map-reduce, research only after owner yes); order.py new prints the decision hint for HEAVY/LEAD units; forge-route step 0 = single-or-swarm for every unit; K26 wording; verify.py suffix-only routing fix (multi-file text deliverables reviewed again); tests for decide().

## 0.7.0 (2026-09-20)

Swarm + machine auto-tune (ank-launch spec + R-17/18/20/22/23/24): machine.py probes the PC per session and tunes models/slots/threads (K28, brief line); swarm_probe/swarm_accept (manifest -> staging -> scripted end-of-cycle acceptance -> atomic apply, K26-K27); skill forge-swarm; bench_threads (GPU/CPU evidence); verify.py routes by deliverable type (code/multi-file -> SKIP); order.py amend with journal; RU-language gate skips Core-2 files; init_project pre-excludes publish/build/dist/bin/node_modules; order template allows en for code/websites; templates/swarm.plan.example.json; tests/test_swarm.py. TODO 0.7.1: order.py --manifest integration, Stop-hook acceptance.

## 0.6.0 and earlier (2026-09-17 .. 2026-09-18)

2026-09-18 v0.6.0 — decision B: Forge collapsed into a Claude Code / Cowork plugin (skills + hooks/gates + 11 scripts); manifest, registry gate, metrics, bench, queue, retro, verify2, release, relayout, dashboard and drawing scripts removed (archived in FORGE_0.3 and git history); K5/K10/K11/K13/K17/K18/K23 reworded; `init_project.py` replaces instantiate + forge new; `step.py mode|end` replace mode.py/session_end.py.
2026-09-18 v0.5.0 — CP2+CP3: metrics.jsonl per order and bench.py (fitness, keep/rollback, release gate); parallel LOCAL orders + queue.py (night queue); gate_lessons (failure registry checked at close); R24 second actor for Lead artifacts; R21 external documents wait for the owner; verify2.py (other model family, key-gated); retro.py (lessons → proposals); pdf2text, serve, check_html; local_exec --by-section; md2pdf widths/emoji/code; small 4B model for routing; packs/_template; plan.example.json + forge.py schema.
2026-09-18 v0.4.3 — K25 routing: Ollama = inter-agent tool, quality-critical work stays with Claude; direct path for one-shot creative/code deliverables; quality bar in every order.
2026-09-18 v0.4.1 — CP1 (R13 tier from plan, R14 sources, R15 verify_facts/review_lang, R23 project.json + dashboard, R25 `order.py done`, R31 check_file hook); K8/K20/K25 wording per owner answers 11–27.
2026-09-17 v0.4 — three levels K23–K25 (immutable Core 1 + manifest gate, learning Core 2, modes).
2026-09-17 v0.2.1 — K22 context hunger; report template section.
2026-09-17 v0.2 — K7 context/deliverable/language, K8 same-actor rule, K12 auto-lessons (trial audit R1–R6).
2026-09-17 v0.1 — first version, ported and shortened from the owner's Circle v1 rules (K0–K16),
renumbered, two-actor rule and token ladder made explicit, third-party rule per owner.
