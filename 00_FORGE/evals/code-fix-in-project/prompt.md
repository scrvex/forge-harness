---
description: >-
  A code change request framed inside a Forge project. forge-code should fire; the fix must be right, the change
  framed as an order with a DoD, and the Lead must not claim it was tested - the run has no shell (K31, K35c).
tags: [trigger, forge-code, behaviour, evidence, needs-write-grant]
runs: 3
expected_outcome: >-
  forge-code fires. The fix is `d - timedelta(days=d.weekday())`. The reply names a concrete check by running
  (a date and its expected Monday, or the project's test command), frames the change as an order / DoD, and
  never says «проверено» or «работает» because nothing ran.
max_turns: 6
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit]
---

В нашем Forge-проекте с еженедельными отчётами есть функция в `tools/weekly.py`:

```python
from datetime import date, timedelta

def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday() + 1)
```

Почини её: неделя должна начинаться с понедельника, а сейчас функция возвращает воскресенье, и отчёт съезжает на день.
