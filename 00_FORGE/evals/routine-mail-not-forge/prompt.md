---
description: >-
  Routine mail work that standard tools already cover. Forge must say so (forge-new step 0, forge-route decision B)
  and point to a Cowork plugin or connector instead of starting a Forge project.
tags: [routing, forge-new, behaviour]
runs: 3
expected_outcome: >-
  The reply recommends a ready route - a Cowork plugin or the Gmail connector with a scheduled task, or Gmail's
  own tools - and does not start a Forge intake or a project.
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
---

Хочу, чтобы каждое утро мне приходила сводка непрочитанных писем из Gmail: кто написал и что срочное.
