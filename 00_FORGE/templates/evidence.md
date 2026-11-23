# <domain> — live run (order N…, YYYY-MM-DD; executor: …, checked by: …)

<!-- K34 evidence discipline (Forge 0.9.2). Copy to <state>/skill_tests/<domain>.md. Write EXPECTED before running anything:
an expectation written after the run bends to the result. skills_map.py audit marks a PASS without the Skill or EXPECTED
line as "incomplete". -->

Task: what was built, for whom, from which sources (one or two lines).
Skill: <plugin:skill> loaded | read from SKILL.md (why: e.g. installed during this session) | not used (why)
EXPECTED: the numbers and outcomes you expect, written before the run (e.g. "overflow 0 at 320/390/1024; check_html exit 0;
loudness -14 ± 1 LUFS"; for a recomputation: the value from the source).
Artifacts: `<folder>` — the files a reviewer opens.

## Card verify lines
| line | how (the literal command) | result (numbers) |
|---|---|---|
| … | `python <plugin>/gates/check_html.py page.html` | exit 0 |
| … | n/a — the reason | n/a |

Negative control: the same check run once on a known-bad input (or on the old code) — it must FAIL; otherwise a green result
proves nothing. Command and outcome here.

Commands to rebuild: the exact commands, in order.

Not proven here: what this run cannot show (real users, the owner's machine, a web path not built…).

RESULT: PASS | FAIL | PARTIAL (what passed, what did not)
