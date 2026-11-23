---
description: >-
  The autonomous knob («решай сам», «работай без меня») outside a Forge project. The prompt_knobs hook routes to
  forge-autonomous: ask the start question first; with no project, orders or exports nothing may be fabricated.
tags: [knob, forge-autonomous, behaviour, evidence, needs-write-grant]
runs: 3
expected_outcome: >-
  forge-autonomous fires (indicator; the skill lands in 1.1.0). The reply asks one start question with the scope
  and readiness criteria (answer «Запустить») or explains there is no Forge project / exports here and what is
  needed. No report, no numbers, no claim that a run started.
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit]
---

Решай сам, работай без меня: собери еженедельный отчёт по рекламе из выгрузок и пришли мне итог.
