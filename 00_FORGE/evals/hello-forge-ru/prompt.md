---
description: >-
  The owner greets Forge and asks what it can do. forge-new must answer from templates/HELLO.ru.md in plain
  Russian and end with the three starter questions.
tags: [trigger, forge-new, behaviour, language, smoke]
runs: 3
expected_outcome: >-
  forge-new fires. The reply follows HELLO.ru.md - understand the task through questions, build a workspace,
  work in checked orders, automate routine, learn from lessons, never decide for the owner - and ends with the
  three questions (кто вы в этой задаче, что должно получиться, сколько времени).
max_turns: 6
timeout_seconds: 150
allowed_tools: [Read, Glob, Grep, Skill]
---

Привет, Forge! Что ты умеешь?
