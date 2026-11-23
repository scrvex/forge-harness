---
description: >-
  A marketer describes recurring work on their own data (weekly ad report from CSV exports).
  forge-new must fire, and the Lead must interview and play the task back before building anything.
tags: [trigger, forge-new, behaviour, interview, smoke, needs-write-grant]
runs: 3
expected_outcome: >-
  forge-new fires. The reply is in Russian and asks the brief questions (role, what the report must show and
  for whom, time per week, which exports and where, red lines) or plays the task back and waits for «да, собирай».
  No script, report, folder or forge.json is produced; no invented figures.
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit]
---

Я маркетолог в небольшом интернет-магазине. Хочу каждую неделю собирать отчёт по рекламе из своих выгрузок — Яндекс Директ и VK Реклама, обычно csv. Сейчас трачу на это полдня каждый понедельник.
