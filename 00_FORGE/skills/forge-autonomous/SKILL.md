---
name: forge-autonomous
description: Use when the owner hands the work over for a while («работай без меня», «решай сам», «как считаешь нужным», «я ушёл, продолжай»), when an autonomous run is on in the brief, or when the owner says «стоп» during one.
---
# Autonomous run (K20, Forge 1.1.0)

The owner's "decide yourself" is a mode of the project, recorded and enforced, not a mood of one reply. While it runs,
the Stop hook keeps you working as long as an order in scope is OPEN or TAKEN; questions to the owner wait in a list
instead of stopping the work. `<plugin>` = the folder printed by the brief.

## 1. Start - one question, one approval (K19)
- Collect what the run covers: the orders in scope (`step.py`), each with its DoD. An order without a DoD the owner
  approved is not in scope - the run cannot approve DoDs for the owner.
- Ask ONE AskUserQuestion: the question names the run, the preview holds the orders with their DoD lines and what you
  will not do; options «Запустить» and «Не сейчас». Long text goes in the preview, short lines.
- Only after «Запустить»: `python <plugin>/tools/autorun.py start --words "<the owner's words>" --orders N004,N005`.
- Optional, for the owner to paste (a native completion check over the whole run): `/goal every order in
  "autorun.py status" is CLOSED or HOLD, or stop after 40 turns`. You cannot set it yourself; offer it in one line.

## 2. While it runs - what replaces the owner's answers
| Moment that would stop for the owner | In the run |
|---|---|
| a question whose answer changes the result | `autorun.py ask "<question>" --guess "<your best guess>"`, go on with the guess, mark it "(assumption)" in the deliverable |
| two readings of a DoD line | the narrower reading that keeps the owner's words; record it as a question |
| a review finding you disagree with | triage it in writing (accept, or reject with the reason) - the owner reads the report |
| a review finding that is blocking and you agree | fix it in this order |
| a non-blocking or pre-existing finding | leave it, record it in the report for the owner |
| a bug seen in passing | `order.py new` for it (OPEN, not in scope), never fix it inside the current order |
| Codex or an agent failed | the limits decide the next step (`codex_exec.py` prints it); the second failure is HOLD |

## 3. Never during a run
Money, deletion of owner files, sending anything outside, strategy (K1) - stop and ask, the run waits · push, merge or
work on the main branch of a code project · changing lint, type or test configs, skipping or weakening tests to get
green · starting work outside the orders in scope · installing software (K35f) · closing an order the K8/K31 evidence
does not cover (the run makes no exceptions to the circle).

## 4. When it stops
- A real blocker: `order.py hold N --why "..."` (the run goes on with the next order) or, when nothing else can move,
  `autorun.py stop --why "..."`.
- K21: two Verify rounds without progress -> HOLD + lesson. Progress means: no new failing check appeared and at least
  one earlier failure went away (count from `answers/N_checks.json` and the reviews on disk, not from memory).
- The owner's «стоп» -> `autorun.py stop --why "owner: стоп"`, finish the tool step in hand, leave the work resumable.
- Nothing left in scope -> the Stop hook marks the run done and tells the owner.

## 5. The report when the owner is back
Closed, held (with why), open questions with your guesses (`autorun.py status`), every assumption the work rests on,
what you deliberately did not do. The owner answers a question with `autorun.py answer <n> "<answer>"` - then re-check
the work that used the guess.

Ideas: ambatupipe autonomous-run and the continuation mod (Apache-2.0), rewritten (K16).
