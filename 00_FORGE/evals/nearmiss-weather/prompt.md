---
description: >-
  Near-miss: an everyday question with nothing to do with Forge. No Forge skill may fire and Forge must not be
  pitched. Expected Δ about 0 - a negative Δ means the plugin made plain answers worse.
tags: [near-miss, smoke]
runs: 3
expected_outcome: >-
  No Forge skill fires. A short Russian answer: no live weather data in this session, where to check; no
  invented forecast, no Forge pitch.
max_turns: 3
timeout_seconds: 90
allowed_tools: [Read, Glob, Grep, Skill]
---

какая сегодня погода в Москве?
