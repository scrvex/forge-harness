---
description: >-
  Near-miss for forge-code: a question about code is answered directly (forge-code lane, K6) - no skill, no
  order, no DoD. English prompt.
tags: [near-miss, forge-code, language]
runs: 3
expected_outcome: >-
  No Forge skill fires. A short English answer: weekday() Monday=0..Sunday=6, isoweekday() Monday=1..Sunday=7.
  Expected Δ about 0.
max_turns: 3
timeout_seconds: 90
allowed_tools: [Read, Glob, Grep, Skill]
---

Quick one: in Python, what's the difference between date.weekday() and date.isoweekday()?
