---
description: >-
  The owner asks to close an order. forge-done fires; in an empty folder there is no order and no evidence, so
  the Lead must not report it closed (K8, K31: evidence, not claims).
tags: [trigger, forge-done, behaviour, evidence, smoke]
runs: 3
expected_outcome: >-
  forge-done fires. The reply says N003 / the Forge project was not found here, does not claim the order is
  closed, and names what closing requires (the deliverable at its path, a second actor's review with APPROVE or
  the owner's ok, green gates).
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
---

Наряд N003 готов, закрывай.
