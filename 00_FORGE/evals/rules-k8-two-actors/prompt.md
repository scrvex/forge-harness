---
description: >-
  A rule question in the owner's shorthand. forge-rules (or forge-done) should fire, and K8 - the two-actor rule -
  plus the K31 closing evidence must be stated correctly. Without the plugin this is unknowable.
tags: [trigger, forge-rules, behaviour, smoke]
runs: 3
expected_outcome: >-
  forge-rules fires. K8: whoever produced an artifact does not approve it; one model family is one actor. A close
  needs evidence from a second actor in answers/ (verifier2 APPROVE; a Codex or Ollama review for ordinary
  orders; or the owner's ok) and green gates.
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
---

Напомни, что требует правило K8 и что нужно, чтобы наряд вообще можно было закрыть?
