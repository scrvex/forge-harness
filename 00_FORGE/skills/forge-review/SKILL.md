---
name: forge-review
description: Use before closing any Forge order, when the user asks to check a result («проверь», «всё ли верно»), or when a document goes to the user or to third parties.
---
# Verifier ladder (K4, K8, K29–K31)

`<plugin>` = the Forge plugin folder (printed by the SessionStart brief). Every rung that judges leaves an **evidence
file** in `<circle>/answers/` — `order.py close` reads the verdicts in them (K31), a text claim is not enough.

**What counts as evidence (1.1.0, from superpowers' verification-before-completion)** - each claim has its own proof:
"done" → the file or the diff, never the executor's summary · "tests pass" → the command and its output (`N_checks.json`)
· "requirements met" → every DoD line checked against the artifact, not a green test run · "fixed" → the failing case run
again · "works" → the artifact run on a real input. "Should", "probably", "seems" in an answer are claims without proof.
For code, a skipped or weakened test, a widened tolerance or an edited lint/type/test config is a finding, not a fix.

**Triage of findings (1.1.0, from Trail of Bits fp-check and second-opinion)**: restate each finding in one line and check
it against the cited lines before acting - reviewers, Claude and Codex alike, see defects that are not there and overrate
severity. Agreement of two reviewers is not proof; one primary source settles a fact (K30). A review run that exited with
an error or returned invalid JSON is an incomplete review, never a clean one. Give both families the same diff and paths.

0. Gates, 0 tokens: `python <plugin>/gates/run_gates.py` — must be green. HTML deliverables: `python <plugin>/gates/check_html.py file.html`.
1. Ollama, advisory (cheap mode only): `python <plugin>/tools/verify.py N001` → `N001_review.json`; `verify_facts.py doc.md --sources a,b`.
   Read the summaries, not the drafts, for internal orders. Ollama never judges Ollama's work (K8).
2. **Claude reviews Claude** — mandatory for important work (for an ordinary order an Ollama APPROVE in cheap mode or a
   Codex APPROVE from step 3 is enough, K31 1.0; Codex-made work is judged by the Lead, and by `verifier2` too when important): launch the sub-agent `verifier2` (read-only; never a `general-purpose` agent as the reviewer - ~73k vs ~10k tokens of prompt at start, forge-route §2)
   with the order id (it also books the reviewer's tokens to the order, R-37) and the deliverable path; **save its whole output** to `<circle>/answers/N001_review_v2.md` (first line
   `VERDICT: APPROVE|CHANGES|REJECT`), then `order.py report N001 ... --verified-by verifier2`. Only this order's review files count, and they must be newer than the deliverable. CHANGES → fix and run verifier2
   again (≤ 2 rounds, K21), or record why it is wrong: `--overrule "reason"` (a lesson is written).
   **Re-check after fixes (0.9.3):** resume the same verifier (SendMessage to its agent id) only if its last activity - the
   time its last result reached you - was under 5 minutes ago: its prompt cache is still warm. Otherwise spawn a fresh `verifier2` with a compact brief: the
   previous review file (its findings), the fix list (an evidence section or amendment), the paths. A late resume rewrites
   the whole review context into a cold cache (measured 2026-10-11 over 191 subagent runs: 36 such resumes, 4.7M units;
   a global 1 h cache TTL would cost +15% overall, so it is not set).
   Code deliverables: run `/code-review` first, then `verifier2` if the artifact is user-facing.
   **Brief the reviewer with paths only** (order, deliverable, sources): no account of why the work is right — a reviewer
   told the intent starts agreeing (K34). An empty findings list is a valid verdict.
3. **Codex/ChatGPT — the other family** (skill `forge-gpt`): `python <plugin>/tools/verify_gpt.py N001` → `N001_review_gpt.json`.
   Ordinary Claude-made order: its APPROVE may replace rung 2 (1.0). Important order (`--important`, implied by `--external`):
   mandatory *in addition to* rung 2, never instead. Never on Codex-made work (K8). Either way it is advisory: triage every finding (accept / reject with a reason / check against a primary source) and record it with
   `--rung3 "..."`. Cannot run (project not allowed, limit, Codex down) → `--rung3-skip "reason"`.
4. Interactive artifacts (pages, games, scripts): verify by running them (`forge-direct` step 4) — a review that did not
   open the artifact is not a review.
5. **Every new check is itself checked (K34b)**: it has an expected result written before it runs and a negative control —
   a known-bad input, or the old code, on which it must FAIL (N012: a negation exemption passed "price target 250 (not
   advice)"; a self-check read the hidden attribute instead of the rendering). Evidence of live runs follows
   `<plugin>/templates/evidence.md` (Skill and EXPECTED lines).
6. Owner: money, deletion, external sending, documents to third parties (`--external` → AWAITING_OWNER) — wait for "ok".

Why several actors: evaluators favour their own output (cycle 1: Ollama 9.9/10 vs Lead 3.2/5), and one model family shares
blind spots (urist: ~60 Claude actors, false claims still reached the owner). Why every finding is still triaged: pilot
N008 — ChatGPT found 4 defects all Claude reviewers missed and was confidently wrong once on a legal point.
