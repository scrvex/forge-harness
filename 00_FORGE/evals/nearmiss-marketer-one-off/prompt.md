---
description: >-
  Near-miss for forge-new: starts like its trigger («Я маркетолог…») but is a one-off question. Answer directly;
  no intake, no workspace. Expected Δ about 0.
tags: [near-miss, forge-new]
runs: 3
expected_outcome: >-
  No Forge skill fires. ROMI = (revenue or gross profit from marketing − marketing costs) / marketing costs ×
  100%, with one arithmetically correct example, in Russian.
max_turns: 3
timeout_seconds: 90
allowed_tools: [Read, Glob, Grep, Skill]
---

Я маркетолог. Как считается ROMI? Дай формулу и один пример с цифрами.
