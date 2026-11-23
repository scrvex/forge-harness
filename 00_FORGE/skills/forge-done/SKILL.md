---
name: forge-done
description: Use when an order's deliverable is ready to close («закрой наряд», «готово», «принимай»), or before ending a session that still has open Forge work.
---
# Closing an order

`<plugin>` = the Forge plugin folder (printed by the SessionStart brief). Tell the owner "готово" only after step 4 printed
the close: until then the work is a claim, and every claim needs its evidence (forge-review, "What counts as evidence").

1. The deliverable file exists at the path named in the order (`## Deliverable`), in the user's language, no invented
   numbers, assumptions marked. The result answers the order's verbatim Request (K34a), not only the Lead's Goal. For Ollama drafts read the Verifier 1 summary (`<circle>/answers/N_review.json`).
2. Two-actor rule with evidence (K8, K31):
   - Codex-made (tier CODEX) → the Lead judged each `answers/N_<task>.patch` before `apply`, then `answers/N_checks.json`;
     important → `verifier2` too. Waiver `review` (owner's words in the order) → no second-actor evidence needed.
   - Claude-made, ordinary order → a Codex review `answers/N_review_gpt.json` APPROVE (`verify_gpt.py N`) counts as the second actor.
   - Claude-made (Lead / FAST / HEAVY) → `verifier2` output saved as `answers/N_review_v2.md` (`VERDICT: APPROVE` as the first non-empty line; a verdict word elsewhere in the file does not count) — the only
     option for important orders — or an Ollama `answers/N_review.json` with APPROVE (cheap mode, ordinary orders), or the
     owner's ok (`--by owner --owner-text "<the owner's words>"`, K8). Only this order's files count and they must be newer than the deliverable (re-run after edits).
   - Ollama-made → the Lead reviews (`--by lead:claude`). ChatGPT-made image (tier GPT) → Claude reviews it (opens it).
   - A review that says CHANGES is fixed and re-run, or overruled in writing: `--overrule "why"` (lesson).
   - ChatGPT never writes the closing report (K29).
3. Important order (`**Rung 3:** required`) or any `answers/N_review_gpt.json`: triage every ChatGPT finding (skill `forge-gpt`)
   and pass `--rung3 "accepted …, rejected … (why), checked … at <source>"`; if the audit could not run, `--rung3-skip "why"`.
4. One command:
   `python <plugin>/tools/order.py done N001 --text "<verdict in the user's language>" [--executor lead:claude --summary "..." --files a.md,b.md] [--verified-by verifier2] [--rung3 "..."] --commit`
   Add `--not-done "what was deliberately left out (why); ..."` (K34) and list every changed file in `--files`, literally.
   It writes the answer (if missing), the report, reads the evidence, checks known failure classes (gate_lessons), runs the
   gates, closes the order and commits **only the order's files** (records + `files_changed` + their PDFs). Anything else
   uncommitted is listed, never swept in. Red gates → nothing closes; fix and repeat. A "K34 scope" line lists files changed
   since `take` that you did not declare: yours → re-run with them in `--files`; another session's → leave them.
   With `--commit`, files already staged outside the order's scope are refused by name (K14); unstage them, or name a path in
   `--allow-extra "path,..."` to leave it staged out of this commit knowingly (it is not committed; journaled).
   Before `done`, read `python <plugin>/tools/weaken_scan.py --order N001` (K11, report only, exit 0): with the answer written
   it scans the answer's `files_changed`, before `order.py answer` it scans the whole working tree and prints a note saying so.
   Each signal is a question: confirm that a check was weakened and fix it, or say in the report why it was not.
   An answer with `--status blocked` cannot close: answer the executor's question first (amend, reopen, take).
   Tokens (R-37): `close` reads the Claude Code transcripts of the sessions that worked on the order (your own requests in its
   window + every agent it spawned or that names it) and prints `N spend X units (Lead …, agents …) vs estimate Y = xR`
   (`answers/N_usage.json`; dry run: `usage.py scan N`). Name the order id in agent prompts. Only without transcripts (another
   client): `order.py usage N --who <agent> --usage "<usage block>"`.
   Your own share is measured from your transcript; a typed estimate (`order.py usage N --who lead --tokens 250k`,
   `step.py end --tokens`) is only the fallback and is superseded once the transcript is read.
5. Verdict CHANGES/REJECT: `--verdict CHANGES` records it plus an auto-lesson; re-execute, then `done` again. A review that
   said CHANGES is re-checked by the same verifier only within 5 minutes of its last activity, else by a fresh one (forge-review).
6. Documents that go to third parties (`--external` orders) wait in AWAITING_OWNER until the owner says "ok":
   `order.py report N --verdict APPROVE --by owner --owner-text "<the owner's words>" --text "ok"` then `order.py close N` (R21).
7. User-facing documents: refresh the PDF (`python <plugin>/tools/md2pdf.py file.md`) before closing — one file per call.
8. The owner corrects a closed order → the fixing order is opened with `order.py new ... --fixes N001` (lesson written).
